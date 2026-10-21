# FinSim Live Terminal — authoritative design (synthesis)

Author: Claude (Fable 5.1), 2026-10-20. Status: **AUTHORITATIVE** for the build. Supersedes the three proposals in
`docs/design_proposals/` (accounting / execution / product), from which it is synthesised; where this document is
silent, `docs/TERMINAL_DECISIONS.md` wins, then `HANDOFF_2026-10-20.md`, then `BUILD_PROMPT_2026-10-20.md`, then the
directive amendments. Nothing in `TERMINAL_DECISIONS.md` is re-litigated here.

**Plan-check amendment (Claude, 2026-10-20 ~18:00 ET).** The owner amendments written at the top of
`BUILD_PROMPT_2026-10-20.md` after this design was drafted (swap variation margin AND resets 17:03/17:07; "Reset
lifetime" with history 17:09; THREE BOOKS with PB/OTC eligibility 17:41; PRIVATE MARKETS 17:48), the build prompt's
B4 (editable PB fee schedules), B7 (trailing stops, all-or-none option packages), B8 (the old Calculations templates),
B10 (private credit), B16 (Daily News with real headlines, Portfolio Performance statistics, sidebar order), C1/C4
(`docs/MAP.md`, `CHANGELOG.md`), D1–D13 (named acceptance tests) and the directive's §7 (Treasury definition, excess
liquidity / available / encumbrance formulas) and §11 (calculations to proofread) are folded in below. Amended
sections: §1.1, §1.3, §2, §3, §4.1, §5.1, §5.4, §6.0, §6.8, §6.9, §7, §8.2, §8.6, §10, §11.2, §12.4, §12.5,
§13.10–§13.16, §14, §15, §17.2–§17.9, §19.14–§19.24, §20, §21. Where an earlier sentence of this document still says "Build 2" or "NOT IN THIS
BUILD" for swaps, dividends, private credit or the Performance tab, the amended section wins.

Base: the accounting-first proposal's ledger (per-currency AND base balancing, integrity inside the transaction before
COMMIT, projection re-derived from the journal, `capital_explained`, zero-residual P&L explain) with the execution
proposal's storage and matching core (integer cents, fills keyed by observation, new-bar guard, OS-released lock,
`daily()` catch-up closes, no `live` label) and the product proposal's scope split, short-sale construction and UI
chapter. Every judge risk is resolved explicitly in §21.

Reading order for a coding agent: §1 → §2 (your module's signatures) → §3 (DDL) → §4 (bus) → §5–§6 (ledger and
accounting) → the section for your work package → §19 (the hand-worked numbers your tests must reproduce) →
`TERMINAL_WORKPLAN.md`.

**Conventions the UI shows in one sentence** are collected in §1.3; each is also stated where it bites.

---

## 0. One-paragraph summary

One SQLite file (`%USERPROFILE%\.finsim\terminal.db`), one process, one writer. Every change to money or positions is a
**command** executed by the `CommandBus` inside `BEGIN IMMEDIATE … COMMIT` with a client-supplied `request_id` that is
unique in the database, so retries are free and nothing is applied twice. A command posts **journal entries**
(double-entry, integer cents, balanced per currency and in USD base), appends **events**, updates the only projection
(`gl_balances`), writes lots (positions are a VIEW over lots) and runs the ledger-level `integrity()` on what it touched
**before** COMMIT; a failure rolls the whole command back and the caller gets the reason. The ledger is at **cost**;
market value, unrealised P&L, FX result and NAV are computed by one module (`accounting.py`) from the ledger plus the
current marks — no second cash or NAV exists. Prices come only from background workers (Yahoo v8 chart, Cboe delayed
quotes, Treasury.gov, FRED, Frankfurter, FINRA); every mark carries source, provider time, fetch time, delay and
quality; **nothing in this build is labelled `live`**; stale, computed or unavailable quotes never fill. Lifecycle
(settlement, accruals, expiries, variation margin, 102 % collateral marks, sweeps, snapshots) runs from the real clock
as idempotent jobs keyed `(job, reference, date)` and catches up after downtime from observed data (daily bars), never
from guesses. The HTTP server never holds the writer lock on a read; the UI polls small JSON snapshots and shows
freshness on every price. A Windows launcher replaces the old Startup entry and refuses to run twice.

---

## 1. Scope, principles, conventions

### 1.1 Build 1 and Build 2 (the release boundary)

**Build 1 (this session, in this order):** core incl. the three books / lifetimes model → Treasury + funding
(deposits, transfers, sweeps) → US Exchange equities/ETFs incl. short sales with borrows → lifecycle jobs → listed
equity/index options (Cboe chain, Reg-T style margin, expiry/exercise/assignment, hedge candidates) → futures (20
roots, IM/VM) → FX spot + currency loans → prime broker accounts (editable schedules, DVP trading of the US set,
optional financing) → server/API → pages (Daily News first) + ticket → launcher; **in parallel after the equities
package:** PB OTC swaps (IRS, then TRS and cross-currency) with daily variation margin, resets and payments (§13.11,
§13.12); dividends and payments in lieu from Yahoo chart events (§13.15); private markets (§13.13) and private credit
(§13.14); the strategy-template data module (§13.16); Portfolio Performance statistics (§6.9); lifetimes (reset with
history, onboarding agreements, §13.10); the glossary data. Everything in this paragraph is specified with hand-worked
numbers in §19 and ships only when its tests pass; until then its UI surface shows **NOT YET BUILT** (never a fake
number).

**Build 2 (same architecture, after Build 1 is deployed):** Debt Instruments (Treasury benchmarks from the par curve,
curated corporates with FRED OAS), foreign listings (Yahoo meta sessions), FX forwards (CIP), repo / reverse repo,
securities lending out of inventory, recall notices from FINRA data, OTC options and forwards via PB, splits applied
to lots, SEC shares outstanding for 13D/G notices.

Build 2 products are specified here (§13.8, §13.9) so their packages can be built later without a new design, but they
ship as **LIMITED** (marks only, no execution) or **NOT IN THIS BUILD** rows in the UI until their hand-worked tests pass
(§20). Nothing is priced with invented data in either build.

### 1.2 Principles (checked by tests, not by goodwill)

1. **No money creation.** Capital enters only through `treasury.init_capital` (once, request_id `init:capital:1`,
   skipped when the row exists) and explicit `treasury.deposit`. Accounts open empty. Transfers post `3100` on both
   sides and net to zero. No `capital=` default exists anywhere; a missing amount is HTTP 400.
2. **One ledger, cost basis, no MTM postings.** Posted: realised P&L, fees, accruals, variation margin, collateral and
   margin movements, settlements. Computed: market values, unrealised, FX result, NAV, P&L periods.
3. **Atomic or nothing.** One command = one SQLite transaction; projections, lots, events and the journal commit
   together; the scoped ledger-level integrity check runs before COMMIT.
4. **Idempotent by construction.** UNIQUE: `commands.request_id`, `journal_entries.idem_key`, `fills.idem_key`,
   `transfers.idem_key`, `settlements(fill_id, leg)`, `jobs_done(job, key)`, `futures_settle` PK, `marks` PK,
   `nav_snapshots` PK, `accrual_state` PK. Jobs derive request ids from `(job, ref, date)`.
5. **Marks are observed or labelled.** Quality ∈ {`delayed`, `close`, `computed`, `stale`, `unavailable`} (`live` is
   reserved and never produced). Missing = `unavailable`; dependent figures are shown as partial with the reason.
6. **Executions only against observed conditions.** Open session, fresh quote, a quote from the current session, and a
   quantity cap from observed volume per **new** 1-minute bar. No random prices, no invented recalls, no invented
   assignments, no synthesised buy-ins.
7. **Decimal everywhere in accounting.** Integer cents in SQLite (`*_c`), `decimal.Decimal` in Python
   (`finsim.money.D/money/ZERO`), prices as canonical decimal strings. Floats only inside pricing models, converted
   with `D(repr(x))` before any posting.
8. **Reads never wait for writes or the network.** Request threads use pooled reader connections (WAL); only
   `CommandBus.execute` takes the writer lock; chain/bars reads that need a fetch answer 202 and the UI polls.
9. **Errors are loud.** Every worker exception is logged, counted in `/api/health`, and shown in the UI Attention strip.
   No `except: pass`.
10. **Fake data never reaches the owner.** `FakeProvider` exists for tests and `serve --offline`; `--offline` refuses
    the real `FINSIM_HOME` (it needs `--home` pointing elsewhere) and paints a permanent red "FAKE DATA — not market
    prices" banner.

### 1.3 Conventions stated on screen (one sentence each, verbatim in the glossary and where they apply)

| key | sentence |
|---|---|
| FILL_PRICE | "Fills happen at the last observed trade (Yahoo, up to 15 min delayed) or, for options, at the Cboe delayed bid/ask; no price is invented." |
| LIMIT_RULE | "A marketable limit fills at the observed price on its first look; afterwards a crossed limit fills at the limit price." |
| STOP_RULE | "Stops trigger on the observed last trade and fill at that print; a gap through the stop fills at the gap price." |
| PARTICIPATION | "Each new 1-minute bar lets at most 10 % of its volume fill; the remainder keeps working." |
| NO_VOLUME | "With no volume information the order fills in full." |
| SESSION | "Orders entered outside the session rest until it opens; DAY orders expire at that session's close; GTC after 90 days." |
| STALE | "A quote older than its stated delay plus 5 minutes is stale and blocks executions; valuation keeps the last valid mark with its time." |
| COST_BASIS | "Cost basis is gross; commissions are in Fees." |
| FIFO | "Lots are relieved first-in, first-out." |
| EXERCISE | "Exercise and assignment are commission-free; the option premium is rolled into the stock's cost basis or proceeds." |
| OCC | "At expiry an option in the money by USD 0.01 or more is exercised or assigned from the observed closing price; early assignment is not simulated." |
| FUT_SETTLE | "Futures settle daily at the observed price at or before the group's settlement time — not the exchange's official settlement." |
| FUT_MARGIN | "Initial margins are a static table; verify against the exchange." |
| OPT_MARGIN | "Short-option margin is a Reg T-style approximation and is moved to a margin deposit so buying power is honest." |
| SHORT_COLLATERAL | "A borrow posts 102 % cash collateral from the account, marked daily both ways; sale proceeds settle as ordinary cash; the collateral is held at the lender until the borrow is returned." |
| BORROW_AVAIL | "Borrow availability and fee tiers are estimated from FINRA daily short volume and average volume; a real locate would come from a lender." |
| ACCRUAL | "Interest, borrow fees and rebates accrue for each calendar day the balance is open at 17:00 ET, ACT/360 (GBP ACT/365, bonds ACT/ACT), rounded cumulatively so daily postings never drift from the total." |
| RATE_LAG | "Floating rates use the latest published observation on or before the accrual day (SOFR is published the next morning)." |
| FX_SPOT | "FX spot deals at the Yahoo mid plus or minus 0.2 pip against you (pip 0.0001, JPY pairs 0.01) and settles T+2." |
| FX_RESULT | "FX result = foreign balances at today's rate minus the dollars they were worth when booked (no realised/unrealised split)." |
| ACCT_DAY | "The accounting day closes at 17:00 ET; 'today' compares with yesterday's 17:45 ET snapshot." |
| SWEEP | "When an account needs more cash than it holds, exactly the shortfall moves from Treasury as one linked transfer; idle cash above the account's needs returns to Treasury after 17:30 ET." |
| CME_CAL | "CME holidays are approximated from the NYSE calendar." |
| CASH_INT | "Idle Treasury USD earns SOFR − 0.50 % (floor 0); foreign Treasury cash earns nothing in this build." |
| COMPUTED | "A computed mark is a model from observed inputs (shown on hover) and is never executable." |
| PAPER | "PAPER TRADING — fictional capital; free delayed market data." |
| BOOKS | "Three books, each its own lifetime with its own Treasury, accounts and ledger: Personal (USD 1,000,000), Fund (USD 25,000,000), Institutional (USD 1,000,000,000); books never transfer to each other." |
| LIFETIME | "Reset lifetime closes the current lifetime read-only (nothing is deleted) and opens a new one with the capital you type; past lifetimes stay browsable under History." |
| ELIGIBILITY | "OTC swaps need Eligible Contract Participant status (CEA 1a(18): more than USD 10M invested) and a dealer's ISDA Master + CSA + Prime Brokerage Agreement; this terminal unlocks prime brokers and OTC when a book's NAV reaches the editable threshold (default USD 25,000,000) — dealers' real minimums vary; relationships already open stay open if NAV later falls." |
| QP | "Private markets need accredited-investor / qualified-purchaser status (Rule 506, Rule 144 transfer restrictions); here the Private Markets account unlocks at USD 5,000,000 of NAV for the Personal book, USD 25,000,000 for the Fund book and from the start for the Institutional book (editable)." |
| SWAP_MARK | "A swap's mark is the clean present value from the live par curve (IRS) or the underlying's observed price since the last reset (TRS), labelled computed; the current period's coupons accrue separately and are paid on the payment date." |
| SWAP_VM | "Swaps carry daily variation margin with the prime broker: the day's change in the computed mark moves in cash — collateralised-to-market (collateral, not P&L) unless the PB schedule says settled-to-market (realised each day, like futures)." |
| SWAP_RESET | "IRS floating legs fix at each reset from the latest published reference rate; TRS resets settle the return and financing legs in cash and reset the notional to the underlying's current value; cross-currency swaps exchange notional at start and maturity and, when booked mark-to-market, reset the USD notional at each reset with the difference settled in cash." |
| TRAILING | "A trailing stop ratchets with each observed print in your favour (sell: highest last minus the trail; buy: lowest last plus the trail) and never moves back; it triggers on the observed last and fills at that print." |
| AON | "An option-only package fills all-or-none: every leg must have a fresh Cboe quote with displayed size for the whole quantity in the same observation, or nothing fills; packages with a stock or futures leg fill sequentially (the hedge legs after the primary, resized on partial fills)." |
| PRIVATE_MARK | "A private company's mark is the last reported round's implied price per share with its date (never dimmed as stale; it changes only when a new round or a sourced secondary indication is recorded); the P&L is mark minus cost, realised on sale." |
| PRIVATE_TRADE | "Private secondaries are negotiated: the trade executes at the price you enter (default the last round's price ± your discount or premium), pays the placement fee you set (default 3 %), settles after the lag you set (default T+30 calendar days) and carries a lock-up (default 6 months) that the Sell ticket warns about." |
| PRIVATE_CREDIT | "A private loan pays the policy rate plus its spread, ACT/360, quarterly; the original-issue discount accretes straight-line to maturity; amendment fees are recorded when you receive them; the mark is a model price from the live rate and the FRED high-yield spread, labelled computed." |
| DIVIDEND | "Dividends come from Yahoo's chart events: income on the ex-date for long positions, a payment in lieu for shorts; cash on the pay date when Yahoo gives one, else 15 business days after the ex-date (convention)." |
| EXCESS_LIQ | "Excess liquidity = Treasury available + every account's available cash − futures maintenance shortfalls; it is what the book could still deploy today." |
| ENCUMBRANCE | "Encumbrance = collateral and margin the book has posted and cannot spend: stock-borrow collateral at the lender, futures and option margin, OTC initial and variation margin." |
| AVAILABLE | "Available = settled cash + receivables due by the order's settlement date − payables − cash reserved by working orders (per account, per currency)." |
| TREASURY | "Treasury is the lifetime's own funds: contributed capital plus retained results (its cash interest) minus what it has advanced to the accounts; advances are internal transfers that net to zero across the lifetime." |
| PERF | "Performance statistics use daily NAV-to-NAV returns with capital flows removed, annualised by √252, Sharpe and Sortino against SOFR, beta and alpha from a daily regression on SPY's observed closes; they need at least two daily snapshots and are per lifetime." |
| NEWS | "Headlines are real and unedited: Yahoo Finance news search, the Federal Reserve press feed and this repository's own newspaper editions; they are shown as text and never interpreted." |

---

## 2. Package layout `finsim/finsim/terminal/` and public signatures

Standard library only; Python 3.12 (pythonw) and 3.14; zones only via `finsim.tzfallback.get_zone`. Reused from the old
package by import, never edited: `finsim.money` (`D`, `money`, `price`, `ZERO`), `finsim.tzfallback.get_zone`,
`finsim.calendar.us_equity_holidays` and `BusinessCalendar` (rule fallback), `finsim.engines.options_pricing` (`bsm`,
`crr_american`, `price`, `greeks`, `implied_vol`), `finsim.log.get_logger`, `finsim.app._browser_candidates`,
`finsim.app.open_window`, `finsim.app._known_desktop`, `finsim.app._win_special`, `finsim.app._ico_from_png`,
`finsim.app._ascii`, `finsim.app._vb`; for swaps and statistics the pure float helpers `finsim.quant.swaps`
(`fixed_leg_pv`, `floating_leg_pv`, `irs_pv`, `par_swap_rate`, `trs_total_return`, `trs_financing_leg`),
`finsim.engines.otc_pricing` (`schedule`, `yearfrac`, `df`, `swap_dv01`, `trs_pv`, `black76`) and `finsim.quant.stats`
(`mean`, `stdev`, `covariance`, `sharpe_ratio`, `sortino_ratio`) — floats are converted with `D(repr(x))` before any
posting (§1.2 rule 7). The Yahoo/Cboe parsing and `_http_get` plumbing of `finsim2/data/` are **copied** (the terminal
must not import `finsim2`, whose `http_get` depends on `reqctx`); the news fetchers copy the URL and parsing shapes of
`finsim/engines/realnews.py` (`_yahoo`, `_fed`) but never its browser UA for the Fed. Data files read at startup:
`<repo>/data/nyse-holidays.json` (repo root = three levels above the package), `<repo>/site/reports/index.json` (the
newspaper editions, read-only), `finsim/finsim/data/universe.json`, `finsim/finsim/terminal/data/private_companies.json`
(the sourced private-company master, §13.13) and `finsim/finsim/terminal/strategies.py` (the 60 templates of the old
Calculations page, §13.16; `finsim/engines/playbook.py::STRATEGIES` is copied, not imported, because that module
imports the simulation `World`). The glossary data copies `finsim/finsim/static/glossary.js` (§17.8).

```
finsim/finsim/terminal/
  __init__.py          VERSION = "1.0.0"; APP_ID = "finsim-terminal"; SCHEMA_VERSION = 1
  config.py            paths, env, constants (ports, capital, limits, intervals, UA strings)
  units.py             cents <-> Decimal, quantise helpers, base conversion with the rounding plug
  clock.py             Clock / RealClock / FakeClock, NY helpers, accounting_day(), trade_date()
  calendar.py          TradingCalendar: holiday file + rule fallback, early closes, business-day math, settlement dates
  sessions.py          SessionTable: per-group session state, DAY expiry, settle times, foreign sessions
  db.py                Database: DDL, migrations, PRAGMAs, reader pool, writer, backup
  ledger.py            CHART, Line/Entry, post() with per-currency + base validation, balances, lots FIFO
  commands.py          Command, Result, CommandBus, errors, TxContext, handler registry
  accounting.py        ALL read models + integrity() (ledger-level and mark-dependent sets)
  instruments.py       InstrumentMaster, id rules, OCC/futures parsing, FUTURES_SPECS, seeding, resolve
  tables.py            FEES, RATES, PB_SCHEDULES, HTB tiers, CME settle times, compliance limit tables, BOOK_PROFILES, CONVENTIONS (the §1.3 map served as /api/state.conventions)
  books.py             BOOKS catalogue, lifetime ids, create_lifetime() (accounts + init capital), eligibility() (PB/OTC, QP), profile rules
  treasury.py          handlers treasury.init_capital/deposit/withdraw/transfer, account.update_settings; sweep_in()
  lifetimes.py         handlers book.reset_lifetime, book.onboard_pb (agreements), book.update_settings; history read models
  strategies.py        the 60 strategy templates (data) + resolve_strike/zero_cost_call/expiry_for_tenor/size_legs (pure)
  news.py              NewsStore + fetchers (Yahoo news search, Fed RSS, the repo's newspaper index); attention summary
  performance.py       per-lifetime statistics from nav_snapshots + SPY closes (pure; §6.9)
  data/private_companies.json   sourced private-company master (every number carries source_url + as_of or is null)
  quotes/__init__.py   QuoteRecord, Bar1m, QUALITY, freshness(), is_executable(), MarkView, MarkSet protocol, FixedMarks
  quotes/http.py       http_get (UA policy, gzip, size cap), RateLimiter, CircuitBreaker, ProviderHealth
  quotes/providers.py  YahooChart, CboeChain, TreasuryCurve, FredSeries, Frankfurter, FinraShortVolume, FakeProvider
  quotes/cache.py      QuoteCache (thread-safe, pins/subscriptions, persistence), ChainCache (LRU 2, single-flight)
  quotes/worker.py     QuoteWorker (scheduler + 2 fetch threads), RatesRefresher, matcher hook
  quotes/rates.py      CurveStore, RateStore (curve_points table)
  marks.py             MarkProvider (mark selection per kind, computed marks: bond, CIP forward, option model), snapshot()
  orders.py            validate(), preview(), evaluate() (pure), Matcher thread, handlers order.submit/cancel/expire/resume
  execution.py         apply_fill() postings per product, system fills (exercise/assignment/expiry/final), hedge resize
  lending.py           locate(), open_borrow(), return_borrow(), collateral_mark_lines(), accrual lines; recall notices
  credit.py            currency loans (draw/repay/accrual), PB financing draw/repay
  repo.py              Build 2: repo / reverse repo (thin), encumbrance
  fx.py                FX spot (preview/deal/settle), FX forward (Build 2, computed)
  prime.py             account.open_pb, account.update_schedule (editable PB fee schedules), PB credit interest + monthly minimum lines
  products/options.py  chain view, margin formula, handlers options.exercise/expire/margin_mark
  products/futures.py  handlers futures.vm/final/margin check; listing of contract months
  products/bonds.py    Build 2: benchmarks, pricing from the curve, accrued, coupons, maturity
  products/swaps.py    PB OTC IRS: otc.preview/otc.book/otc.terminate, computed mark + DV01, swap.vm (CTM/STM), swap.accrue lines, swap.pay, IM
  products/swaps_trs.py TRS and cross-currency: resets (return + financing legs, notional reset), XCCY notional exchanges, FX notional resets
  products/dividends.py dividends and payments in lieu from Yahoo chart events; split notices
  products/private.py  Private Markets: company master, private.record_round/add_company, negotiated secondary trades, lock-ups, last-round marks
  products/private_credit.py  private loans: pc.commit/pc.amend_fee/pc.repay, nightly coupon + OID accretion lines, quarterly payment, computed mark
  lifecycle.py         Job registry, LifecycleScheduler, due-key discovery, catch-up, EOD snapshot, sweep-out, accruals
  hedges.py            hedge candidates from the live chain / futures table; package creation
  risk.py              exposures, Greeks, DV01, scenarios, concentration — warnings only
  compliance.py        inform-only rule notices (pre-trade + holdings)
  api.py               Router: routes → services/bus; JSON codec; error mapping
  server.py            TerminalServer (exclusive bind), Runtime, single-instance lock, supervisor, shutdown
  app.py               CLI: serve/stop/status/install/open/uninstall/legacy; Windows VBS + shortcuts
  smoke.py             manual network smoke test (`python -m finsim.terminal.smoke`), never run by tests
  static/              index.html, terminal.css, core.js, glossary-data.js, components/*.js, pages/*.js (§17)
```

Import direction: `config/units/clock/calendar/sessions/db/ledger/commands/accounting/instruments/tables/quotes/__init__`
are the core; product modules import the core and each other only through the signatures below; `api`/`server`/`app`
import everything; nothing imports `api`, `server` or `app`.

### 2.1 `config.py`

```python
HOME_ENV = "FINSIM_HOME"; DB_ENV = "FINSIM_TERMINAL_DB"; PORT_ENV = "FINSIM_PORT"
DEFAULT_PORT = 8765; LEGACY_PORT = 8766; HOST = "127.0.0.1"
BASE_CCY = "USD"
# Books (§13.10): code, name, profile, starting capital (editable in Settings; changes apply to NEW lifetimes only)
BOOKS = [("P", "Personal Book", "INDIVIDUAL", 1_000_000_00), ("F", "Fund Book", "FUND", 25_000_000_00),
         ("I", "Institutional Book", "INSTITUTION", 1_000_000_000_00)]
TEST_BOOK = ("T", "Test Book", "FUND", 10_000_000_00)          # terminal_helpers.make_runtime only; keeps §19's 10,000,000 numbers
ECP_FLOOR_C = 10_000_000_00                                     # CEA 1a(18) — below it a book cannot trade OTC at all
PB_OTC_THRESHOLD_C = 25_000_000_00                              # settings.pb_otc_threshold_c default; eligible when NAV >= threshold
QP_THRESHOLD_C = {"INDIVIDUAL": 5_000_000_00, "FUND": 25_000_000_00, "INSTITUTION": 0}   # Private Markets unlock, per profile
# Account roles (§13.10): role, name, kind, sort; account_id = f"{lifetime_id}:{role}" e.g. "P1:TREASURY", "I1:PB_GS"
ACCOUNT_CATALOGUE = [("TREASURY", "Treasury", "TREASURY", 0), ("US_EXCHANGE", "US Exchange", "TRADING", 10),
                     ("DEBT", "Debt Instruments", "TRADING", 20), ("CREDIT", "Credit Instruments", "TRADING", 30),
                     ("FOREIGN_FX", "Foreign Markets & FX", "TRADING", 40), ("PRIVATE", "Private Markets", "TRADING", 50)]
PRIME_BROKERS = {"GS": "Goldman Sachs", "JPM": "JPMorgan", "MS": "Morgan Stanley", "DB": "Deutsche Bank",
                 "BOFA": "BofA Securities", "BARC": "Barclays", "CITI": "Citi", "UBS": "UBS", "BNP": "BNP Paribas"}   # role = "PB_" + key
ACCOUNT_ID_RE = r"^[A-Z][0-9]+:[A-Z_]+$"
PRIVATE_FEE_PCT = Decimal("0.03"); PRIVATE_SETTLE_DAYS = 30; PRIVATE_LOCKUP_MONTHS = 6     # defaults, editable on the ticket
SWAP_IM_BP = 50                                                 # OTC initial margin = DV01 × 50 bp (static, disclosed)
NEWS_YAHOO = "https://query2.finance.yahoo.com/v1/finance/search?q={q}&quotesCount=0&newsCount={n}"   # BROWSER_UA (Yahoo only)
NEWS_FED_RSS = "https://www.federalreserve.gov/feeds/press_all.xml"                                  # GOV_UA
NEWS_PAPER_URL = "https://shafferusa.github.io/intelligence-terminal/"; NEWS_INTERVALS = {"YAHOO": 900, "FED": 3600, "PAPER": 300}
PARTICIPATION_RATE = Decimal("0.10"); GTC_DAYS = 90; STALE_GRACE_S = 300; FETCH_GRACE_S = 300; ERROR_AGE_S = 60
SWEEP_BUFFER_C = 10_000_00; COLLATERAL_PCT = Decimal("1.02"); OCC_THRESHOLD = Decimal("0.01")
SWEEP_CONFIRM_THRESHOLD_C = 2_000_000_00                     # a sweep-in above this needs confirm_large (fat-finger guard)
PREVIEW_TTL_S = 120; PRICE_MOVED_PCT = Decimal("0.005")       # 409 PREVIEW_STALE rules
QUOTE_INTERVALS = {"ORDER": 15, "HELD": 30, "WATCH": 60, "PAGE": 30, "CLOSED": 300, "CHAIN": 90, "CURVE": 86400, "FRED": 21600}
YAHOO_RPS = 2.0; CBOE_RPS = 1.0; FRED_RPS = 1.0
GOV_UA = "LoganTerminal/1.0 (loganshaffer87@gmail.com)"                       # .gov and cdn.finra.org
TERMINAL_UA = "FinSimTerminal/1.0 (paper trading; python stdlib)"             # Cboe, Frankfurter (honest, non-browser)
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"  # Yahoo ONLY
MAX_BYTES = 16 << 20; CHAIN_MAX_DAYS = 400; CHAIN_STRIKE_BAND = Decimal("0.35")
def home() -> str; def db_path() -> str; def log_path() -> str; def lock_path() -> str; def port() -> int
def repo_root() -> str        # the folder holding data/nyse-holidays.json and site/reports/index.json
def newspaper_index() -> str  # <repo_root>/site/reports/index.json
```

### 2.2 `units.py`

```python
def to_c(x: Decimal | str | int) -> int            # quantise 0.01 HALF_EVEN -> cents; ValueError on NaN/inf
def from_c(c: int) -> Decimal                       # cents -> Decimal(2 dp)
def qprice(x) -> Decimal                            # canonical price/rate: at most 8 dp, str() round-trips
def mul_c(qty: int, px: Decimal, mult: Decimal, scale: Decimal = Decimal(1)) -> int   # to_c(qty*px*mult*scale)
def fx_c(amount_c: int, rate: Decimal) -> int       # HALF_EVEN base conversion of one line
def base_with_plug(amounts_c: list[int], rate: Decimal) -> list[int]
    # the rounding plug (§5.3): base of every line but the largest-|amount| line is fx_c(); that line's base is
    # -sum(others), so sum(base) == 0 exactly when sum(amounts) == 0
def fmt_money(c: int) -> str                        # "1,697,525.00"
```

### 2.3 `clock.py`

```python
NY = get_zone("America/New_York")
class Clock:                       # injectable; tests use FakeClock
    def now(self) -> datetime      # aware UTC
    def epoch(self) -> int
    def ny(self) -> datetime
    def today(self) -> date        # NY calendar date
class RealClock(Clock): ...
class FakeClock(Clock):            # FakeClock(at: datetime); set(dt); advance(seconds)
def ny_of(epoch: int) -> datetime
def epoch_of(d: date, hhmm: str) -> int             # NY wall time -> epoch
def accounting_day(now: datetime) -> date           # D = (D-1 17:00 ET, D 17:00 ET]: before 17:00 -> today, else tomorrow
def trade_date(now: datetime, group: str, cal) -> date   # CME groups after 18:00 ET -> next business day; else NY date
def iso(dt: datetime) -> str                        # 'YYYY-MM-DDTHH:MM:SS+00:00'
```

### 2.4 `calendar.py`

```python
class TradingCalendar:
    def __init__(self, holidays_path: str | None, fallback=finsim.calendar.us_equity_holidays): ...
    def is_business_day(self, d: date) -> bool       # Mon-Fri and not a NYSE full holiday
    def is_holiday(self, d: date) -> bool
    def early_close(self, d: date) -> time | None    # 13:00 ET on the file's early-close dates
    def next_business_day(self, d: date) -> date; def prev_business_day(self, d: date) -> date
    def add_business_days(self, d: date, n: int) -> date
    def settlement_date(self, trade_date: date, settle_days: int) -> date
    def month_end(self, d: date) -> date
    def source(self) -> dict                         # {"file": path, "years": [...], "fallback_years": [...]}
```
The file's `years[YYYY].holidays[].date` and `.early_closes[].date` are the sets; years missing from the file use the
rule fallback and are listed in `/api/health.calendar.fallback_years`.

### 2.5 `sessions.py`

```python
GROUPS = ("US_EQUITY", "US_EQUITY_OPTIONS", "US_INDEX_OPTIONS", "US_BONDS", "CME_EQUITY_INDEX", "CME_RATES",
          "NYMEX_ENERGY", "COMEX_METALS", "CBOT_GRAINS", "CME_FX", "FX_SPOT", "FOREIGN")
@dataclass(frozen=True)
class SessionState:
    group: str; open: bool; phase: str              # OPEN | CLOSED | BREAK | HOLIDAY | WEEKEND
    opened_at: int | None; closes_at: int | None; next_open: int | None
    trade_date: str | None; early_close: bool; note: str
class SessionTable:
    def __init__(self, cal: TradingCalendar, clock: Clock, overrides: dict | None = None)
    def state(self, group: str, at: int | None = None, foreign: dict | None = None) -> SessionState
    def states(self, at: int | None = None) -> dict[str, SessionState]
    def day_order_expiry(self, group: str, at: int) -> int     # closes_at of the session the order can first trade in
    def settle_time(self, group: str, d: date) -> int | None   # futures daily settlement time (tables.SETTLE_TIME_ET)
    def is_trading_day(self, group: str, d: date) -> bool
```

### 2.6 `db.py`

```python
class Database:
    def __init__(self, path: str, clock: Clock, log)
    def open(self) -> None                 # backup -> migrate (schema_version gate; newer file -> exit 4) -> PRAGMAs
    def close(self) -> None                # wal_checkpoint(TRUNCATE), close every connection
    @contextmanager
    def reader(self) -> Iterator[sqlite3.Connection]   # pooled (4) read connections, query_only=ON; never the writer lock
    @contextmanager
    def writer(self, timeout: float = 10.0) -> Iterator["Tx"]   # process-wide RLock + BEGIN IMMEDIATE; COMMIT/ROLLBACK
    def backup(self, dest_dir: str, keep: int = 10) -> str | None  # sqlite3 backup API, before the writer opens
    def state_version(self) -> int         # meta.state_version, +1 per committed command (UI polls it)
class Tx:
    conn: sqlite3.Connection; now: datetime; today: str
    def q(self, sql, params=()) -> list[sqlite3.Row]; def one(self, sql, params=()) -> sqlite3.Row | None
    def x(self, sql, params=()) -> sqlite3.Cursor
SCHEMA: list[str]; MIGRATIONS: list[tuple[int, list[str]]]
```
PRAGMAs on every connection: `journal_mode=WAL`, `synchronous=FULL`, `foreign_keys=ON`, `busy_timeout=5000`,
`temp_store=MEMORY`. Reader connections use `check_same_thread=False` and are handed out one at a time from a
`queue.Queue(4)`; a request that cannot get one within 2 s gets 503 `BUSY` (never seen with 4 and short reads).

### 2.7 `ledger.py`

```python
CHART: dict[str, GlSpec]     # GlSpec(code, name, kind, per_instrument: bool, bucket: str)   (§5.1)
@dataclass(frozen=True)
class Line:
    account_id: str; gl: str; currency: str; amount_c: int              # signed: debit +, credit −
    instrument_id: str | None = None; lot_id: int | None = None; memo: str = ""; counterparty: str | None = None
    fx_rate: Decimal | None = None      # None -> the command's FxTable rate; FX-deal handlers set the deal rate
@dataclass
class Entry:
    kind: str; memo: str; lines: list[Line]; idem_key: str
    ref_type: str | None = None; ref_id: str | None = None; business_date: str | None = None
class LedgerError(Exception): ...
class DuplicatePosting(LedgerError): ...    # idem_key reuse -> 409 DUPLICATE_POSTING
def dr(account_id, gl, currency, amount_c, **kw) -> Line      # amount_c > 0 debit; negative flips (old helper semantics)
def cr(account_id, gl, currency, amount_c, **kw) -> Line
def post(ctx: "TxContext", entry: Entry) -> int
    # validate (§5.3): lines non-empty, every amount_c != 0, gl in CHART, account exists, currency 3 letters,
    # per-currency sum == 0, base per line via base_with_plug() per (currency, fx_rate) group, entry base sum == 0,
    # per_instrument codes carry instrument_id; inserts entry + lines; updates gl_balances; records touched keys
def balance(conn, account_id, gl, currency=None, instrument_id=None) -> int      # natural sign (§5.1), cents
def base_balance(conn, account_id, gl, currency=None, instrument_id=None) -> int
def balances_by(conn, account_id, gl_prefix="") -> dict[tuple[str, str, str], int]  # (gl, ccy, inst) -> natural cents
@dataclass
class Relief: lot_id: int; qty: int; cost_relieved_c: int; proceeds_c: int; realised_c: int
def open_lot(tx, account_id, instrument_id, side, fill_id, qty, unit_cost: Decimal, cost_c: int, currency) -> int
def relieve_fifo(tx, account_id, instrument_id, side, qty, close_fill_id, proceeds_c_total: int) -> list[Relief]
    # oldest open lot first (open_ts, lot_id); proceeds allocated pro rata by qty, residual cent to the last relief
def position(conn, account_id, instrument_id) -> tuple[int, int]    # (signed qty, cost_c) from v_positions
```

### 2.8 `commands.py`

```python
@dataclass(frozen=True)
class Command:
    kind: str; payload: dict; request_id: str; actor: str = "ui"    # ui | matcher | lifecycle | cli | init | worker
@dataclass
class Result:
    ok: bool; http: int; body: dict; replayed: bool = False; command_id: int | None = None
class RejectError(Exception):     code: str; message: str; details: dict; http = 400   # validation / business rejection
class ConflictError(RejectError): http = 409                                           # stale quote, funds, reuse, hold
class IntegrityFailure(Exception): report: "IntegrityReport"                           # 500, rolled back, Attention
class TxContext:
    tx: Tx; conn: sqlite3.Connection; now: datetime; clock: Clock; cal: TradingCalendar; sessions: SessionTable
    marks: "MarkSet"; fx: "FxTable"; command_id: int; request_id: str; actor: str
    touched_accounts: set[str]; touched_instruments: set[tuple[str, str]]; touched_entries: list[int]
    def post(self, entry: Entry) -> int
    def event(self, type: str, account_id=None, instrument_id=None, ref_type=None, ref_id=None, **payload) -> int
    def attention(self, severity: str, source: str, code: str, message: str, details: dict | None = None) -> int
Handler = Callable[[TxContext, dict], dict]
class CommandBus:
    def __init__(self, db: Database, clock: Clock, cal: TradingCalendar, sessions: SessionTable,
                 marks_provider: "MarkProvider | None", log)
    def register(self, kind: str, handler: Handler, light: bool = False) -> None
    def execute(self, cmd: Command) -> Result
    def on_commit(self, listener: Callable[[Command, Result], None]) -> None     # outside the lock
```
`FxTable = {ccy: (rate_to_usd: Decimal, source: str, as_of: int)}` captured from the mark snapshot at the start of a
command (USD → `1`); `marks` is the same snapshot (immutable for the command).

### 2.9 `accounting.py`

```python
class Accounting:
    def __init__(self, db: Database, instruments: InstrumentMaster, marks: "MarkProvider", clock: Clock, cal, sessions)
    def cash(self, account_id: str) -> dict[str, CashView]
    def available(self, account_id: str, currency: str, settle_date: date | None = None) -> int
    def buying_power(self, account_id: str, currency: str, settle_date: date | None = None) -> BuyingPower
    def positions(self, scope: str) -> list[PositionView]          # account id or "ALL"
    def position(self, account_id: str, instrument_id: str) -> PositionView | None
    def lots(self, account_id: str, instrument_id: str) -> list[LotView]
    def nav(self, scope: str) -> NavView
    def balance_sheet(self, scope: str) -> BalanceSheet
    def pnl(self, scope: str, period: str) -> PnlView             # TODAY | MTD | YTD | ITD
    def treasury(self) -> TreasuryView
    def transactions(self, scope: str, *, from_date=None, to_date=None, kinds=None, instrument_id=None, limit=500, offset=0) -> list[TxnRow]
    def obligations(self, account_id: str) -> ObligationsView
    def flows(self, scope: str, from_ts: int, to_ts: int) -> int
    def liquidity(self, scope: str) -> LiquidityView                 # excess_liquidity_c, encumbrance_c with their formulas (§6.0)
    def integrity(self, mode: str = "full", touched: Touched | None = None, snapshot: "MarkSet | None" = None,
                  lifetime_id: str | None = None) -> IntegrityReport
        # mode: "scoped" (ledger-level, inside a command), "ledger" (ledger-level, whole DB), "full" (+ mark-dependent WARN set)
        # lifetime_id: None = every OPEN lifetime; a CLOSED lifetime is checked only at its reset and the report stored on its row
```
All read models take a reader connection and a mark snapshot; nothing here writes. Shapes in §6. **Scopes** (§6.0):
an `account_id` (`P1:US_EXCHANGE`), `"{lifetime_id}:ALL"` (one lifetime consolidated, current or archived),
`"ALL"` (the active lifetime of the UI's selected book — the API resolves it from the `book` query parameter) and
`"ALL_BOOKS"` (the three open lifetimes consolidated; there are no transfers between books, so nothing is eliminated).

### 2.10 `instruments.py`

```python
@dataclass(frozen=True)
class Instrument:
    instrument_id: str; kind: str; symbol: str; name: str; currency: str; multiplier: Decimal; tick: Decimal
    lot: int; price_scale: Decimal; session_group: str; settle_days: int; account_kinds: tuple[str, ...]
    yahoo_symbol: str | None; cboe_symbol: str | None; underlying_id: str | None
    expiry: str | None; strike: Decimal | None; right: str | None; style: str | None; exercise: str | None
    spec: dict; support: str; status: str
    def is_option(self) -> bool; def is_future(self) -> bool; def is_bond(self) -> bool; def is_fx(self) -> bool
    def is_private(self) -> bool; def is_swap(self) -> bool       # kind PRIVATE (private company shares) / SWAP (an OTC contract row)
    def contract_value_c(self, px: Decimal, qty: int) -> int    # qty*px*multiplier*price_scale (bonds: face*px/100)
class InstrumentMaster:
    def __init__(self, db: Database, log)
    def load(self) -> None; def get(self, instrument_id) -> Instrument | None
    def search(self, q: str, kinds=(), limit=20) -> list[Instrument]
    def by_underlying(self, underlying_id: str, expiry: str | None = None) -> list[Instrument]
    def seed(self, tx: Tx) -> int                       # first run (§7.4)
    def ensure_option(self, tx, occ: str, underlying: Instrument, chain_row) -> Instrument
    def ensure_futures_listings(self, tx, today: date, cal) -> int
    def ensure_foreign_listing(self, tx, yahoo_symbol: str, meta: dict) -> Instrument   # Build 2
    def upsert_from_yahoo_meta(self, tx, symbol: str, meta: dict) -> Instrument         # resolve unknown US tickers
    def tradeable_in(self, inst: Instrument, account_kind: str) -> bool
def parse_occ(s: str) -> tuple[str, date, str, Decimal] | None
def futures_id(code: str, year: int, month: int) -> str; def futures_yahoo(code, year, month) -> str
def last_trade_date(code: str, year: int, month: int, cal: TradingCalendar) -> date
def fx_yahoo(pair: str) -> tuple[str, bool]            # ("JPY=X", invert=True)
FUTURES_SPECS: dict[str, FutSpec]                      # §12.3
```

### 2.11 `quotes/__init__.py` (the contract every other package codes against)

```python
QUALITY = ("live", "delayed", "close", "computed", "private", "stale", "unavailable")   # live: reserved, never produced
# private: a last-reported-round mark (§13.13) — valuation only, never executable, never aged into "stale"
@dataclass
class Bar1m: t: int; o: Decimal; h: Decimal; l: Decimal; c: Decimal; v: int | None
@dataclass
class QuoteRecord:
    instrument_id: str; provider: str; source: str; method: str          # direct | chain | front_month_continuous | curve | cip | model | daily_close
    last: Decimal | None; bid: Decimal | None; ask: Decimal | None; bid_size: int | None; ask_size: int | None; mid: Decimal | None
    prev_close: Decimal | None; day_high: Decimal | None; day_low: Decimal | None; day_volume: int | None
    quote_time: int | None; fetched_at: int; delay_s: int; delay_label: str; currency: str
    session_group: str; exchange_tz: str | None; provider_session: tuple[int, int] | None
    bar: Bar1m | None; bars_tail: list[Bar1m]; error: str | None; inputs: dict | None; quality: str
def freshness(rec: QuoteRecord | None, session: SessionState, now: int) -> str          # §8.2
def is_executable(rec, session, now) -> tuple[bool, str]                                 # (ok, reason)
@dataclass
class MarkView: instrument_id: str; mark: Decimal | None; quality: str; source: str; method: str; quote_time: int | None
                fetched_at: int | None; delay_label: str; inputs: dict | None; executable: bool; note: str
class MarkSet(Protocol):            # an immutable snapshot used by one command or one read
    def mark(self, instrument_id: str) -> MarkView
    def fx(self, ccy: str) -> MarkView            # USD per 1 unit of ccy; USD -> mark 1, quality "delayed", source "const"
    def quote(self, instrument_id: str) -> QuoteRecord | None
    def as_of(self) -> int
class FixedMarks(MarkSet): ...      # tests: FixedMarks({"AAPL": ("339.50", "delayed", quote_time), "EURUSD": ...})
```

### 2.12 `orders.py`, `execution.py`, `lending.py`, `credit.py`, `fx.py`, `prime.py`, products, lifecycle, hedges, risk

```python
# orders.py
def validate(services, req: dict) -> ValidatedOrder                 # raises RejectError/ConflictError (§10.2)
def preview(services, req: dict) -> dict                             # pure; §10.3 shape; stores preview_id in PreviewStore
class PreviewStore: put(preview) -> str; get(preview_id) -> dict | None   # in-memory ring of 200, TTL 120 s
@dataclass
class Decision: action: str; price: Decimal | None; qty: int; reason: str; bar_ts: int | None   # NONE | FILL | TRIGGER
def evaluate(order: OrderRow, rec: QuoteRecord | None, session: SessionState, now: int, participation: Decimal) -> Decision  # pure (§10.5)
def fill_cap(order, rec, inst, participation) -> tuple[int, str]     # (qty cap this pass, disclosure)
class Matcher:                                                       # worker-side: on_quotes(ids) / on_submit(order_id)
    def __init__(self, db, bus, cache, sessions, instruments, clock, log)
    def on_quotes(self, instrument_ids: Iterable[str]) -> int        # evaluates open orders; bus.execute(order.match ...) per FILL/TRIGGER
    def sweep(self) -> int                                           # every 15 s: all open orders whose session is open
# handlers: order.submit, order.cancel, order.match, order.expire, order.resume, order.record_rejection (light)
# execution.py
@dataclass
class FillSpec: order_id: str | None; account_id: str; instrument_id: str; side: str; qty: int; price: Decimal
                origin: str; quote: QuoteRecord | None; idem_key: str; bar_ts: int | None; price_basis: str; fee_override_c: int | None = None
def apply_fill(ctx: TxContext, spec: FillSpec, services) -> FillResult        # §10.7 steps, every product
def apply_system_fill(ctx, account_id, inst, side, qty, price, origin, quote, idem_key, note) -> FillResult   # exercise/assignment/expiry/final
def fund(ctx, account_id, currency, need_c: int, link_type: str, link_id: str, settle_date: date) -> FundingResult  # own cash -> sweep-in -> PB -> refuse
# lending.py
@dataclass
class LocateQuote: instrument_id: str; qty: int; available: bool; qty_available: int; fee_rate: Decimal; rebate_rate: Decimal
                   tier: str; lender: str; collateral_c: int; mark: Decimal; mark_quality: str; valid_until: int; basis: str
def locate(services, account_id, inst, qty) -> LocateQuote
def open_borrow(ctx, services, account_id, inst, qty, locate: LocateQuote, order_id: str | None) -> str
def consume_for_short(ctx, borrow_id: str, qty: int, fill_id: str) -> None
def return_borrow(ctx, services, borrow_id: str, qty: int, fill_id: str | None, reason: str) -> None
def collateral_mark_lines(ctx, borrow, mark: Decimal, mark_date: str) -> tuple[list[Line], int]   # one movement to 102 %
def accrual_lines(ctx, borrow, d: str, mark: Decimal, sofr: Decimal) -> list[Line]                 # fee + rebate for night d
# handlers: credit.borrow_stock, credit.return_stock, credit.collateral_mark
# credit.py
# handlers: credit.loan_draw, credit.loan_repay, credit.pb_draw, credit.pb_repay; accrual_lines_loan(ctx, loan, d, index, fx) -> list[Line]
# fx.py
def spot_preview(services, account_id, buy_ccy, sell_ccy, amount_c, amount_ccy) -> dict
# handlers: fx.spot, fx.forward (Build 2), fx.settle (job)
# prime.py
# handlers: account.open_pb; def schedule(account) -> PbSchedule; OTC endpoints return NOT_IN_THIS_BUILD
# products/options.py
def margin_requirement_c(positions_in_account, marks) -> tuple[int, list[dict]]     # §13.3 formula, per underlying
# handlers: options.exercise, options.expire, options.margin_mark
# products/futures.py
# handlers: futures.vm, futures.final; def im_required_c(inst, qty) -> int
# lifecycle.py
class Job(Protocol): name: str; def due(self, conn, clock, cal, sessions) -> list[str]; def run(self, ctx, key: str, services) -> dict
class LifecycleScheduler(threading.Thread):
    def __init__(self, bus, db, services, clock, log, interval_s: int = 30)
    def tick(self, at: int | None = None) -> dict     # tests call this with a FakeClock
    def catch_up(self) -> dict                         # = tick(); keys derive from state, so one tick catches up
REGISTRY: list[Job]
# hedges.py
def candidates(services, account_id, inst, side, qty, chain) -> list[HedgeCandidate]
def package(ctx, services, primary_order_id, candidate_id, legs, ratio) -> str
# risk.py / compliance.py
def report(services, scope) -> RiskView; def pretrade(services, account_id, inst, side, qty, preview) -> list[Notice]; def holdings(services, scope) -> list[Notice]
# books.py (core; §13.10)
@dataclass(frozen=True)
class Book: code: str; name: str; profile: str; starting_capital_c: int          # profile INDIVIDUAL | FUND | INSTITUTION
@dataclass(frozen=True)
class Eligibility: lifetime_id: str; nav_c: int; pb_otc: bool; pb_otc_reason: str; private_markets: bool; private_reason: str
                   ecp: bool; threshold_c: int; qp_threshold_c: int; has_relationships: bool
def lifetime_id(code: str, seq: int) -> str                       # "P1"
def account_id(lifetime_id: str, role: str) -> str                # "P1:US_EXCHANGE"
def split_account_id(account_id: str) -> tuple[str, str]           # ("P1", "US_EXCHANGE")
def create_lifetime(tx, bus_or_ctx, book: Book, seq: int, capital_c: int, name: str | None = None) -> str
    # inserts the lifetimes row, the catalogue accounts (PRIVATE only when QP_THRESHOLD_C[profile] == 0; otherwise created on unlock),
    # posts init capital through treasury.init_capital with request_id f"init:capital:{lifetime_id}" (skipped when present)
def eligibility(services, lifetime_id: str, nav_c: int | None = None) -> Eligibility
def require_pb_otc(services, account_id: str) -> None             # raises RejectError NOT_ELIGIBLE with the plain-English reason
def require_private(services, lifetime_id: str) -> None           # raises RejectError NOT_ELIGIBLE_QP
# lifetimes.py (§13.10)
# handlers: book.reset_lifetime {book_code, starting_capital_c, confirm: "RESET", name?}, book.onboard_pb {lifetime_id, broker}, book.update_settings (light)
def history(services, lifetime_id: str) -> LifetimeView           # row + final figures + links to the read-only pages
# strategies.py (§13.16; data copied from finsim/engines/playbook.py::STRATEGIES, 60 templates in 12 groups)
GROUPS: list[tuple[str, str]]; STRATEGIES: list[dict]; BY_KEY: dict[str, dict]
def resolve_strike(rule: str, spot: Decimal, listed: list[Decimal]) -> Decimal      # ATM | pct:<n> | absolute; nearest listed, ties -> lower
def zero_cost_call(rows, put_premium: Decimal, spot: Decimal) -> Decimal | None    # call strike above spot whose bid is nearest the put's ask
def expiry_for_tenor(expiries: list[date], today: date, months: int) -> date | None   # nearest listed >= today + months (calendar)
def size_legs(strategy: dict, units: int, spot: Decimal, fut_px: Decimal | None, fut_mult: Decimal | None) -> list[LegSpec]
    # stock qty = round(units×size); option contracts = max(1, round(units×size/100)); futures n = max(1, round(units×size×spot/(fut_px×mult))); TRS units = round(units×size)
def package_legs(services, account_id, key: str, params: dict) -> PackageSpec      # resolved legs with instrument ids, est prices, fill_mode AON|SEQUENTIAL
# products/swaps.py (§13.11)
def preview(services, account_id, kind: str, terms: dict) -> dict                  # par rate, dealt rate (± PB half-width), mark0, DV01, IM, schedules, conventions
# handlers: otc.book, otc.terminate, swap.vm (job), swap.pay (job), swap.fix (job); accrual_lines_swap(ctx, contract, d, rates) -> list[Line]
def mark(services, contract, at: int | None = None) -> tuple[int, dict]             # clean PV cents + inputs (curve as_of, fixings), quality computed
def dv01_c(services, contract) -> int
# products/swaps_trs.py (§13.12): handlers trs.reset (job), xccy.exchange (job), xccy.fx_reset (job); mark_trs(), mark_xccy()
# products/dividends.py (§13.15): handlers div.ex (job), div.pay (job); def events_for(services, inst) -> list[DivEvent]  (from YahooChart.events)
# products/private.py (§13.13): handlers private.add_company (light), private.record_round (light), private.trade {account_id, company_id, side, qty, price, fee_pct, settle_days, lockup_months}
def private_mark(company) -> MarkView                                                # quality "private", note "last round <date>"
# products/private_credit.py (§13.14): handlers pc.commit, pc.amend_fee, pc.repay, pc.pay (job); accrual_lines_pc(ctx, loan, d, index) -> list[Line]; def pc_mark(services, loan) -> tuple[int, dict]
# news.py (§17.9)
class NewsStore: def items(self, limit=60, source=None) -> list[NewsItem]; def refresh(self, which: str) -> int   # worker thread only
def attention_summary(services, scope) -> dict                                       # counts for "Requires your attention"
# performance.py (§6.9)
def statistics(snapshots: list[Snap], spy_closes: dict[str, Decimal], sofr: Decimal) -> PerfView   # pure; floats for ratios, cents for NAVs
# prime.py additions (§12.4): handler account.update_schedule (light); def schedule(account) -> PbSchedule reads pb_schedules; accrual_lines_pb_credit(); monthly_minimum_line()
# orders.py additions (§10.5, §10.9): TRAILING_STOP in validate/evaluate; def evaluate_package(pkg, legs, recs, session, now) -> PackageDecision; handler order.match_package
```

`services` is the `Runtime` bundle (§16) — `db, clock, cal, sessions, instruments, cache, chains, marks, accounting,
bus, rates, curve, settings, log` — passed explicitly, never imported as a global.

---

## 3. SQLite schema (DDL, `db.SCHEMA`, applied in order; `meta.schema_version = 1`)

Types: `INTEGER` for cents (`*_c`), quantities, epoch seconds (`*_ts`); `TEXT` for ids, ISO dates (`YYYY-MM-DD`, New
York), decimal prices/rates (canonical strings, never summed in SQL) and JSON. No `STRICT` (older SQLite builds still
open the file). Every table is `CREATE TABLE IF NOT EXISTS`.

```sql
PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000; PRAGMA temp_store=MEMORY;

-- 3.1 bookkeeping
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- keys: schema_version, state_version, created_ts, instance_id (uuid4), base_ccy, app_version_last_opened, integrity_json, integrity_ts

CREATE TABLE commands (
  id            INTEGER PRIMARY KEY,
  request_id    TEXT NOT NULL UNIQUE,                 -- ^[A-Za-z0-9:_.-]{8,80}$
  kind          TEXT NOT NULL, actor TEXT NOT NULL,   -- actor: ui | matcher | lifecycle | cli | init | worker
  payload_json  TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
  result_json   TEXT NOT NULL, http_status INTEGER NOT NULL DEFAULT 200,
  started_ts    INTEGER NOT NULL, finished_ts INTEGER NOT NULL, duration_ms INTEGER NOT NULL,
  app_version   TEXT NOT NULL, state_version INTEGER NOT NULL      -- meta.state_version after this command
);                                                     -- only COMMITTED commands exist here (a rejection leaves no row)

CREATE TABLE events (
  id INTEGER PRIMARY KEY, command_id INTEGER NOT NULL REFERENCES commands(id),
  ts INTEGER NOT NULL, type TEXT NOT NULL,             -- ORDER_SUBMITTED ORDER_TRIGGERED ORDER_PARTIAL ORDER_FILLED ORDER_CANCELLED ORDER_EXPIRED ORDER_SUSPENDED ORDER_RESUMED
                                                       -- SETTLED ACCRUED PAID CAPITAL_DEPOSITED CAPITAL_WITHDRAWN TRANSFER SWEEP_IN SWEEP_OUT BORROW_OPENED BORROW_RETURNED COLLATERAL_MARKED
                                                       -- LOAN_DRAWN LOAN_REPAID FX_DEALT FX_SETTLED OPTION_EXPIRED OPTION_EXERCISED OPTION_ASSIGNED MARGIN_MOVED FUTURES_VM FUTURES_FINAL
                                                       -- NAV_SNAPSHOT ACCOUNT_OPENED SETTINGS_CHANGED ATTENTION
  account_id TEXT, instrument_id TEXT, ref_type TEXT, ref_id TEXT, payload_json TEXT NOT NULL
);
CREATE INDEX ix_events_acct ON events(account_id, id); CREATE INDEX ix_events_type ON events(type, id);

CREATE TABLE jobs_done (job TEXT NOT NULL, key TEXT NOT NULL, run_ts INTEGER NOT NULL, command_id INTEGER,
  status TEXT NOT NULL CHECK (status IN ('DONE','SKIPPED')), note TEXT, PRIMARY KEY (job, key));
CREATE TABLE job_failures (job TEXT NOT NULL, key TEXT NOT NULL, failures INTEGER NOT NULL, last_error TEXT NOT NULL,
  last_ts INTEGER NOT NULL, parked_until_ts INTEGER, PRIMARY KEY (job, key));

CREATE TABLE attention (
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, severity TEXT NOT NULL CHECK (severity IN ('INFO','WARN','ERROR')),
  source TEXT NOT NULL, code TEXT NOT NULL, message TEXT NOT NULL, details_json TEXT,
  account_id TEXT, ref_type TEXT, ref_id TEXT, resolved_ts INTEGER, auto_resolve INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_attention_open ON attention(resolved_ts) WHERE resolved_ts IS NULL;

CREATE TABLE provider_health (provider TEXT PRIMARY KEY, last_ok_ts INTEGER, last_err_ts INTEGER, last_error TEXT,
  consecutive_errors INTEGER NOT NULL DEFAULT 0, requests_today INTEGER NOT NULL DEFAULT 0, day TEXT, down_until_ts INTEGER);
CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
CREATE TABLE sequences (name TEXT PRIMARY KEY, next INTEGER NOT NULL);      -- order ids O-000001, fill ids F-000001, ...

-- 3.2 accounts, chart, instruments
CREATE TABLE books (book_code TEXT PRIMARY KEY, name TEXT NOT NULL, profile TEXT NOT NULL CHECK (profile IN ('INDIVIDUAL','FUND','INSTITUTION')),
  starting_capital_c INTEGER NOT NULL, sort_order INTEGER NOT NULL, created_ts INTEGER NOT NULL);       -- P, F, I (and T in tests)
CREATE TABLE lifetimes (                               -- §13.10: one row per lifetime; a reset CLOSES the row and opens the next seq
  lifetime_id TEXT PRIMARY KEY, book_code TEXT NOT NULL REFERENCES books(book_code), seq INTEGER NOT NULL, name TEXT NOT NULL,
  started_ts INTEGER NOT NULL, ended_ts INTEGER, starting_capital_c INTEGER NOT NULL, status TEXT NOT NULL CHECK (status IN ('OPEN','CLOSED')),
  final_nav_c INTEGER, final_realised_c INTEGER, final_pnl_c INTEGER, final_quality TEXT, reason TEXT, integrity_json TEXT,
  UNIQUE (book_code, seq)
);
CREATE TABLE agreements (id INTEGER PRIMARY KEY, lifetime_id TEXT NOT NULL REFERENCES lifetimes(lifetime_id), broker TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('ISDA_MASTER','CSA','PBA')), signed_date TEXT NOT NULL, command_id INTEGER NOT NULL, note TEXT,
  UNIQUE (lifetime_id, broker, kind));                 -- recorded by book.onboard_pb (one click); informational
CREATE TABLE accounts (
  account_id TEXT PRIMARY KEY,                         -- "{lifetime_id}:{role}": P1:TREASURY | P1:US_EXCHANGE | I1:PRIVATE | F1:PB_GS ... (§13.10)
  book_code TEXT NOT NULL REFERENCES books(book_code), lifetime_id TEXT NOT NULL REFERENCES lifetimes(lifetime_id),
  role TEXT NOT NULL,                                  -- TREASURY | US_EXCHANGE | DEBT | CREDIT | FOREIGN_FX | PRIVATE | PB_GS ... PB_BNP
  name TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('TREASURY','TRADING','PB')),
  broker TEXT, fee_schedule TEXT NOT NULL DEFAULT 'DEFAULT',
  status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED')),
  sweep_out INTEGER NOT NULL DEFAULT 1, sweep_buffer_c INTEGER NOT NULL DEFAULT 1000000,   -- USD 10,000.00
  pb_financing INTEGER NOT NULL DEFAULT 0, pb_limit_c INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 100, opened_ts INTEGER NOT NULL, closed_ts INTEGER, settings_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX ix_accounts_lifetime ON accounts(lifetime_id, sort_order);
CREATE TABLE pb_schedules (account_id TEXT PRIMARY KEY REFERENCES accounts(account_id), schedule_json TEXT NOT NULL, updated_ts INTEGER NOT NULL);
  -- §12.4: seeded from tables.PB_SCHEDULES at account.open_pb; edited by account.update_schedule (Settings); read by prime.schedule()
CREATE TABLE chart (gl TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('ASSET','LIABILITY','EQUITY','INCOME','EXPENSE')),
  per_instrument INTEGER NOT NULL DEFAULT 0, bucket TEXT NOT NULL);

CREATE TABLE instruments (
  instrument_id TEXT PRIMARY KEY,                      -- §7.1 id rules
  kind TEXT NOT NULL CHECK (kind IN ('EQUITY','ETF','ADR','REIT','PREFERRED','OPTION','FUTURE','BOND','FX','INDEX','YIELD','PRIVATE','SWAP')),
  symbol TEXT NOT NULL, name TEXT NOT NULL, currency TEXT NOT NULL,
  multiplier TEXT NOT NULL DEFAULT '1', tick TEXT NOT NULL DEFAULT '0.01', lot INTEGER NOT NULL DEFAULT 1,
  price_scale TEXT NOT NULL DEFAULT '1',               -- quoted price -> currency units (0.01 for pence; grains quoted in cents use multiplier 50)
  session_group TEXT NOT NULL, settle_days INTEGER NOT NULL DEFAULT 1,
  account_kinds TEXT NOT NULL,                         -- JSON list: ["US_EXCHANGE","PB"] ...
  yahoo_symbol TEXT, yahoo_invert INTEGER NOT NULL DEFAULT 0, cboe_symbol TEXT, underlying_id TEXT,
  expiry TEXT, strike TEXT, right TEXT CHECK (right IN ('C','P')), style TEXT, exercise TEXT,   -- options: AMERICAN/EUROPEAN, PHYSICAL/CASH
  spec_json TEXT NOT NULL DEFAULT '{}',                -- futures {root, month, year, im_c, mm_c, settle_time_et, last_trade}; equity {adv, beta, shares_outstanding, cik, dividend_yield}; bond {...}
  support TEXT NOT NULL DEFAULT 'FULL' CHECK (support IN ('FULL','LIMITED','COMPUTED','UNSUPPORTED')),
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','EXPIRED','DELISTED')),
  created_ts INTEGER NOT NULL, updated_ts INTEGER NOT NULL
);
CREATE INDEX ix_instruments_sym ON instruments(symbol); CREATE INDEX ix_instruments_under ON instruments(underlying_id, expiry);
CREATE UNIQUE INDEX ux_instruments_yahoo ON instruments(yahoo_symbol) WHERE yahoo_symbol IS NOT NULL AND kind <> 'OPTION';
CREATE TABLE exchange_sessions (exchange TEXT NOT NULL, day TEXT NOT NULL, tz TEXT NOT NULL, open_ts INTEGER NOT NULL,
  close_ts INTEGER NOT NULL, fetched_ts INTEGER NOT NULL, PRIMARY KEY (exchange, day));        -- Build 2 (foreign listings)

-- 3.3 the ledger (single source of truth)
CREATE TABLE journal_entries (
  entry_id INTEGER PRIMARY KEY, command_id INTEGER NOT NULL REFERENCES commands(id), event_id INTEGER,
  ts INTEGER NOT NULL, business_date TEXT NOT NULL,   -- accounting day (§6.6)
  kind TEXT NOT NULL,                                  -- CAPITAL TRANSFER SWEEP_IN SWEEP_OUT FILL SETTLE FEE ACCRUAL PAY VM IM OPT_MARGIN COLLATERAL BORROW LOAN FX EXPIRY EXERCISE ASSIGN FINAL ADJUST
  memo TEXT NOT NULL, ref_type TEXT, ref_id TEXT,
  idem_key TEXT NOT NULL UNIQUE                        -- §5.4 templates name every key
);
CREATE TABLE journal_lines (
  line_id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL REFERENCES journal_entries(entry_id), line_no INTEGER NOT NULL,
  account_id TEXT NOT NULL REFERENCES accounts(account_id), gl TEXT NOT NULL REFERENCES chart(gl), currency TEXT NOT NULL,
  amount_c INTEGER NOT NULL CHECK (amount_c <> 0),     -- signed: debit +, credit −, in `currency`
  base_c INTEGER NOT NULL,                             -- signed USD cents at fx_rate (plug rule §5.3)
  fx_rate TEXT NOT NULL, fx_source TEXT NOT NULL,      -- '1' / 'const' for USD
  instrument_id TEXT, lot_id INTEGER, counterparty TEXT, memo TEXT,
  UNIQUE (entry_id, line_no)
);
CREATE INDEX ix_lines_acct ON journal_lines(account_id, gl, currency); CREATE INDEX ix_lines_inst ON journal_lines(account_id, instrument_id, gl);
CREATE INDEX ix_lines_entry ON journal_lines(entry_id);

CREATE TABLE gl_balances (                             -- the ONLY projection; verified against journal_lines (§6.7 L3)
  account_id TEXT NOT NULL, gl TEXT NOT NULL, currency TEXT NOT NULL, instrument_id TEXT NOT NULL DEFAULT '',
  balance_c INTEGER NOT NULL, base_c INTEGER NOT NULL, PRIMARY KEY (account_id, gl, currency, instrument_id)
);

-- 3.4 flows
CREATE TABLE transfers (
  transfer_id TEXT PRIMARY KEY, command_id INTEGER NOT NULL, ts INTEGER NOT NULL, business_date TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('INITIAL_CAPITAL','DEPOSIT','WITHDRAWAL','TRANSFER','SWEEP_IN','SWEEP_OUT')),
  from_account TEXT, to_account TEXT, currency TEXT NOT NULL, amount_c INTEGER NOT NULL CHECK (amount_c > 0),
  linked_type TEXT, linked_id TEXT, shortfall_c INTEGER,     -- SWEEP_IN: the fill/settlement/borrow/margin it paid for and the recorded shortfall
  entry_id INTEGER NOT NULL REFERENCES journal_entries(entry_id), idem_key TEXT NOT NULL UNIQUE
);

-- 3.5 orders, fills, lots, settlements
CREATE TABLE orders (
  order_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, account_id TEXT NOT NULL REFERENCES accounts(account_id),
  instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
  side TEXT NOT NULL CHECK (side IN ('BUY','SELL','SELL_SHORT','BUY_TO_COVER','BTO','STO','BTC','STC')),
  qty INTEGER NOT NULL CHECK (qty > 0), filled_qty INTEGER NOT NULL DEFAULT 0, avg_px TEXT,
  order_type TEXT NOT NULL CHECK (order_type IN ('MARKET','LIMIT','STOP','STOP_LIMIT','TRAILING_STOP')), limit_px TEXT, stop_px TEXT,
  trail_amt TEXT, trail_pct TEXT, trail_ref_px TEXT,  -- TRAILING_STOP (§10.5): the trail and the best observed print it ratchets from
  fill_mode TEXT NOT NULL DEFAULT 'SEQUENTIAL' CHECK (fill_mode IN ('SEQUENTIAL','AON')),   -- AON legs match together (§10.9)
  tif TEXT NOT NULL CHECK (tif IN ('DAY','GTC')),
  status TEXT NOT NULL CHECK (status IN ('WORKING','PARTIAL','FILLED','CANCELLED','EXPIRED','SUSPENDED','WAITING_PARENT')),
  triggered INTEGER NOT NULL DEFAULT 0, first_eval_done INTEGER NOT NULL DEFAULT 0, last_bar_ts INTEGER, last_quote_time INTEGER,
  session_group TEXT NOT NULL, created_ts INTEGER NOT NULL, updated_ts INTEGER NOT NULL, expires_ts INTEGER NOT NULL,
  participation TEXT NOT NULL DEFAULT '0.10', borrow_id TEXT, package_id TEXT, parent_order_id TEXT, ratio TEXT,
  fees_c INTEGER NOT NULL DEFAULT 0, reason TEXT, preview_json TEXT NOT NULL
);
CREATE INDEX ix_orders_open ON orders(status, instrument_id) WHERE status IN ('WORKING','PARTIAL','SUSPENDED','WAITING_PARENT');
CREATE INDEX ix_orders_acct ON orders(account_id, created_ts);
CREATE TABLE order_events (id INTEGER PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(order_id), ts INTEGER NOT NULL,
  from_status TEXT, to_status TEXT NOT NULL, note TEXT NOT NULL, quote_json TEXT);
CREATE TABLE order_rejections (                        -- audit of refused submissions (handoff §7: rejected orders are preserved)
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, request_id TEXT NOT NULL, account_id TEXT, instrument_id TEXT,
  side TEXT, qty INTEGER, order_type TEXT, code TEXT NOT NULL, message TEXT NOT NULL, payload_json TEXT NOT NULL);

CREATE TABLE fills (
  fill_id TEXT PRIMARY KEY, order_id TEXT REFERENCES orders(order_id), account_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),   -- position effect; the order's side is on the order
  qty INTEGER NOT NULL CHECK (qty > 0), price TEXT NOT NULL, gross_c INTEGER NOT NULL, fee_c INTEGER NOT NULL DEFAULT 0,
  currency TEXT NOT NULL, fx_rate TEXT NOT NULL, ts INTEGER NOT NULL, trade_date TEXT NOT NULL, settle_date TEXT,
  origin TEXT NOT NULL CHECK (origin IN ('ORDER','EXERCISE','ASSIGNMENT','EXPIRY','FUTURES_FINAL','MATURITY','NEGOTIATED')),
  price_basis TEXT NOT NULL,                           -- last | ask | bid | limit | stop_print | strike | zero | settle | par | negotiated
  quote_json TEXT NOT NULL,                            -- the full QuoteRecord used (provenance; replay needs no network)
  bar_ts INTEGER, cap_qty INTEGER, cap_note TEXT, entry_id INTEGER NOT NULL REFERENCES journal_entries(entry_id),
  idem_key TEXT NOT NULL UNIQUE                        -- match:<order_id>:<quote_time>:<bar_ts|none> | <job>:<key>
);
CREATE INDEX ix_fills_pos ON fills(account_id, instrument_id, ts);

CREATE TABLE lots (
  lot_id INTEGER PRIMARY KEY, account_id TEXT NOT NULL, instrument_id TEXT NOT NULL, side TEXT NOT NULL CHECK (side IN ('LONG','SHORT')),
  open_fill_id TEXT NOT NULL REFERENCES fills(fill_id), open_ts INTEGER NOT NULL, qty_open INTEGER NOT NULL, qty_remaining INTEGER NOT NULL CHECK (qty_remaining >= 0),
  unit_cost TEXT NOT NULL, cost_c INTEGER NOT NULL,    -- remaining gross cost (LONG) / remaining gross proceeds (SHORT), instrument currency
  currency TEXT NOT NULL, lock_until TEXT              -- private secondaries: lock-up end date (§13.13); NULL otherwise
);
CREATE INDEX ix_lots_open ON lots(account_id, instrument_id, side) WHERE qty_remaining > 0;
CREATE TABLE lot_relief (id INTEGER PRIMARY KEY, close_fill_id TEXT NOT NULL REFERENCES fills(fill_id), lot_id INTEGER NOT NULL REFERENCES lots(lot_id),
  qty INTEGER NOT NULL, cost_relieved_c INTEGER NOT NULL, proceeds_c INTEGER NOT NULL, realised_c INTEGER NOT NULL);
CREATE VIEW v_positions AS
  SELECT account_id, instrument_id,
         SUM(CASE WHEN side='LONG' THEN qty_remaining ELSE -qty_remaining END) AS qty,
         SUM(CASE WHEN side='LONG' THEN cost_c ELSE -cost_c END) AS cost_c, MIN(currency) AS currency
  FROM lots WHERE qty_remaining > 0 GROUP BY account_id, instrument_id;

CREATE TABLE settlements (
  settle_id TEXT PRIMARY KEY, fill_id TEXT REFERENCES fills(fill_id), fx_deal_id TEXT, account_id TEXT NOT NULL,
  leg TEXT NOT NULL,                                   -- CASH | BUY_CCY | SELL_CCY
  currency TEXT NOT NULL, amount_c INTEGER NOT NULL,   -- + receivable (we receive cash) / − payable
  gl TEXT NOT NULL,                                    -- 1200 | 2100 | 1250 | 2350
  due_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','SETTLED')),
  settled_ts INTEGER, entry_id INTEGER, UNIQUE (fill_id, leg), UNIQUE (fx_deal_id, leg)
);
CREATE INDEX ix_settle_due ON settlements(status, due_date);

-- 3.6 financing and derivatives state
CREATE TABLE borrows (
  borrow_id TEXT PRIMARY KEY, command_id INTEGER NOT NULL, account_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
  qty INTEGER NOT NULL, qty_returned INTEGER NOT NULL DEFAULT 0, fee_rate TEXT NOT NULL, rebate_rate TEXT NOT NULL, tier TEXT NOT NULL,
  lender TEXT NOT NULL, collateral_c INTEGER NOT NULL, currency TEXT NOT NULL, opened_ts INTEGER NOT NULL, opened_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED')), closed_ts INTEGER, order_id TEXT, reason TEXT,
  last_mark_date TEXT, recall_notice TEXT
);
CREATE TABLE loans (
  loan_id TEXT PRIMARY KEY, command_id INTEGER NOT NULL, account_id TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('CASH_LOAN','PB_MARGIN')),
  currency TEXT NOT NULL, principal_c INTEGER NOT NULL, index_name TEXT NOT NULL, spread TEXT NOT NULL, day_basis INTEGER NOT NULL,
  opened_date TEXT NOT NULL, maturity_date TEXT, lender TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED')), closed_ts INTEGER
);
CREATE TABLE repos (repo_id TEXT PRIMARY KEY, command_id INTEGER NOT NULL, account_id TEXT NOT NULL, side TEXT NOT NULL,
  instrument_id TEXT NOT NULL, face INTEGER NOT NULL, price TEXT NOT NULL, haircut TEXT NOT NULL, principal_c INTEGER NOT NULL,
  rate TEXT NOT NULL, opened_date TEXT NOT NULL, maturity_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN');     -- Build 2
CREATE TABLE fx_deals (
  deal_id TEXT PRIMARY KEY, command_id INTEGER NOT NULL, account_id TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('SPOT','FORWARD')),
  buy_ccy TEXT NOT NULL, sell_ccy TEXT NOT NULL, buy_c INTEGER NOT NULL, sell_c INTEGER NOT NULL, rate TEXT NOT NULL, mid TEXT NOT NULL,
  spread_cost_c INTEGER NOT NULL, quote_json TEXT NOT NULL, trade_ts INTEGER NOT NULL, trade_date TEXT NOT NULL, value_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','SETTLED')), settled_ts INTEGER, entry_id INTEGER NOT NULL, inputs_json TEXT
);
CREATE TABLE futures_settle (account_id TEXT NOT NULL, instrument_id TEXT NOT NULL, settle_date TEXT NOT NULL,
  settle_px TEXT NOT NULL, qty_after INTEGER NOT NULL, vm_c INTEGER NOT NULL, quote_json TEXT NOT NULL, entry_id INTEGER,
  PRIMARY KEY (account_id, instrument_id, settle_date));
CREATE TABLE margin_state (account_id TEXT PRIMARY KEY, as_of_ts INTEGER NOT NULL, options_required_c INTEGER NOT NULL,
  options_detail_json TEXT NOT NULL, note TEXT);        -- 1310 balance must equal options_required_c (§6.7 L9)
CREATE TABLE accrual_state (                           -- cumulative-rounding accruals (§11.3)
  kind TEXT NOT NULL, ref_id TEXT NOT NULL, segment_start TEXT NOT NULL, base_c INTEGER NOT NULL, rate TEXT NOT NULL,
  day_basis INTEGER NOT NULL, accrued_through TEXT, cum_c INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (kind, ref_id)
);
CREATE TABLE hedge_packages (package_id TEXT PRIMARY KEY, command_id INTEGER NOT NULL, account_id TEXT NOT NULL, primary_order_id TEXT,
  candidate_id TEXT NOT NULL, strategy_key TEXT, legs_json TEXT NOT NULL, fill_mode TEXT NOT NULL DEFAULT 'SEQUENTIAL',
  status TEXT NOT NULL, created_ts INTEGER NOT NULL);  -- primary_order_id NULL for option-only AON packages (every leg is a package leg)

-- 3.6a PB OTC swaps (§13.11, §13.12)
CREATE TABLE otc_contracts (
  contract_id TEXT PRIMARY KEY,                        -- SWP-000001 (sequences); also an instruments row of kind SWAP (currency = the USD/base leg)
  command_id INTEGER NOT NULL, account_id TEXT NOT NULL REFERENCES accounts(account_id), broker TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('IRS','TRS','XCCY')), terms_json TEXT NOT NULL,   -- §13.11 / §13.12 term dictionaries
  notional_c INTEGER NOT NULL, currency TEXT NOT NULL, start_date TEXT NOT NULL, maturity_date TEXT NOT NULL,
  margin_mode TEXT NOT NULL CHECK (margin_mode IN ('CTM','STM')), im_c INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','TERMINATED','MATURED')),
  last_reset_date TEXT, reset_px TEXT, mark_c INTEGER NOT NULL DEFAULT 0, mark_ts INTEGER, vm_balance_c INTEGER NOT NULL DEFAULT 0,
  inputs_json TEXT, closed_ts INTEGER
);
CREATE TABLE otc_vm (contract_id TEXT NOT NULL REFERENCES otc_contracts(contract_id), vm_date TEXT NOT NULL, mark_c INTEGER NOT NULL,
  delta_c INTEGER NOT NULL, mode TEXT NOT NULL, entry_id INTEGER, inputs_json TEXT NOT NULL, PRIMARY KEY (contract_id, vm_date));
CREATE TABLE otc_resets (contract_id TEXT NOT NULL REFERENCES otc_contracts(contract_id), event_date TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('FIX','PAY','RESET','NOTIONAL_START','NOTIONAL_END','FX_RESET')), amount_c INTEGER NOT NULL,
  detail_json TEXT NOT NULL, entry_id INTEGER, PRIMARY KEY (contract_id, event_date, kind));

-- 3.6b private markets and private credit (§13.13, §13.14)
CREATE TABLE private_companies (company_id TEXT PRIMARY KEY, name TEXT NOT NULL, sector TEXT, country TEXT,
  status TEXT NOT NULL CHECK (status IN ('PRIVATE','FILED_IPO','PUBLIC')), ticker TEXT, instrument_id TEXT NOT NULL,   -- instrument kind PRIVATE
  notes TEXT, updated_ts INTEGER NOT NULL);
CREATE TABLE private_rounds (id INTEGER PRIMARY KEY, company_id TEXT NOT NULL REFERENCES private_companies(company_id), round_date TEXT NOT NULL,
  round_type TEXT NOT NULL,                            -- Series X | tender offer | secondary | SAFE | secondary indication (owner-recorded)
  post_money_c INTEGER, price_per_share TEXT, lead_investors TEXT, source_url TEXT NOT NULL, as_of TEXT NOT NULL,
  recorded_by TEXT NOT NULL CHECK (recorded_by IN ('seed','owner')), command_id INTEGER, UNIQUE (company_id, round_date, round_type));
CREATE TABLE private_loans (
  loan_id TEXT PRIMARY KEY, command_id INTEGER NOT NULL, account_id TEXT NOT NULL REFERENCES accounts(account_id), instrument_id TEXT NOT NULL,
  company_id TEXT, borrower TEXT NOT NULL, currency TEXT NOT NULL, principal_c INTEGER NOT NULL, issue_price TEXT NOT NULL,   -- 98 = 2 % OID
  index_name TEXT NOT NULL, spread TEXT NOT NULL, day_basis INTEGER NOT NULL DEFAULT 360, payment_months INTEGER NOT NULL DEFAULT 3,
  start_date TEXT NOT NULL, maturity_date TEXT NOT NULL, next_payment_date TEXT NOT NULL, oas_issue TEXT,
  status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','REPAID')), closed_ts INTEGER
);

-- 3.6c dividends (§13.15) and news (§17.9)
CREATE TABLE dividends (instrument_id TEXT NOT NULL, ex_date TEXT NOT NULL, amount TEXT NOT NULL, pay_date TEXT, source TEXT NOT NULL,
  fetched_ts INTEGER NOT NULL, PRIMARY KEY (instrument_id, ex_date));
CREATE TABLE news_items (id INTEGER PRIMARY KEY, source TEXT NOT NULL CHECK (source IN ('yahoo','fed','newspaper')), title TEXT NOT NULL,
  publisher TEXT NOT NULL, link TEXT NOT NULL, time_ts INTEGER NOT NULL, tickers_json TEXT NOT NULL DEFAULT '[]', category TEXT,
  fetched_ts INTEGER NOT NULL, UNIQUE (source, link));
CREATE INDEX ix_news_time ON news_items(time_ts);

-- 3.7 marks, snapshots, quotes, rates
CREATE TABLE marks (                                   -- observed end-of-day marks (append-only; provenance kept)
  instrument_id TEXT NOT NULL, mark_date TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('CLOSE','SETTLE','CURVE')),
  price TEXT NOT NULL, currency TEXT NOT NULL, quality TEXT NOT NULL, source TEXT NOT NULL, method TEXT NOT NULL,
  quote_time INTEGER, fetched_at INTEGER NOT NULL, note TEXT, PRIMARY KEY (instrument_id, mark_date, kind)
);
CREATE TABLE nav_snapshots (
  account_id TEXT NOT NULL, snap_date TEXT NOT NULL, ts INTEGER NOT NULL,
  nav_c INTEGER NOT NULL, settled_c INTEGER NOT NULL, pending_c INTEGER NOT NULL, mv_long_c INTEGER NOT NULL, mv_short_c INTEGER NOT NULL,
  unrealised_c INTEGER NOT NULL, fx_result_c INTEGER NOT NULL, realised_cum_c INTEGER NOT NULL, income_cum_c INTEGER NOT NULL,
  financing_cum_c INTEGER NOT NULL, fees_cum_c INTEGER NOT NULL, capital_cum_c INTEGER NOT NULL, transfers_cum_c INTEGER NOT NULL,
  flows_day_c INTEGER NOT NULL, pnl_day_c INTEGER NOT NULL, quality TEXT NOT NULL, partial INTEGER NOT NULL DEFAULT 0,
  detail_json TEXT NOT NULL, PRIMARY KEY (account_id, snap_date)           -- account_id '{lifetime_id}:ALL' = that lifetime consolidated
);
CREATE TABLE quote_cache (instrument_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, updated_ts INTEGER NOT NULL);
CREATE TABLE curve_points (series TEXT NOT NULL, as_of TEXT NOT NULL, tenor TEXT NOT NULL, value TEXT NOT NULL, fetched_ts INTEGER NOT NULL,
  source TEXT NOT NULL, PRIMARY KEY (series, as_of, tenor));   -- UST_PAR tenors 1M..30Y; FRED series tenor 'VALUE'
CREATE TABLE finra_short (symbol TEXT NOT NULL, day TEXT NOT NULL, short_volume INTEGER NOT NULL, total_volume INTEGER NOT NULL, PRIMARY KEY (symbol, day));
CREATE TABLE watchlists (id INTEGER PRIMARY KEY, account_id TEXT NOT NULL, name TEXT NOT NULL, instrument_id TEXT NOT NULL,
  position INTEGER NOT NULL, UNIQUE (account_id, name, instrument_id));
```

Idempotency enforced by the schema: `commands.request_id`; `journal_entries.idem_key`; `fills.idem_key`;
`transfers.idem_key`; `settlements (fill_id, leg)` / `(fx_deal_id, leg)`; `jobs_done (job, key)`; `futures_settle` PK;
`marks` PK; `nav_snapshots` PK; `accrual_state` PK; `orders.request_id`. A duplicate insert raises
`sqlite3.IntegrityError`, which the bus maps to 409 `DUPLICATE_POSTING` after confirming the stored command (if any)
carries the same payload hash.

Migrations: `MIGRATIONS = [(1, SCHEMA)]`; `db.open()` applies the steps above `meta.schema_version` in one transaction; a
file whose `schema_version` is newer than the code refuses to open (exit 4). Backup at every `serve` start into
`<home>/backups/terminal-YYYYmmdd-HHMMSS.db` (newest 10 kept) via the sqlite3 backup API. `finsim.db` is never opened.

### 3.1 Seed rows (`db.open()` on an empty file, inside one transaction)

- `books`: the three `config.BOOKS` rows (tests: `TEST_BOOK` only). `accounts`: NOT seeded by DDL — `books.create_lifetime()`
  creates each lifetime's catalogue accounts (`{lifetime}:TREASURY`, `US_EXCHANGE`, `DEBT`, `CREDIT`, `FOREIGN_FX`; `PRIVATE` at
  once for the INSTITUTION profile, else on unlock); PB accounts are created by `account.open_pb` (after `book.onboard_pb`).
- `chart`: §5.1. `settings`: `participation_rate "0.10"`, `sweep_out_time_et "17:30"`, `snapshot_time_et "17:45"`,
  `treasury_reserve_c 0`, `session_overrides {}`, `sweep_confirm_threshold_c 200000000`, `theme "dark"`,
  `pb_otc_threshold_c 2500000000`, `qp_threshold_c {"INDIVIDUAL": 500000000, "FUND": 2500000000, "INSTITUTION": 0}`,
  `book_defaults {code: {name, starting_capital_c}}` (applied to NEW lifetimes only), `private_defaults {fee_pct "0.03",
  settle_days 30, lockup_months 6}`, `news_enabled true`.
- `instruments`: `InstrumentMaster.seed()` (§7.4) incl. the private-company master (§13.13).
- The initial capital is NOT seeded by DDL: `server.start()` (and `make_runtime`) calls `books.create_lifetime()` for every
  book that has no OPEN lifetime; it posts `Command("treasury.init_capital", {"lifetime_id": "P1", "amount_c": 100000000,
  "currency": "USD", "source": "initial capital"}, request_id="init:capital:P1", actor="init")` only when `commands` has no
  row with that request_id (so a later change of a constant can never re-post or 409). First run: `P1` 1,000,000.00,
  `F1` 25,000,000.00, `I1` 1,000,000,000.00.

---

## 4. The CommandBus contract (`commands.CommandBus.execute`)

```
execute(cmd):
  0. request_id must match ^[A-Za-z0-9:_.-]{8,80}$ else Result(False, 400, BAD_REQUEST_ID) (nothing stored)
  1. acquire the process-wide writer RLock, timeout 10 s -> Result(False, 503, WRITER_BUSY) + Retry-After: 1 (nothing stored)
  2. BEGIN IMMEDIATE  (sqlite3.OperationalError busy/locked -> ROLLBACK, Result(False, 503, RETRY), Retry-After: 1)
  3. row = SELECT kind, payload_sha256, result_json, http_status FROM commands WHERE request_id = ?
     - row and sha256(canonical json(payload)) == row.sha -> ROLLBACK; return stored result, replayed=True, header X-Replayed: 1
     - row and sha differs                               -> ROLLBACK; Result(False, 409, REQUEST_ID_REUSED {stored_kind})
  4. INSERT commands (request_id, kind, actor, payload, sha, result_json='{}', started_ts, ...) -> command_id
     (inside the transaction: a rejection or failure leaves NO trace, so the same request_id may be retried after the cause is fixed)
  5. ctx = TxContext(now = clock.now(), marks = marks_provider.snapshot(), fx = FxTable(marks), ...)
  6. body = handler(ctx, payload)                       # may raise RejectError / ConflictError / IntegrityFailure / any Exception
  7. if not light: report = accounting.integrity("scoped", touched=ctx.touched, snapshot=ctx.marks)
     - not report.ok -> raise IntegrityFailure(report)
  8. UPDATE commands SET result_json, finished_ts, duration_ms, state_version = meta.state_version + 1; meta.state_version += 1
  9. COMMIT; release the lock; run on_commit listeners (quote pins for new instruments, matcher wake, UI counter)
  on RejectError/ConflictError: ROLLBACK everything; log at INFO; return Result(False, e.http, {error:{code,message,details}})
  on IntegrityFailure / sqlite3.IntegrityError (duplicate key; -> 409 DUPLICATE_POSTING) / any other Exception:
      ROLLBACK; log.exception; in a NEW short writer transaction INSERT attention(ERROR, source='bus', code=COMMAND_FAILED|INTEGRITY, details={request_id, kind, problems});
      return Result(False, 500, {error:{code, message, incident_id}})   -- nothing stored for the id; a retry is allowed
```

- `request_id`: the UI generates `ui:<uuid4>` per click and keeps it across retries of that click; the matcher uses
  `match:<order_id>:<quote_time>:<bar_ts|none>` (`matchpkg:<package_id>:<quote_time>` for AON packages); jobs use
  `job:<name>:<key>`; the init uses `init:capital:<lifetime_id>`; the CLI `cli:<uuid4>`.
- Handlers never open transactions, never sleep, never do network I/O; the quote snapshot a command uses is in the
  payload (`quote` for matches) or taken from the immutable `ctx.marks` at step 5.
- `light=True` handlers (`quotes.persist_batch`, `rates.record`, `marks.record`, `instruments.upsert`, `watchlist.*`,
  `settings.set`, `attention.resolve`, `order.record_rejection`, `provider_health.update`) skip events and the scoped
  integrity check (they touch no ledger table) but still run inside the lock and the transaction.
- Background callers (`Matcher`, `LifecycleScheduler`, `QuoteWorker` persistence) call `bus.execute` exactly like the
  API. A job's failure is recorded in `job_failures` (count, error) and `attention`; the scheduler retries every tick
  with backoff 1, 2, 4, 8, 16 min, then hourly, and the attention severity becomes ERROR after 5 failures.
- HTTP mapping: `RejectError` 400, `ConflictError` 409, not found 404, `WRITER_BUSY`/`RETRY` 503 (+ `Retry-After: 1`;
  the UI retries the same request_id up to 3 times), everything else 500. Body:
  `{"error": {"code": "...", "message": "...", "details": {...}}, "request_id": "..."}`.

### 4.1 Handler registry (kind → module)

| kind | module | actor |
|---|---|---|
| treasury.init_capital, treasury.deposit, treasury.withdraw, treasury.transfer, account.update_settings | `treasury.py` | init / ui / cli |
| book.reset_lifetime, book.onboard_pb, book.update_settings (light) | `lifetimes.py` | ui |
| account.open_pb, account.close, account.update_schedule (light) | `prime.py` | ui |
| order.submit, order.cancel, order.match, order.match_package, order.expire, order.resume, order.record_rejection (light) | `orders.py` | ui / matcher / lifecycle |
| otc.book, otc.terminate, swap.vm, swap.fix, swap.pay | `products/swaps.py` | ui / lifecycle |
| trs.reset, xccy.exchange, xccy.fx_reset | `products/swaps_trs.py` | lifecycle |
| private.add_company (light), private.record_round (light), private.trade | `products/private.py` | ui |
| pc.commit, pc.amend_fee, pc.repay, pc.pay | `products/private_credit.py` | ui / lifecycle |
| div.ex, div.pay, div.split_notice | `products/dividends.py` | lifecycle |
| credit.borrow_stock, credit.return_stock, credit.collateral_mark | `lending.py` | ui / lifecycle |
| credit.loan_draw, credit.loan_repay, credit.pb_draw, credit.pb_repay | `credit.py` | ui / execution |
| fx.spot, fx.forward (Build 2), fx.settle | `fx.py` | ui / lifecycle |
| options.exercise, options.expire, options.margin_mark | `products/options.py` | ui / lifecycle |
| futures.vm, futures.final | `products/futures.py` | lifecycle |
| bonds.coupon, bonds.accrue, bonds.mature (Build 2) | `products/bonds.py` | lifecycle |
| repo.open, repo.close, repo.accrue (Build 2) | `repo.py` | ui / lifecycle |
| lifecycle.settle, lifecycle.accrue, lifecycle.pay_monthly, lifecycle.sweep_out, lifecycle.snapshot, lifecycle.expire_orders, lifecycle.housekeeping, lifecycle.integrity_full | `lifecycle.py` | lifecycle |
| quotes.persist_batch, marks.record, rates.record, instruments.upsert, watchlist.add/remove, settings.set, attention.resolve, provider_health.update (light) | `quotes/*`, `instruments.py`, `api.py` | worker / ui |

---

## 5. The ledger

### 5.1 Chart of accounts (`ledger.CHART`; old numbering kept where the meaning is the same)

Balances per `(account_id, gl, currency[, instrument_id])`. `bucket` is the read-model category. Storage sign:
`amount_c` debit-positive for every kind. Natural-sign balance for display: ASSET/EXPENSE = `+balance`,
LIABILITY/EQUITY/INCOME = `−balance`.

| gl | name | kind | per_instr | bucket | notes |
|---|---|---|---|---|---|
| 1010 | Cash — settled | ASSET | 0 | cash_settled | per currency; the only spendable cash |
| 1100 | Investments — equities/ETFs at cost | ASSET | 1 | positions_cost | gross, FIFO lots |
| 1110 | Investments — bonds at cost | ASSET | 1 | positions_cost | Build 2 |
| 1120 | Investments — private company shares at cost | ASSET | 1 | positions_cost | §13.13; FIFO lots with `lock_until` |
| 1130 | Private credit loans at amortised cost | ASSET | 1 | positions_cost | §13.14; principal × issue price + OID accreted |
| 1200 | Receivable — unsettled sales | ASSET | 0 | cash_pending_in | per currency; one settlements row per fill |
| 1210 | Dividends receivable | ASSET | 1 | accrued_income | §13.15 (Build 1) |
| 1220 | Accrued interest receivable — bonds and private credit | ASSET | 1 | accrued_income | bonds Build 2; private credit §13.14 |
| 1225 | Accrued swap receivable | ASSET | 1 | accrued_income | receive-leg coupons / TRS return accrual, per contract (§13.11) |
| 1230 | Accrued interest receivable — cash | ASSET | 0 | accrued_income | Treasury idle USD; PB credit interest (§12.4) |
| 1250 | FX settlement receivable | ASSET | 0 | cash_pending_in | per currency |
| 1300 | Margin deposit — futures initial margin | ASSET | 1 | margin | = IM × contracts at all times |
| 1310 | Margin deposit — listed options | ASSET | 0 | margin | = margin_state.options_required_c |
| 1320 | Variation margin posted — OTC (PB) | ASSET | 1 | margin | CTM swaps: collateral we posted when the mark fell; = −vm_balance_c when negative (L22) |
| 1330 | Initial margin — OTC (PB) | ASSET | 1 | margin | = otc_contracts.im_c while OPEN |
| 1360 | Cross-currency notional receivable | ASSET | 1 | financing_asset | XCCY: the notional we delivered at start, returned at maturity (§13.12) |
| 1400 | Cash collateral posted — stock borrow | ASSET | 1 | collateral | 102 %, marked daily; memo = borrow_id |
| 1410 | Accrued rebate receivable | ASSET | 1 | accrued_income | per borrow instrument |
| 1500 | Reverse repo receivable | ASSET | 0 | financing_asset | Build 2 |
| 1700 | Options purchased at cost | ASSET | 1 | positions_cost | |
| 2100 | Payable — unsettled purchases | LIABILITY | 0 | cash_pending_out | per currency |
| 2300 | Accrued interest payable — PB financing | LIABILITY | 0 | accrued_expense | |
| 2310 | Accrued borrow fees payable | LIABILITY | 1 | accrued_expense | per borrow instrument |
| 2320 | Payments in lieu payable | LIABILITY | 1 | accrued_expense | §13.15 (Build 1) |
| 2330 | Variation margin received — OTC (PB) | LIABILITY | 1 | margin_liability | CTM swaps: collateral the PB posted to us when the mark rose; = vm_balance_c when positive |
| 2340 | Accrued swap payable | LIABILITY | 1 | accrued_expense | pay-leg coupons / TRS financing accrual, per contract |
| 2350 | FX settlement payable | LIABILITY | 0 | cash_pending_out | per currency |
| 2360 | Cross-currency notional payable | LIABILITY | 1 | financing | XCCY: the notional we received at start, repaid at maturity; MTM resets adjust it |
| 2500 | Repo borrowing | LIABILITY | 0 | financing | Build 2 |
| 2600 | Securities sold short (at proceeds) | LIABILITY | 1 | positions_short | gross proceeds, FIFO short lots |
| 2700 | Prime-broker financing | LIABILITY | 0 | financing | PB accounts |
| 2750 | Currency loans — principal | LIABILITY | 0 | financing | per currency |
| 2755 | Accrued interest payable — currency loans | LIABILITY | 0 | accrued_expense | per currency |
| 2900 | Options written (at premium received) | LIABILITY | 1 | positions_short | |
| 3000 | Contributed capital | EQUITY | 0 | capital | Treasury only |
| 3100 | Treasury advances (internal funding) | EQUITY | 0 | transfers | Dr in Treasury, Cr in the funded account; Σ = 0 |
| 3900 | FX conversion clearing | EQUITY | 0 | fx_clearing | the two legs of an FX deal; nets to 0 in base |
| 4000 | Realised P&L — securities and options | INCOME | 1 | realised | fills, expiries, cash-settled options, private secondaries |
| 4200 | Dividend income | INCOME | 1 | income | §13.15 (Build 1) |
| 4300 | Interest income — cash and coupons | INCOME | 1 | income | Treasury cash (instrument ''); PB credit interest (''); private credit coupon + OID (loan id); bonds Build 2 |
| 4320 | Fee income — private credit | INCOME | 1 | income | amendment / arrangement fees recorded by the owner (§13.14) |
| 4350 | Rebate income — cash collateral | INCOME | 1 | income | |
| 4400 | Futures variation margin | INCOME | 1 | realised | daily cash settlement |
| 4450 | Swap results — resets, net coupons, STM settlements | INCOME | 1 | realised | per contract: IRS coupon accruals (both legs), TRS return/financing settlements, STM daily settlements, termination value |
| 4500 | Reverse repo interest | INCOME | 0 | income | Build 2 |
| 5000 | Commissions and fees | EXPENSE | 1 | fees | |
| 5100 | Interest expense — PB financing | EXPENSE | 0 | financing_cost | |
| 5150 | Interest expense — currency loans | EXPENSE | 0 | financing_cost | |
| 5300 | Securities borrow fees | EXPENSE | 1 | financing_cost | |
| 5400 | Payments in lieu | EXPENSE | 1 | financing_cost | §13.15 (Build 1) |
| 5500 | Interest expense — repo | EXPENSE | 0 | financing_cost | Build 2 |
| 5700 | Buy-in penalties | EXPENSE | 1 | financing_cost | never posted automatically (§13.1); kept for a manual, disclosed buy-in |

Not in the chart on purpose: unrealised accounts, FX translation accounts, forward/OTC PV accounts — these are
overlays computed from marks (§6). `1030 Cash — restricted` does not exist: "short proceeds restricted until covered"
is implemented by the 102 % collateral construction (§13.1) and shown as "held at lender (102 % collateral)".

### 5.2 Entry validation (`ledger.post`)

1. Lines non-empty; every `amount_c != 0`; every `gl` in `CHART`; every `account_id` exists and is OPEN; `currency`
   is three upper-case letters; `per_instrument` codes carry `instrument_id` (others carry `''` in `gl_balances`).
2. **Per currency:** for each currency in the entry, Σ `amount_c` = 0. A cross-currency deal is therefore two legs
   through `3900` (one per currency), each balanced in its own currency (§5.4 FX template).
3. **Base:** lines are grouped by `(currency, fx_rate)`; USD lines get `base_c = amount_c`; every other group gets
   `base_with_plug(amounts, rate)` (§5.3), so each group — and therefore the entry — sums to zero in base exactly.
4. `idem_key` unique (schema) → `DuplicatePosting`.
5. Insert `journal_entries` + `journal_lines` (line_no 1..n), then `INSERT INTO gl_balances ... ON CONFLICT DO UPDATE
   SET balance_c = balance_c + ?, base_c = base_c + ?` per line; record `(account_id)`, `(account_id, instrument_id)`
   and `entry_id` in the context's touched sets.

### 5.3 The base-rounding plug rule (resolves the "3-line foreign entry fails by 0.01" risk)

For a group of lines in one currency at one rate whose native amounts sum to zero: compute `base_c = fx_c(amount_c,
rate)` (HALF_EVEN) for every line except the line with the largest `|amount_c|` (ties: the first such line); that
line's `base_c = −Σ(other base_c)`. The plug is at most `(n − 1) × 0.005` USD and lands on the largest line, where it
is immaterial. Because a single-currency group always nets to zero, Σ base over the entry is zero **by construction**,
never by luck. Tests: a EUR share buy with a fee (3 lines, rate 1.1238, amounts 100,000.33 / 5.00 / −100,005.33) posts
without a residual; the per-line bases are asserted.

### 5.4 Posting templates (the only places that post; every template names its `idem_key`)

Notation `Dr gl / Cr gl`; amounts in the instrument currency; base at the command's rate unless stated. `A` = the
trading account, `T` = TREASURY. Fees are in the account's currency (USD for the US set).

| event | lines | idem_key |
|---|---|---|
| Initial capital / deposit | T: Dr 1010 / Cr 3000 | `capital:initial` / `deposit:{request_id}` |
| Withdrawal (refused if T settled USD < amount) | T: Dr 3000 / Cr 1010 | `withdraw:{request_id}` |
| Transfer T → A (manual or SWEEP_IN), one entry four lines | T: Dr 3100 / Cr 1010 ; A: Dr 1010 / Cr 3100 | `xfer:{transfer_id}` |
| Transfer A → T (manual or SWEEP_OUT) | A: Dr 3100 / Cr 1010 ; T: Dr 1010 / Cr 3100 | `xfer:{transfer_id}` |
| Equity/ETF BUY fill | Dr 1100 gross / Dr 5000 fee / Cr 2100 gross+fee ; settlement row −(gross+fee) due T+1 | `fill:{fill_id}` |
| Equity SELL long (FIFO) | Dr 1200 gross−fee / Dr 5000 fee / Cr 1100 cost relieved / Cr(Dr) 4000 realised | `fill:{fill_id}` |
| Equity SELL_SHORT (opens SHORT lot) | Dr 1200 gross−fee / Dr 5000 fee / Cr 2600 gross | `fill:{fill_id}` |
| Equity BUY_TO_COVER (FIFO on SHORT lots) | Dr 2600 proceeds relieved / Dr 5000 fee / Cr 2100 gross+fee / Cr(Dr) 4000 realised | `fill:{fill_id}` |
| Settlement of a receivable | Dr 1010 / Cr 1200 (or 1250) | `settle:{settle_id}` |
| Settlement of a payable | Dr 2100 (or 2350) / Cr 1010 (fund() first if 1010 would go below zero) | `settle:{settle_id}` |
| Option BTO | Dr 1700 premium / Dr 5000 fee / Cr 2100 premium+fee (T+1) | `fill:{fill_id}` |
| Option STO | Dr 1200 premium−fee / Dr 5000 fee / Cr 2900 premium | `fill:{fill_id}` |
| Option STC / BTC | FIFO against 1700 / 2900 as for equities; realised to 4000 | `fill:{fill_id}` |
| Option expiry OTM | long: Dr 4000 / Cr 1700 cost ; short: Dr 2900 premium / Cr 4000 | `fill:{fill_id}` (fill origin EXPIRY at price 0) |
| Long call exercise | Dr 1100 K×100×n + premium / Cr 1700 premium / Cr 2100 K×100×n (T+1) | `fill:{fill_id}` (origin EXERCISE) |
| Long put exercise (long shares) | Dr 1200 K×100×n / Cr 1700 premium / Cr 1100 cost relieved / Cr(Dr) 4000 = K×100×n − premium − cost | same |
| Long put exercise (no shares) | needs a borrow: Dr 1200 K×100×n / Cr 1700 premium / Cr 2600 K×100×n − premium (SHORT lot at K − premium/100); without a borrow the job SKIPS with Attention `EXERCISE_NEEDS_BORROW` (never cash-settled, never invented) | same |
| Short put assignment | Dr 1100 K×100×n − premium / Dr 2900 premium / Cr 2100 K×100×n | `fill:{fill_id}` (origin ASSIGNMENT) |
| Short call assignment (long shares) | Dr 1200 K×100×n / Dr 2900 premium / Cr 1100 cost relieved / 4000 = K×100×n + premium − cost | same |
| Short call assignment (no shares) | opens a short at K + premium/100 per share: Dr 1200 / Dr 2900 / Cr 2600 — requires a borrow opened by the job through `lending.open_borrow`; if the locate fails the job SKIPS with Attention `ASSIGNMENT_NEEDS_BORROW` and retries each tick; the owner may cover manually by buying shares first (disclosed) | same |
| Index option cash settlement | long: Dr 1200 intrinsic×100×n / Cr 1700 cost / 4000 diff ; short: Dr 2900 premium / Cr 2100 intrinsic×100×n / 4000 diff | `fill:{fill_id}` |
| Futures fill | Dr 5000 fee / Cr 1010 fee (same day); IM: Dr 1300 / Cr 1010 (per contract) on opening, Dr 1010 / Cr 1300 on closing | `fill:{fill_id}`, `im:{fill_id}` |
| Futures variation margin | gain: Dr 1010 / Cr 4400 ; loss: Dr 4400 / Cr 1010 (fund() first; overdraft only with an open FUNDING_SHORTFALL item) | `vm:{account}:{instrument}:{date}` |
| Futures final settlement | the day's VM then a FUTURES_FINAL fill at the settle (qty → 0, no further P&L), IM released | `vm:…`, `fill:{fill_id}`, `im:{fill_id}` |
| Option margin mark | requirement up: Dr 1310 / Cr 1010 (fund() first) ; down: Dr 1010 / Cr 1310 | `omargin:{account}:{ts}` |
| Borrow open | A: Dr 1400 (instrument, memo borrow_id) collateral / Cr 1010 (fund() first) | `collat:{borrow_id}:open` |
| Collateral mark (daily, both ways) | call: Dr 1400 / Cr 1010 ; return: Dr 1010 / Cr 1400 | `cmark:{borrow_id}:{date}` |
| Borrow fee accrual (night d) | Dr 5300 / Cr 2310 | `accr:bfee:{borrow_id}:{d}` |
| Rebate accrual (night d) | Dr 1410 / Cr 4350 | `accr:rebate:{borrow_id}:{d}` |
| Fee / rebate settlement (1st of month, and on return) | Dr 2310 / Cr 1010 ; Dr 1010 / Cr 1410 | `pay:bfee:{borrow_id}:{yyyy-mm|return}` |
| Borrow return | Dr 1010 collateral returned / Cr 1400 | `collat:{borrow_id}:return:{fill_id|manual}` |
| Treasury cash interest (night d) | T: Dr 1230 / Cr 4300 | `accr:cashint:USD:{d}` |
| Cash interest paid (1st of month) | T: Dr 1010 / Cr 1230 | `pay:cashint:{yyyy-mm}` |
| Currency loan draw | A: Dr 1010:ccy / Cr 2750:ccy | `loan:{loan_id}:draw` |
| Loan interest accrual (night d) | Dr 5150:ccy / Cr 2755:ccy (base at that command's rate) | `accr:loan:{loan_id}:{d}` |
| Loan repayment | Dr 2755 accrued / Dr 2750 principal / Cr 1010:ccy (refused unless settled ccy cash ≥ amount) | `loan:{loan_id}:repay:{request_id}` |
| PB financing draw / accrual / pay / repay | Dr 1010 / Cr 2700 ; Dr 5100 / Cr 2300 ; Dr 2300 / Cr 1010 ; Dr 2700 / Cr 1010 | `pb:{account}:draw:{request_id}`, `accr:pb:{account}:{d}`, `pay:pb:{account}:{yyyy-mm}`, `pb:{account}:repay:{request_id}` |
| FX spot deal (two legs at the deal rate) | USD leg: Dr 1250:USD / Cr 3900:USD ; EUR leg: Dr 3900:EUR / Cr 2350:EUR (both EUR lines `fx_rate` = deal rate) — for a USD→EUR purchase mirror the legs | `fx:{deal_id}` |
| FX spot settlement (T+2) | Dr 1010:buy / Cr 1250:buy ; Dr 2350:sell / Cr 1010:sell (base at the command's rate) | `settle:{settle_id}` per leg |
| Dividend ex-date / pay (§13.15) | long: Dr 1210 / Cr 4200 on the ex-date; Dr 1010 / Cr 1210 on the pay date ; short: Dr 5400 / Cr 2320 ; Dr 2320 / Cr 1010 | `div:{account}:{inst}:{exdate}`, `divpay:{account}:{inst}:{exdate}` |
| Bond buy / accrual / coupon / maturity (Build 2) | Dr 1110 clean / Dr 1220 accrued / Dr 5000 / Cr 2100 ; Dr 1220 / Cr 4300 ; Dr 1010 / Cr 1220 ; Dr 1010 face / Cr 1110 / 4000 | `fill:…`, `accr:bond:…`, `coupon:…`, `fill:…` (MATURITY) |
| PB credit interest (night d) / monthly minimum shortfall (§12.4) | Dr 1230 / Cr 4300 (PB account, positive settled USD) ; Dr 5000 / Cr 1010 (minimum − fees charged in the month, if positive) | `accr:pbcredit:{account}:{d}`, `pay:pbmin:{account}:{yyyy-mm}` |
| Swap booking (§13.11) | IM: Dr 1330 contract / Cr 1010 (`fund()` first); no cost posting — the contract's computed mark is an overlay; termination: Dr 1010 / Cr 4450 (or reverse) for the termination value, IM released | `im:{contract}:open`, `otc:{contract}:terminate`, `im:{contract}:release` |
| Swap VM, CTM (daily) | mark fell by Δ: Dr 1320 / Cr 1010 Δ (`fund()` first); mark rose by Δ: Dr 1010 / Cr 2330 Δ — one entry; when the running balance crosses zero the entry carries two lines pairs (release the held side first, then post the other) | `swapvm:{contract}:{date}` |
| Swap VM, STM (daily) | mark rose: Dr 1010 / Cr 4450 Δ ; fell: Dr 4450 / Cr 1010 Δ (`fund()` first; §11.6 overdraft rule); the contract's mark resets to 0 | `swapvm:{contract}:{date}` |
| IRS coupon accrual (night d) | receive leg: Dr 1225 / Cr 4450 ; pay leg: Dr 4450 / Cr 2340 (cumulative rounding per leg segment) | `accr:swaprcv:{contract}:{d}`, `accr:swappay:{contract}:{d}` |
| IRS payment date | Dr 2340 (pay accrued) / Cr 1225 (receive accrued) / Dr or Cr 1010 net (`fund()` for a net payment) | `swappay:{contract}:{date}` |
| TRS reset (§13.12) | return leg gain: Dr 1010 / Cr 4450 (loss reversed); financing accrued paid: Dr 2340 / Cr 1010; dividend accruals Dr 1225 / Cr 4450 between resets settle with the return leg | `trsreset:{contract}:{date}` |
| XCCY notional exchange | start: Dr 1010:recv_ccy / Cr 2360:recv_ccy and Dr 1360:pay_ccy / Cr 1010:pay_ccy (each leg balanced in its currency at the deal rate) ; maturity: the reverse ; MTM FX reset of the USD leg: Dr 1010:USD / Cr 2360:USD (notional up) or reverse | `xccy:{contract}:start`, `xccy:{contract}:end`, `xccyfx:{contract}:{date}` |
| Private secondary BUY (§13.13) | Dr 1120 gross / Dr 5000 fee (placement) / Cr 2100 gross+fee ; settlement row due trade + settle_days calendar days; lot `lock_until` | `fill:{fill_id}` (origin NEGOTIATED) |
| Private secondary SELL (FIFO) | Dr 1200 gross−fee / Dr 5000 fee / Cr 1120 cost relieved / Cr(Dr) 4000 realised | `fill:{fill_id}` |
| Private credit commit (§13.14) | Dr 1130 principal × issue_price/100 / Cr 1010 (`fund()` first) | `pc:{loan_id}:commit` |
| Private credit coupon accrual / OID accretion (night d) | Dr 1220 / Cr 4300 coupon ; Dr 1130 / Cr 4300 OID (straight-line, cumulative rounding) | `accr:pccpn:{loan_id}:{d}`, `accr:pcoid:{loan_id}:{d}` |
| Private credit quarterly payment / amendment fee / repayment | Dr 1010 / Cr 1220 ; Dr 1010 / Cr 4320 ; Dr 1010 face / Cr 1130 amortised cost / Cr(Dr) 4000 difference | `pcpay:{loan_id}:{date}`, `pcfee:{loan_id}:{request_id}`, `pc:{loan_id}:repay:{request_id}` |

Every entry's `memo` names the economics in words ("BUY 1,000 AAPL @ 339.50, fee 5.00"); accrual memos carry the rate
and its observation date ("SOFR 3.87 % (2026-10-19), ACT/360, night of 2026-10-16").

---

## 6. Accounting read models (`accounting.py`) — exact definitions

All functions read `gl_balances` (fast path, proven equal to `journal_lines` by L3), `v_positions`, `lots`,
`settlements`, `orders`, `borrows`, `loans`, `margin_state`, and a `MarkSet` snapshot. Figures are integer cents in
Python and decimal strings over JSON; every mark-dependent number is serialised as
`{"value": "123.45", "quality": "delayed", "as_of": 1792526401, "source": "Yahoo Finance (up to 15 min delayed)"}`.

### 6.0 Scopes, lifetimes, liquidity (amendment)

Every read model takes a **scope** (§2.9): one account, `"{lifetime_id}:ALL"`, `"ALL"` (the selected book's open
lifetime) or `"ALL_BOOKS"`. Rows are selected through `accounts.lifetime_id`, so an archived lifetime's figures are the
same functions over its CLOSED accounts (frozen at its last marks: the snapshot stored at the reset is served for
mark-dependent fields, never a re-mark). `nav_snapshots` and `pnl()` baselines are per lifetime (`{lifetime}:ALL`).

```
excess_liquidity_c(scope) = treasury_available_c + Σ_accounts max(0, available_c(account, USD))
                          − Σ_futures_positions max(0, MM × |qty| − balance(1300, inst))          (sentence EXCESS_LIQ)
encumbrance_c(scope)      = Σ [1400 + 1300 + 1310 + 1320 + 1330]  (base)                              (sentence ENCUMBRANCE)
```
Both appear on the Cash & financing tab, Treasury page and Risk with the formula on hover (directive §7).

### 6.1 Cash (per account, per currency) — `cash()`, `available()`, `buying_power()`

```
settled_c          = balance(1010)
receivable_c       = balance(1200) + balance(1250)                          # pending in
payable_c          = balance(2100) + balance(2350)                          # pending out
pending_c          = receivable_c − payable_c
held_at_lender_c   = balance(1400)      (display line "held at lender (102 % collateral)"; not spendable)
margin_c           = balance(1300) + balance(1310)
reserved_c         = Σ over WORKING/PARTIAL/WAITING_PARENT BUY-side orders of this account in this currency:
                     remaining × ref_px × multiplier × price_scale + est_fee, ref_px = limit_px for LIMIT/STOP_LIMIT,
                     else last × 1.02 (MARKET/STOP); SELL_SHORT orders add 1.02 × remaining × last (collateral not yet posted)
available_c(settle_date) = settled_c + Σ receivables with due_date ≤ settle_date − payable_c − reserved_c
treasury_available_c = available_c(TREASURY, ccy) − settings.treasury_reserve_c                 (0 for the Treasury itself)
pb_headroom_c      = (0.50 × long market value in the PB account) − balance(2700) if pb_financing else 0  (Reg-T 50 %, disclosed)
buying_power_total = max(0, available_c) + max(0, treasury_available_c) + max(0, pb_headroom_c)
```
Settlement date used by `available_c` = the new order's own settle date (T+1 US; T+2 FX). The ticket shows the three
components and the sentence SWEEP (§1.3). Option margin is already in 1310 (cash moved), so it is not subtracted
again. A negative `available_c` is shown in red and means a funding shortfall is outstanding (§11.6).

### 6.2 Positions and lots — `positions()`, `lots()`

```
qty, cost_c                 from v_positions (qty > 0 long, < 0 short; cost_c = remaining gross cost / proceeds)
avg_px                      = cost_c / (|qty| × multiplier × price_scale)            (price units, 4 dp)
mark                        = marks.mark(instrument)                                   (None -> unavailable)
mv_c  (securities, options) = qty × mark × multiplier × price_scale                   (signed; shorts negative)
mv_c  (futures)             = 0   (margined daily; notional = qty × mark × multiplier shown separately)
unrealised_c (long)         = mv_c − cost_c
unrealised_c (short)        = cost_c(=proceeds) − |mv_c|
unrealised_c (futures)      = qty × (mark − last settle_px) × multiplier      ("open trade equity since the last settlement")
realised_itd_c              = natural(4000, inst) + natural(4400, inst)
income_itd_c                = natural(4200, inst) + natural(4300, inst) + natural(4350, inst)
financing_itd_c             = −(natural(5300, inst) + natural(5400, inst) + natural(5700, inst))
fees_itd_c                  = −natural(5000, inst)
day_pnl_c                   = mv_c − mv_c at the last snapshot (detail_json) − net cash flows of the position since (fills gross+fee, VM, income)
weight                      = mv_c(base) / nav_c(account);  days_held = today − oldest open lot's open date
```
Each `PositionView` carries the full `MarkView` (quality, source, quote_time, delay_label, method, inputs, note) and
`borrow` (id, fee_rate, collateral_c) for shorts, `settle_px` for futures. Lots: `lot_id, side, open date, qty_open,
qty_remaining, unit_cost, cost_c, open_fill_id, lock_until`.

Additional position kinds (amendment): **swaps** (`otc_contracts` OPEN rows; `qty` = notional sign (+ receive / pay
fixed as booked), `cost_c = 0`, `mv_c = mark_c` (CTM: cumulative clean PV; STM: PV since the last settlement),
`unrealised_c = mark_c`, `realised_itd_c = natural(4450, contract)`, `income` = accrued 1225 − 2340, `financing` = 0,
`vm_balance_c`, `im_c`, DV01; class "OTC derivatives"); **private shares** (lots in 1120 with `lock_until`; `mark`
quality `private` with "last round <date>"; class "Private / illiquid"); **private loans** (`private_loans`; `cost_c`
= 1130 balance (amortised cost), `mark` = `pc_mark()` computed, accrued 1220, class "Private credit"). Dividends
receivable (1210) and payments in lieu (2320) show on the position row as `income_pending_c`.

### 6.3 NAV, equity, balance sheet — `nav()`, `balance_sheet()`

```
assets_c(A)      = Σ_ccy fx(ccy) × [1010 + 1200 + 1210 + 1220 + 1225 + 1230 + 1250 + 1300 + 1310 + 1320 + 1330 + 1360 + 1400 + 1410 + 1500]_ccy
                 + Σ long positions mv_c × fx(ccy)                      (replaces 1100/1110/1120/1700 at cost)
                 + Σ private loans pc_mark × fx(ccy)                    (replaces 1130 at amortised cost)
                 + Σ swaps mark_c when positive                         (OTC at market; CTM clean PV / STM PV since last settle)
liabilities_c(A) = Σ_ccy fx(ccy) × [2100 + 2300 + 2310 + 2320 + 2330 + 2340 + 2350 + 2360 + 2500 + 2700 + 2750 + 2755]_ccy
                 + Σ short positions |mv_c| × fx(ccy)                   (replaces 2600/2900 at proceeds)
                 + futures open trade equity when negative (positive goes to assets)
                 + Σ swaps |mark_c| when negative
nav_c(A)         = assets_c − liabilities_c            ("Equity (NAV)" in the UI)
nav_c({lifetime}:ALL) = Σ_A∈lifetime nav_c(A)          (3100 nets out; 3900 nets out in base);  nav_c(ALL_BOOKS) = Σ open lifetimes
quality          = worst mark quality among non-zero positions (delayed > close > computed > private > stale > unavailable)
partial          = any position unavailable -> True, missing = [instrument_ids]; the UI says "NAV excludes N unpriced positions"
```
`balance_sheet(scope)` rows (natural sign, native and USD): cash settled by currency; receivables (sales, FX); accrued
income; securities at market (long) with sub-rows "at cost" and "unrealised"; options purchased at market; futures
margin deposit; option margin deposit; futures open trade equity; collateral posted (held at lender); — payables
(purchases, FX); securities sold short at market; options written at market; accrued expenses; repo; currency loans;
PB financing; — NAV; and the memo block Capital contributed, Treasury advances, FX clearing (0 in base), Retained
results (= NAV − capital − advances), so the owner sees `NAV = capital + advances + retained` tie to the cent.

### 6.4 Unrealised and FX result (computed, never posted)

```
unrealised_c(now) = Σ_positions (mv_native − cost_native) × fx(ccy)                  (price effect at today's rate)
fx_result_c(now)  = Σ_{ccy ≠ USD} [ (Σ_{L ∈ AL(ccy)} amount_c(L)) × fx(ccy) − Σ_{L ∈ AL(ccy)} base_c(L) ]
  AL(ccy) = every journal line in ccy whose gl kind is ASSET or LIABILITY (cash, receivables, payables, loans,
            collateral, margin AND the cost accounts 1100/1110/1700/2600/2900). EQUITY (3000/3100/3900), INCOME and
            EXPENSE lines are excluded. Lines of accounts whose balance is now zero still count (their residual base
            carrying is the FX effect realised while they were open).
```
Why this exact form: each entry balances in base, so Σ base(assets − liabilities) = Σ base(capital + transfers) +
Σ base(income − expense); therefore `NAV = capital + transfers + Σ(realised + income − financing − fees at posting
rates) + unrealised + fx_result` holds to the cent and the period explain below has a zero residual by construction.
The spread paid on an FX deal appears inside `fx_result` on the deal date (−20.00 in §19.6). Convention sentence
FX_RESULT (§1.3).

### 6.5 P&L periods — `pnl(scope, period)`

```
NAV_t       = nav(now)
NAV_0       = nav_snapshots.nav_c at: TODAY -> the last snapshot before today's accounting day; MTD -> the last snapshot of the
              previous month; YTD -> of the previous year; ITD -> 0
flows_c     = Σ capital deposits − withdrawals (3000 movements, Treasury and ALL) ± Σ internal transfers into the account (3100) in (snapshot, now]
pnl_c       = NAV_t − NAV_0 − flows_c
explained   = Δrealised(4000+4400) + Δincome(4200+4300+4350+4500) − Δfinancing(5100+5150+5300+5400+5500+5700) − Δfees(5000)
              + Δunrealised + Δfx_result            (each Δ against the snapshot's stored cumulative)
residual    = pnl_c − explained      -> 0 by construction; integrity M1 (WARN when marks are partial)
```
If the baseline snapshot is missing (first day, snapshot job never ran) the view says `baseline: inception` and sets
`degraded=True`; it never invents a baseline. Consolidated P&L uses the `ALL` snapshot and capital flows only.

### 6.6 The accounting day and snapshots

**Decision:** the accounting day closes at **17:00 ET** (after the US close and at the CME daily break):
`accounting_day(now)` = today before 17:00, tomorrow after. `business_date` on every entry = the accounting day of its
`ts`. The snapshot job (`snap:{account}:{date}`, every calendar day at **17:45 ET**, after VM 16:05–17:05,
collateral marks 17:10 and sweep-out 17:30; catch-up if missed) writes `marks` (CLOSE/SETTLE) for every held
instrument and `nav_snapshots` for every account and `ALL`. "Today" before 17:45 compares with yesterday's snapshot;
after 17:45 with today's. Weekend/holiday snapshots carry the last valid marks labelled `close` (accruals move NAV).
A snapshot made from a close fetched late (daily bars after downtime) is labelled `close (recorded late)`.

### 6.7 Treasury — `treasury()`

```
cash_by_ccy       = Treasury 1010 per currency (+ accrued 1230)
capital_c         = −balance(TREASURY, 3000)                          (natural sign)
advances_c        = +balance(TREASURY, 3100)                          (= Σ over accounts of −balance(A, 3100))
retained_c        = Treasury NAV − capital_c + advances_c             (Treasury's own income: cash interest)
available_c       = Treasury settled USD − reserve (settings.treasury_reserve_c, default 0)
identity (L8)     : Treasury NAV == capital_c + retained_c − advances_c    (to the cent)
encumbrance       = 0 in this build (Treasury holds no positions); shown as such
```

### 6.8 `integrity()` — three sets, two consequences

**Ledger-level set (L) — exact, no marks; failure = error.** Run `scoped` (touched keys) inside every non-light
command before COMMIT (failure rolls the command back); run `ledger` (whole DB) at startup, hourly, after every
lifecycle batch and on demand. A whole-DB failure sets **INTEGRITY_HOLD**: new order submissions are refused with 409
`INTEGRITY_HOLD` until the next clean run; reads, cancels, lifecycle, deposits continue.

| id | check |
|---|---|
| L1 entries_balance | per (entry, currency): Σ amount_c = 0; per entry: Σ base_c = 0 |
| L2 lines_valid | gl in chart; account exists; currency 3 letters; per_instrument codes carry instrument_id; amount_c ≠ 0 |
| L3 gl_balances_match | per (account, gl, ccy, inst): gl_balances.balance_c == Σ journal_lines.amount_c and base_c == Σ base_c; no gl_balances row without lines |
| L4 ledger_identity | per currency: Σ ASSET − Σ LIABILITY − Σ EQUITY − Σ INCOME + Σ EXPENSE == 0 (natural signs) |
| L5 transfers_net_zero | Σ over accounts of balance(3100) == 0 per currency; Σ base_c of 3900 over all accounts == 0 |
| L6 capital_explained | −balance(TREASURY, 3000, USD) == Σ amount_c of COMMITTED commands of kind treasury.init_capital + treasury.deposit − treasury.withdraw (USD) |
| L7 positions_match_fills | per (account, instrument): v_positions.qty == Σ signed fills qty (BUY +, SELL −, all origins) |
| L8 lots_consistent | per lot: qty_open − Σ relief qty == qty_remaining; qty_open × unit_cost × mult × scale − Σ cost_relieved == cost_c (to the cent) |
| L9 cost_accounts_match | per (account, instrument): balance(1100/1110/1700 long) == Σ open LONG lots cost_c; balance(2600/2900) == Σ open SHORT lots cost_c; zero when flat |
| L10 obligations_match | PENDING settlements' Σ amount_c per (account, gl, ccy) == balance of that gl; every TRADE/EXERCISE/ASSIGNMENT fill with settle_date has its settlement rows; SETTLED rows have entry_id |
| L11 no_duplicate_keys | distinct-key counts == row counts for fills, journal_entries, transfers, jobs_done, settlements legs (belt and braces over the UNIQUE indexes) |
| L12 shorts_borrowed | every position with qty < 0 in EQUITY/ETF/ADR/REIT/PREFERRED (BOND Build 2) has Σ OPEN borrows (qty − qty_returned) ≥ \|qty\|; every OPEN borrow has a short position or a `reason` ('pending short sale' set at submit, cleared at fill; 'cover pending settlement') |
| L13 collateral_match | per OPEN borrow: balance(1400, instrument, memo borrow_id) == borrows.collateral_c |
| L14 loans_match | Σ principal of OPEN loans by (kind, ccy) == balances of 2750 / 2700; PB accounts: balance(2700) ≤ pb_limit_c |
| L15 futures_margin | per open futures position: balance(1300, instrument) == im_c × \|qty\|; zero when flat |
| L16 option_margin | per account: balance(1310) == margin_state.options_required_c (0 when no row) |
| L17 orders_consistent | orders.filled_qty == Σ fills qty for the order; FILLED ⇒ filled_qty == qty; WORKING/PARTIAL ⇒ filled_qty < qty |
| L18 cash_floor | balance(1010) ≥ 0 for every (account, ccy), except (a) PB accounts with pb_financing (2700 ≤ pb_limit_c) and (b) an (account, ccy) with an OPEN attention item `FUNDING_SHORTFALL` created by the overdrawing lifecycle posting (§11.6) |
| L19 sweep_linkage | every SWEEP_IN transfer has linked_type/linked_id pointing at an existing fill / settlement / borrow / margin entry and amount_c == shortfall_c recorded on it |
| L20 request_linkage | every fill / transfer / settlement entry_id exists; every entry's command_id exists; every COMMITTED command whose kind posts has ≥ 1 entry |
| L21 settlement_health | no PENDING settlement with due_date < today − 5 business days (the job would be stuck); private settlements use their calendar-day due date |
| L22 otc_margin_match | per OPEN swap: CTM → balance(1320, contract) == max(0, −vm_balance_c) and balance(2330, contract) == max(0, vm_balance_c); STM → both 0; balance(1330, contract) == im_c; TERMINATED/MATURED → all 0 |
| L23 lifetime_frozen | a CLOSED lifetime has no journal line, fill, transfer or order event with ts > lifetimes.ended_ts, and no OPEN order (every open order was CANCELLED "lifetime reset") |
| L24 private_lots | per private-shares position: 1120 == Σ open LONG lots cost_c (as L9); every private fill has origin NEGOTIATED and a settlement row; per OPEN private loan: 1130 == principal × issue_price/100 + Σ OID accreted (accrual_state kind pcoid) |
| L25 books_isolated | no journal entry mixes accounts of two lifetimes (3100 nets to zero per lifetime); Σ capital per lifetime Treasury == stored init + deposits − withdrawals of that lifetime (L6 per lifetime) |

**Mark-dependent set (M) — uses the snapshot; failure = WARN attention, never a hold.**

| id | check |
|---|---|
| M1 pnl_explains | residual of §6.5 == 0 for TODAY and ITD, every account and ALL (skipped with note when NAV is partial) |
| M2 balance_sheet_ties | assets − liabilities == nav for every scope |
| M3 treasury_identity | §6.7 (marks enter only through fx of foreign Treasury cash) |
| M4 snapshot_continuity | for consecutive snapshots: nav_t − nav_{t−1} − flows_day == pnl_day as recorded (a mismatch means the ledger changed retroactively) |
| M5 snapshot_chain | a snapshot exists for every calendar day since inception (gaps reported) |

Report shape: `{ok, hold, checked_at, mode, lifetime_id, problems: [{id, severity, scope, expected, actual, detail}], ms}`.
The scoped run is O(touched keys) (indexed lookups; typically < 5 ms); the whole-DB run (~100–300 ms on a year of data)
runs on a reader connection and records `meta.integrity_json`. Integrity runs **per lifetime** (amendment): the whole-DB
run covers every OPEN lifetime (L23/L25 cover the CLOSED ones cheaply); `book.reset_lifetime` runs `integrity("full",
lifetime_id=…)` once on the closing lifetime and stores the report on `lifetimes.integrity_json`. INTEGRITY_HOLD is per
lifetime (a failing book blocks only its own submissions).

### 6.9 Performance statistics — `performance.statistics()` (B16; directive §11; per lifetime)

Inputs: the lifetime's `{lifetime}:ALL` `nav_snapshots` in date order (≥ 2 rows, else `{status: "insufficient
history", needed: 2}`), SPY daily closes from `marks` (CLOSE rows; recorded by MARKS_RECORD, catch-up from
`daily()`), SOFR (RateStore). Flows are removed with `flows_day_c` (capital deposits/withdrawals of the lifetime):

```
r_t      = (nav_t − flows_day_t) / nav_{t−1} − 1                  daily return, flows excluded (t over consecutive snapshot days)
m_t      = spy_close_t / spy_close_{t−1} − 1                      benchmark return on the same dates (missing close -> the pair is skipped and counted)
rf       = SOFR / 360                                              daily policy rate
ann_ret  = mean(r) × 252;     vol = stdev(r, ddof=1) × √252       (n ≥ 2 returns; vol needs n ≥ 2)
sharpe   = (mean(r) − rf) / stdev(r) × √252
sortino  = (mean(r) − rf) / sqrt(mean(min(r − rf, 0)²)) × √252    (downside deviation over ALL days, zero when none below rf -> "n/a")
beta     = cov(r, m, ddof=1) / var(m, ddof=1);   alpha = (mean(r) − rf − beta × (mean(m) − rf)) × 252   (daily regression on SPY closes)
max_dd   = max_t (peak_t − idx_t) / peak_t  on the flow-adjusted index idx_0 = 1, idx_t = idx_{t−1} × (1 + r_t)
hit_rate = count(r > 0) / count(r)
monthly  = per calendar month: (nav_end − nav_start − Σ flows in month) / nav_start, nav_start = the last snapshot of the previous month (inception: the first snapshot)
```
Ratios are floats rounded to 4 dp in JSON (they are not money); NAVs and flows are cents. Shown on the Portfolio →
Performance tab with the sentence PERF and the data window (first/last snapshot, n). §19.20 pins every figure.

---

## 7. Instrument master and symbol mapping (`instruments.py`)

### 7.1 Identifiers (Decision)

| kind | `instrument_id` | `yahoo_symbol` | `cboe_symbol` | session_group / settle_days / account_kinds |
|---|---|---|---|---|
| EQUITY / ETF / ADR / REIT / PREFERRED (US listed) | Yahoo ticker as spelled (`AAPL`, `BRK-B`, `BAC-PL`) | same | ticker with `-` → `.` (`BRK.B`) | US_EQUITY / 1 / US_EXCHANGE, PB |
| Foreign listing (Build 2) | Yahoo symbol with suffix (`VOD.L`, `SAP.DE`, `7203.T`) | same | — | FOREIGN:{exchange} / per venue (LSE 2, XETRA 2, TSE 2, HKG 2, TSX 1, ASX 2, default 2) / FOREIGN_FX; `price_scale 0.01` for `.L` pence |
| OPTION | OCC form `AAPL261120C00330000` (root + yymmdd + C/P + strike×1000 as 8 digits) | — | the chain's `option` field | US_EQUITY_OPTIONS (equity/ETF: AMERICAN, PHYSICAL, multiplier 100) or US_INDEX_OPTIONS (`SPX`, `SPXW`, `NDX`, `RUT`, `VIX`: EUROPEAN, CASH) / 1 / US_EXCHANGE, PB |
| FUTURE | `<root><M><yy>` (`ESZ26`, `CLX26`) | `<id>.<suffix>` with suffix from `FUTURES_SPECS` (CME / CBT / NYM / CMX): `ESZ26.CME`, `CLX26.NYM`, `GCZ26.CMX`, `ZNZ26.CBT` | — | the root's group / 0 (margined) / US_EXCHANGE, PB |
| BOND (Build 2) | `UST-<coupon>-<yyyymmdd>` (`UST-4.250-20360815`), bills `UST-B-<yyyymmdd>`; corporates = universe id (`JPM-29`) | — | — | US_BONDS / 1 / DEBT |
| FX | market convention `EURUSD`, `USDJPY`, `GBPUSD` (25 currencies of `universe.fx`) | `EURUSD=X`; `JPY=X` with `yahoo_invert=1` (Yahoo quotes JPY per USD) | — | FX_SPOT / 2 / FOREIGN_FX, CREDIT (convert), TREASURY (convert), PB |
| INDEX (reference only) | `SPX`, `NDX`, `RUT`, `VIX`, `DJX`, `DXY` | `^GSPC`, `^NDX`, `^RUT`, `^VIX`, `^DJI`, `DX-Y.NYB` | `_SPX`, `_NDX`, `_RUT`, `_VIX`, `_DJX` | INDEX / — / none (underlying of index options) |
| YIELD (reference) | `^IRX`, `^FVX`, `^TNX`, `^TYX` | same | — | — / — / none |
| PRIVATE (§13.13) | `PRV-<slug>` (`PRV-ANTHROPIC`, `PRV-OPENAI`), from `private_companies.json`; owner-added companies `PRV-<slug>` too | — (a company that lists gets `ticker` and its mark switches to the listed instrument from the listing date) | — | PRIVATE_MARKETS (no session; negotiated) / calendar days per trade / PRIVATE |
| SWAP (§13.11) | `SWP-000001` (sequence; one per booked contract) | — | — | — / — / PB (the contract's own account) |

**Verify at the WP-01 smoke test:** per-contract Yahoo futures symbols beyond the probe's `CLX26.NYM` (`ESZ26.CME`,
`ZNZ26.CBT`, `6EZ26.CME`, `GCZ26.CMX`, `ZCZ26.CBT`). A root whose symbols Yahoo rejects falls back to the continuous
`<root>=F` **only for the front month**, with `method = front_month_continuous`, `support = LIMITED` and the label
"front-month continuous (reference)"; deferred months of that root are `UNSUPPORTED` (never priced off the front).

### 7.2 Tradeability by account kind (`account_kinds`, checked by `orders.validate`)

| account kind | may trade |
|---|---|
| US_EXCHANGE | US EQUITY/ETF/ADR/REIT/PREFERRED, OPTION on them and on index underlyings, FUTURE |
| DEBT | BOND (Build 2) |
| CREDIT | no listed instruments; cash loans, borrows view, FX convert; **private credit** loans (§13.14) |
| FOREIGN_FX | FX spot (Build 1); foreign listings, FX forwards (Build 2) |
| PRIVATE (§13.13) | PRIVATE company shares at negotiated prices; exists only in a lifetime that passes the QP gate |
| PB (`PB_*`) | everything US_EXCHANGE may trade, at the PB schedule; FX convert; **OTC swaps** IRS / TRS / XCCY (§13.11–13.12) when the lifetime is PB/OTC-eligible; OTC options and forwards Build 2 |
| TREASURY | nothing (cash only; FX convert allowed for funding) |

A PB account can exist only in a lifetime that passed `books.eligibility().pb_otc` at the time `book.onboard_pb` ran
(agreements recorded); an OTC booking re-checks the ECP floor only (relationships survive a later NAV fall; sentence
ELIGIBILITY). The Personal book below USD 10,000,000 gets `NOT_ELIGIBLE` with the ECP wording on both.

### 7.3 Resolve by symbol (`GET /api/instruments/resolve?symbol=`)

An unknown ticker typed into search is resolved by the quote worker (never on the request thread): Yahoo chart meta
gives `instrumentType`, `currency`, `exchangeName`, `fullExchangeName`, `exchangeTimezoneName`, `currentTradingPeriod`.
US exchanges (`NMS`, `NYQ`, `PCX`, `NGM`, `ASE`, `BTS`, `NCM`) → kind from `instrumentType` (EQUITY / ETF; ADR when
the universe says so), `US_EQUITY`, settle 1, `support FULL`. Any other exchange → Build 2 (`support UNSUPPORTED`,
message "foreign listings are not in this build"). `CRYPTOCURRENCY`, `MUTUALFUND` → UNSUPPORTED. The resolve answers
202 `{status: "resolving"}` until the worker's `instruments.upsert` command lands; then the row is returned.

### 7.4 Seeding (`InstrumentMaster.seed`, first run)

From `finsim/finsim/data/universe.json`: `equities` (827 rows; `asset_class` EQUITY/ETF/ADR/REIT/PREFERRED; CRYPTO
skipped; `country` is informative only — all are Yahoo-quoted US listings) with `spec_json = {adv, beta,
shares_outstanding, cik, dividend_yield, sector}`; `fx` (25 currencies → pairs vs USD, `yahoo_invert` for JPY and the
other "per USD" quotes); `indices` and the four `rates` as reference rows; **futures**: for each of the 20 roots the
next 4 listed contract months (§12.3) with `last_trade` from the spec rule. Bonds (`universe.bonds`, 170 rows) are
loaded with `support = LIMITED` and `status ACTIVE` but `account_kinds = []` until Build 2 enables DEBT. Options are
materialised on first sight from a Cboe chain row (`ensure_option`).

### 7.5 `support` and `status`

`FULL` (Yahoo/Cboe quote, executable), `LIMITED` (shown with the limitation text; executable only if the limitation is
about data completeness, e.g. front-month continuous), `COMPUTED` (model mark, never executable: bonds, forwards),
`UNSUPPORTED` (listed so search can say "not in this build"). `status`: ACTIVE / EXPIRED (options past expiry, futures
past last trade) / DELISTED (Yahoo 404 for 5 consecutive business days → Attention, position still shown).

---

## 8. Quotes: record, freshness, providers, worker, computed marks

### 8.1 Record (`quotes.QuoteRecord`, §2.11)

- `quote_time` = the provider's own timestamp: Yahoo `meta.regularMarketTime`; Cboe underlying `last_trade_time`
  (naive ET → epoch via `NY`); Treasury/FRED observations: the observation date at 17:00 ET.
- `bar` = the latest **completed** 1-minute bar: the last chart bar with `t ≤ fetched_at − 60`, volume may be 0 or
  null; `bars_tail` = the last 5 bars (sparkline), never more.
- `delay_s` = 900 for Yahoo and Cboe (label "up to 15 min delayed"); 0 for computed inputs' own records (their quality
  is derived); `delay_label` for rates "daily (prior business day)".
- `provider_session` = Yahoo `currentTradingPeriod.regular` (start, end) — the provider's own view of today's regular
  session, used by `is_executable`.
- `error` keeps the previous values and the last error text (status only, never the URL).
- Chain contracts are separate `QuoteRecord`s with `method = chain`, `provider = cboe`, `quote_time` = the chain's
  **underlying** `last_trade_time` (the chain-level `timestamp` is null in the probe and a contract's own
  `last_trade_time` can be months old); `bid/ask/bid_size/ask_size`, `mid` when `bid > 0 and ask ≥ bid`, `inputs = {iv,
  delta, gamma, theta, vega, oi, volume, last, last_time}`.

### 8.2 Freshness (`freshness()`), exactly

```
if rec is None or (rec.last is None and rec.mid is None):             -> "unavailable"
if rec.method == "private_round":                                      -> "private"    (valuation only; never stale, never executable)
if rec.method in ("curve", "cip", "model", "swap_model", "pc_model"):
    if any input quality in ("stale", "unavailable")                   -> "stale"
    else                                                               -> "computed"
if not session.open:                                                   -> "close"      (valuation ok; execution blocked by the session)
# session open:
if rec.quote_time is None                                              -> "stale"
if now − rec.quote_time > rec.delay_s + STALE_GRACE_S (300)            -> "stale"
if now − rec.fetched_at > FETCH_GRACE_S (300)                          -> "stale"      (nothing fetched for 5 min)
if rec.error is not None and now − rec.fetched_at > ERROR_AGE_S (60)   -> "stale"      (a failing provider ages the record quickly)
if rec.delay_s == 0 and rec.method == "direct"                         -> "live"       (reserved; no provider produces it)
else                                                                   -> "delayed"
```
`is_executable(rec, session, now)` = `freshness() in ("live", "delayed") and session.open`, and additionally:
`rec.quote_time ≥ session.opened_at` (the print belongs to the current session, not yesterday's — guards a DAY order
after a laptop sleep) and, when `provider_session` is present, `now ≤ provider_session.end + 60` (reason
`PROVIDER_SESSION_ENDED`, guards an early close the local table missed). Reasons: `NO_QUOTE`, `STALE`,
`SESSION_CLOSED`, `COMPUTED_NOT_EXECUTABLE`, `QUOTE_FROM_PREVIOUS_SESSION`, `PROVIDER_SESSION_ENDED`. Options are
executable only when the contract record has `bid > 0 and ask ≥ bid`.

Valuation never blocks: a `stale`/`close` record is still the last valid mark and is shown with its time; `unavailable`
shows a dash and the position's MV is reported as unavailable (NAV `partial`).

### 8.3 HTTP plumbing and UA policy (`quotes/http.py`)

```python
class FetchError(Exception): status: int | None; body: bytes; retryable: bool
def http_get(url: str, ua: str, timeout: float = 20.0, max_bytes: int = MAX_BYTES, accept: str = "application/json") -> bytes
    # copied from finsim2.data._http_get: gzip, size cap, FetchError with status; messages never contain the URL or a key
def ua_for(host: str) -> str      # *.gov, cdn.finra.org -> GOV_UA; *.finance.yahoo.com -> BROWSER_UA; everything else -> TERMINAL_UA
class RateLimiter: def __init__(self, rps: float); def wait(self) -> None        # token bucket, thread-safe
class CircuitBreaker: def allow(self, provider) -> bool; def record(self, provider, ok: bool, status: int | None) -> None
    # 429 -> down 60 s; 5 consecutive failures -> down 120 s, doubling to 10 min; success resets
class ProviderHealth: snapshot() -> dict; persisted through the light command provider_health.update every 60 s
```
Rules: never a crumb, never Nasdaq Trader, never Stooq; a browser UA for Yahoo only; `.gov` and FINRA get `GOV_UA`;
Cboe and Frankfurter get the honest `TERMINAL_UA`. **If cdn.cboe.com answers 403 to the honest UA, options become
`computed`-only (model marks, no execution, hedge candidates priced by model and labelled) and `/api/health.providers.cboe`
says `refused_ua` — never a bypass; the owner decides.** The first task of WP-01 is the smoke probe that establishes this
(`python -m finsim.terminal.smoke`).

### 8.4 Providers (`quotes/providers.py`)

```python
class YahooChart:          # BROWSER_UA; hosts query1 then query2; 3 attempts per host, backoff 1/2/4 s on 429/5xx/timeout; 2 rps
    def intraday(self, symbol: str) -> YahooIntraday    # /v8/finance/chart/{sym}?range=1d&interval=1m -> meta + bars (last 5 kept)
    def daily(self, symbol: str, days: int = 10) -> list[DailyBar]   # ?range=1mo&interval=1d (catch-up closes); DailyBar(date, o, h, l, c, v)
    def meta(self, symbol: str) -> dict                 # resolve
class CboeChain:           # TERMINAL_UA; 1 rps; timeout 60 s; https://cdn.cboe.com/api/global/delayed_quotes/options/{SYM}.json
    def chain(self, cboe_symbol: str) -> ChainSnapshot  # underlying {last=current_price, bid, ask, bid_size, ask_size, last_trade_time, prev_close, iv30}
                                                        # contracts: list[ChainRow(occ, expiry, right, strike, bid, ask, bid_size, ask_size, last, last_time, iv, delta, gamma, theta, vega, oi, volume)]
                                                        # reduced at parse: expiries <= CHAIN_MAX_DAYS, strikes within ±35 % of spot; iv normalised (percent -> decimal when the median > 3)
class TreasuryCurve:       # GOV_UA; timeout 45 s; XML ...pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value_month=YYYYMM
    def latest(self) -> ParCurve                        # ParCurve(as_of: date, points: {tenor_years: Decimal pct}) tenors 1M..30Y
class FredSeries:          # GOV_UA; fredgraph.csv?id=...; blank values skipped
    def latest(self, series_id: str, lookback_days: int = 60) -> list[tuple[date, Decimal]]
class Frankfurter:         # TERMINAL_UA; daily ECB reference rates; fallback FX only (quality "close", "ECB reference")
    def latest(self) -> dict[str, Decimal]
class FinraShortVolume:    # GOV_UA; cdn.finra.org daily pipe file; symbol -> (short_volume, total_volume)
    def daily(self, d: date) -> dict[str, tuple[int, int]]
class FakeProvider:        # tests and --offline: set(symbol, last=..., quote_time=..., bar=(t, v), bid=, ask=), set_chain(...), set_rate(...)
```
Parsing follows `finsim2/data/yahoo.py::parse_chart` and `finsim2/data/cboe.py::contracts` (copied): payloads are
untrusted; numbers go through a `num()` that returns `None` on anything odd; OCC symbols by the fixed pattern. JSON of
the 13 MB `_SPX` chain is parsed and the dict released immediately; the reduced rows are ~150 B each.

### 8.5 Cache, chains and the worker (`quotes/cache.py`, `quotes/worker.py`)

```python
class QuoteCache:
    def get(self, instrument_id) -> QuoteRecord | None             # quality recomputed at read with the current session
    def get_many(self, ids) -> dict[str, QuoteRecord]; def put(self, rec) -> list[str]   # returns changed ids
    def subscribe(self, ids, ttl_s=90, priority="WATCH"); def pin(self, ids, priority); def unpin(self, ids)
    def due(self, now) -> list[tuple[str, str]]; def load_persisted(self) -> int; def persist(self) -> int
    def snapshot(self, ids) -> dict[str, QuoteRecord]              # deep copies for a command
class ChainCache:                                                   # LRU of 2 reduced chains; single-flight per underlying
    def get(self, underlying_id, max_age_s=90) -> ChainSnapshot | None
    def request(self, underlying_id) -> Future                       # one in-flight fetch per underlying shared by the chain page and the hedge panel
class QuoteWorker(threading.Thread):
    def __init__(self, cache, chains, instruments, providers, sessions, bus, matcher, clock, log, fetch_threads=2)
    def run(self)        # every 1 s: due ids -> group by provider -> fetch on the pool -> cache.put -> matcher.on_quotes(changed)
    def stop(self, timeout=3.0); def stats(self) -> dict
class RatesRefresher:    # part of the worker loop: curve daily after 18:00 ET (+ at startup if missing today), FRED every 6 h, FINRA after 18:00 ET, Frankfurter daily
```
Scheduling (`QUOTE_INTERVALS`): an instrument's interval is the smallest of its pins — ORDER 15 s (a working order
exists), HELD 30 s, WATCH/PAGE 60/30 s (UI subscriptions, TTL 90 s after the last poll) — and CLOSED 300 s whenever
its session is closed (keeps `prev_close` and the last close honest, catches late prints). Chains: CHAIN 90 s while a
chain is on screen or an option position/order exists for that underlying; `_SPX` only on demand. Budget: Yahoo
≤ 2 rps, Cboe ≤ 1 rps; 60 held/ordered + 200 watched names ≈ 1 request/s. A symbol answering 404/400 is
`unavailable` with the error and retried hourly. Memory: 2,000 records ≈ 2 MB; two reduced chains ≈ 1.2 MB; the SPX
parse peaks ≈ 120 MB for a second and chain fetches are serialised (one at a time, shared future), so two never
overlap. The cache is persisted (`quotes.persist_batch`, light) every 60 s and at shutdown so a restart starts from the
last marks (they come back as `close`/`stale`, never `delayed`).

**Matcher hook:** after each batch the worker calls `matcher.on_quotes(changed_ids)` on its own thread; the matcher
reads open orders (reader connection), evaluates with the pure rules (§10.5), and calls `bus.execute(order.match …)`
per decision. The writer lock is never held during network I/O.

### 8.6 Computed marks and the mark provider (`marks.py`, `quotes/rates.py`)

```python
class MarkProvider:
    def __init__(self, cache, chains, instruments, curve: CurveStore, rates: RateStore, sessions, clock)
    def mark_for(self, inst: Instrument, at: int | None = None) -> MarkView
    def snapshot(self, ids: Iterable[str] | None = None) -> MarkSet        # immutable; fx() for every currency seen
    def bond_price(self, inst, curve, shifts, oas_bps, asof) -> tuple[Decimal, dict]     # Build 2
    def fx_forward(self, pair, value_date, at=None) -> tuple[Decimal, dict]              # Build 2
    def option_model(self, inst, under_rec, chain) -> tuple[Decimal, dict]
class CurveStore: latest() -> ParCurve | None; put(curve); as_of() -> date | None; intraday_shifts(cache) -> dict[Decimal, Decimal]
class RateStore:  latest(series_id) -> tuple[date, Decimal] | None; policy_rate(ccy) -> tuple[date, Decimal] | None; sofr() -> tuple[date, Decimal] | None
```

| kind | mark | quality |
|---|---|---|
| equity / ETF / ADR / REIT / preferred / index / future / FX pair | Yahoo `last` × price_scale (FX: inverted when `yahoo_invert`) | freshness() |
| option | Cboe `mid` when `bid > 0 and ask ≥ bid` (liquidation value bid/ask also carried); else `option_model` | delayed / computed |
| FX for `fx(ccy)` | Yahoo pair; else Frankfurter daily (`close`, "ECB reference"); USD = 1 | per source |
| Treasury benchmark (Build 2) | par yield interpolated linearly in maturity on the latest curve + intraday shift from `^IRX/^FVX/^TNX/^TYX` vs their prev_close; clean price = PV of remaining semi-annual coupons + principal at y/2 compounding with the street fractional first period; accrued ACT/ACT; dirty = clean + accrued | computed (inputs: curve as_of, each yield's quote_time) |
| corporate (Build 2) | same with y += FRED OAS of the rating bucket (`BAMLC0A1CAAA … BAMLH0A3HYC`) | computed |
| FX forward (Build 2) | CIP `F = S × (1 + r_quote × d/B_quote) / (1 + r_base × d/B_base)`, policy index per currency (`tables.POLICY_INDEX`), ACT/360 (GBP ACT/365) | computed |
| cash loan / PB balance | principal + accrued (ledger) | n/a |
| private company shares (§13.13) | the latest `private_rounds.price_per_share` (else `post_money_c × owned fraction` when only the valuation is known, else unavailable); method `private_round`; note "last round <date> (<round_type>)"; a PUBLIC company → its listed instrument's mark from the listing date | private |
| IRS (§13.11) | clean PV from the latest par curve (discount factors bootstrapped from par yields, semi-annual; linear in maturity) and the booked fixings; method `swap_model`; inputs {curve as_of, fixings, dealt rate} | computed |
| TRS (§13.12) | `(S_now − S_reset) × units` from the underlying's mark (its freshness is the input quality) minus nothing (financing is accrued in the ledger) | computed (stale when the underlying is stale) |
| XCCY | the two legs as IRS legs in their currencies, converted at `fx()`, plus the notional FX effect since the last reset | computed |
| private loan (§13.14) | `pc_model`: `amortised_cost × (1 − ΔOAS × T_remaining)` with `ΔOAS = (OAS_now − OAS_issue)` of FRED `BAMLH0A0HYM2` in decimal and `T_remaining` in years (ACT/365): a first-order spread-duration adjustment of the amortised cost (equals it when the spread is unchanged or unobserved); inputs {OAS as-of both dates, days remaining} | computed |

`option_model`: Black-Scholes-Merton (European) / CRR 60-step (American) from `finsim.engines.options_pricing` with
S = underlying last, K, T = days/365, r = `^IRX`/100 (else the curve 3M), q = `dividend_yield` from the master, sigma =
IV interpolated across the two nearest quoted strikes of the same expiry (Cboe `iv`), else the chain `iv30`, else FRED
`VIXCLS`/100 for index/ETF underlyings; labelled `computed`, never executable, shown in italics with the inputs. A
missing rate is `None` and the dependent mark is `unavailable` with the note "SOFR unavailable since <date>"; nothing is
defaulted to a number.

---

## 9. Sessions, holidays, early closes (`sessions.py`, `tables.SESSIONS`, New York time)

| group | regular session | daily break | holidays | early closes | DAY expiry | futures settle time |
|---|---|---|---|---|---|---|
| US_EQUITY | Mon–Fri 09:30–16:00 | — | NYSE file | 13:00 on file dates | session close | — |
| US_EQUITY_OPTIONS | 09:30–16:00 | — | NYSE | 13:00 | close | — |
| US_INDEX_OPTIONS | 09:30–16:15 | — | NYSE | 13:15 | close | — |
| US_BONDS (Build 2) | 08:00–17:00 | — | NYSE (SIFMA approximated, disclosed) | 14:00 on NYSE early-close dates | 17:00 | — |
| CME_EQUITY_INDEX (ES NQ YM RTY) | Sun 18:00 → Fri 17:00 | 17:00–18:00 Mon–Thu | full closure Good Friday, Christmas, New Year's Day; other NYSE holidays: early close 13:00 then reopen 18:00 (approximation, disclosed) | per rule | 17:00 of the trade date (13:00 on early-close days) | 16:00 |
| CME_RATES (ZB ZN ZF ZT) | same | same | same | same | same | 16:00 |
| NYMEX_ENERGY (CL BZ NG) | same | same | same | same | same | 14:30 |
| COMEX_METALS (GC SI HG) | same | same | same | same | same | 14:30 |
| CBOT_GRAINS (ZC ZW ZS) | Sun–Thu 20:00 → next day 08:45, and Mon–Fri 09:30–14:20 | 08:45–09:30, 14:20–20:00 | same | 13:05 on NYSE early-close dates (approximation) | 14:20 | 14:20 |
| CME_FX (6E 6J 6B) | Sun 18:00 → Fri 17:00 | 17:00–18:00 | same | same | 17:00 | 16:00 |
| FX_SPOT | Sun 17:00 → Fri 17:00 continuous | — | 1 Jan and 25 Dec closed (assumption); value dates skip weekends and US holidays, other centres' holidays not modelled (disclosed) | — | 17:00 | — |
| FOREIGN:{exchange} (Build 2) | from Yahoo `currentTradingPeriod.regular` in `exchangeTimezoneName`, refreshed daily | — | implied (no period = closed) | implied | that session's end | — |

`day_order_expiry(group, at)` = `closes_at` of the current session if open, else of the next session (so a DAY order
placed Tue 17:30 expires Wed 16:00, not Tue). `trade_date` for CME groups after 18:00 ET = the next business day. The
settings key `session_overrides` (`{"2026-12-24": {"CME_EQUITY_INDEX": "13:00"}}`) lets the owner add a closure or
early close without a code change; `/api/sessions` shows the source of every rule and the sentence CME_CAL.

---

## 10. Orders: request, validation, preview, state machine, matching, funding, hedges

### 10.1 Order request (one leg; packages are one primary + hedge legs)

```json
{"request_id": "ui:…", "preview_id": "pv:…", "account_id": "US_EXCHANGE", "instrument_id": "AAPL",
 "side": "BUY", "qty": 1000, "order_type": "LIMIT", "limit_px": "339.50", "stop_px": null, "tif": "DAY",
 "borrow": true, "hedge": {"candidate_id": "PUT_7PCT", "ratio": "0.01", "edits": {"strike": "315", "expiry": "2027-01-15"}},
 "confirm_large": false}
```
Sides: cash instruments `BUY / SELL / SELL_SHORT / BUY_TO_COVER`; options `BTO / STO / BTC / STC`; futures `BUY /
SELL`. The server derives the position effect; a `SELL` larger than the long position is rejected (`OVERSELL`: "sell
300 and SELL_SHORT 100 with a borrow"). `borrow` is implied true for `SELL_SHORT`. `order_type` also accepts
`TRAILING_STOP` with `trail_amt` (price units) **or** `trail_pct` (percent, 2 dp) — exactly one (§10.5 step 8a). A
strategy package (§13.16) is submitted as `{"package": {"strategy_key": "IRON_CONDOR", "units": 1000, "tenor_months":
3, "strikes": {"1": "-7"}}}` instead of a single leg; the server resolves the legs (`strategies.package_legs`) and sets
`fill_mode = AON` when every leg is an option on one underlying, else `SEQUENTIAL` with the stock/futures leg as the
primary.

### 10.2 Validation (`orders.validate`) — codes

| code | http | rule |
|---|---|---|
| BAD_REQUEST_ID | 400 | missing or malformed request_id |
| ACCOUNT_CLOSED / ACCOUNT_UNKNOWN | 400 | account missing or not OPEN |
| INSTRUMENT_UNKNOWN / NOT_TRADEABLE_HERE | 400 | not in master; `account_kinds` excludes the account kind; Treasury never trades |
| UNSUPPORTED | 400 | `support` UNSUPPORTED or COMPUTED (model marks never execute); LIMITED is allowed with a warning |
| INSTRUMENT_EXPIRED / LAST_SESSION | 400 | option/future past its last trade date; on the expiry day trading is allowed until the session close; no new opens after 15:30 on an option's expiry day |
| QTY_INVALID | 400 | qty ≤ 0 or not a multiple of `lot` (options/futures whole contracts; bonds 1,000 face) |
| PRICE_REQUIRED / PRICE_TICK | 400 | LIMIT/STOP_LIMIT need limit_px; STOP/STOP_LIMIT need stop_px; prices on the tick grid |
| STOP_SIDE | 400 | buy stop below last / sell stop above last (would trigger at once; "use a limit") |
| OVERSELL / CLOSE_ONLY_MISMATCH | 400 | SELL > long qty net of working sells; BTC/STC/BUY_TO_COVER larger than the position |
| (session closed) | — | **not a block**: every order type rests until the session opens (TERMINAL_DECISIONS §4); the preview says when it can first trade and when a DAY order would expire |
| QUOTE_STALE | 409 | MARKET or STOP order submitted while the session is OPEN and `is_executable()` is false (a LIMIT may rest) |
| NO_BORROW | 409 | SELL_SHORT and `lending.locate` says unavailable; partial availability scales the order down with the reason in `orders.reason` (disclosed in the preview) |
| INSUFFICIENT_FUNDS | 409 | worst-case cash need > own available + Treasury available + PB headroom (§6.1); the message carries the three amounts |
| CONFIRM_REQUIRED | 409 | the Treasury sweep-in the fill would need exceeds `settings.sweep_confirm_threshold_c` (default 2,000,000.00) or the notional exceeds 25 % of consolidated NAV, and `confirm_large` is false — the UI shows the amount and a second confirm (fat-finger guard) |
| PREVIEW_STALE | 409 | `preview_id` unknown/expired (120 s) or the executable price moved > 0.5 % against the user since the preview (MARKET / marketable orders); the body carries the fresh preview and a `diff` |
| INTEGRITY_HOLD | 409 | the whole-DB ledger check failed and is unresolved (§6.8) |
| FUNDING_SHORTFALL | 409 | the account has an open FUNDING_SHORTFALL attention item in this currency (§11.6) |
| LIFETIME_CLOSED | 400 | the account belongs to a CLOSED lifetime (History is read-only) |
| NOT_ELIGIBLE | 400 | an OTC booking or PB onboarding in a lifetime that fails `books.eligibility` (message: the NAV, the threshold, the ECP floor and the sentence ELIGIBILITY) — an eligibility mechanic, not a risk warning |
| NOT_ELIGIBLE_QP | 400 | a private-markets trade in a lifetime below its qualified-purchaser threshold (sentence QP) |
| TRAIL_INVALID | 400 | TRAILING_STOP without exactly one of trail_amt / trail_pct, or a trail ≤ 0, or trail_pct ≥ 100 |
| AON_LEGS | 400 | a package marked AON whose legs are not all options on one underlying |
| PRICE_REQUIRED (private) | 400 | a private trade without a negotiated price; `LOCKUP` is a **warning** (never a block) on a sell inside the lot's lock-up |

Risk warnings and compliance notices never reject (they return `warnings` / `compliance`).

### 10.3 Preview (`POST /api/orders/preview`, pure, no writes; the submit handler recomputes it from the same snapshot)

```json
{"preview_id": "pv:…", "as_of": 1792526401, "expires_at": 1792526521, "ok": true, "blockers": [],
 "account_id": "US_EXCHANGE", "instrument": {"id": "AAPL", "name": "Apple Inc.", "kind": "EQUITY", "currency": "USD", "multiplier": "1", "support": "FULL"},
 "side": "BUY", "qty": 5000, "order_type": "MARKET", "tif": "DAY",
 "quote": {"last": "339.50", "bid": null, "ask": null, "quality": "delayed", "source": "Yahoo Finance (up to 15 min delayed)", "quote_time": 1792526401, "age_s": 245, "delay_label": "up to 15 min delayed", "method": "direct"},
 "execution": {"price_basis": "last", "expected_price": "339.50", "fill_now": true, "fill_qty_now": 5000,
               "cap_note": "latest 1-minute bar volume 61,900; 10 % participation caps a pass at 6,190 shares",
               "session": {"group": "US_EQUITY", "open": true, "closes_at": 1792526400}, "rests_until": null, "expires_at": 1792526400,
               "assumption": "fills at the last observed trade price; no bid/ask is available from the free feed"},
 "economics": {"gross": "1697500.00", "fee": "25.00", "fee_basis": "USD 0.005/share, min 1.00", "net_cash": "-1697525.00", "currency": "USD", "settle_date": "2026-10-21", "accrued": null},
 "funding": {"currency": "USD", "settled": "1000000.00", "pending": "0.00", "reserved": "0.00", "available": "1000000.00", "need": "1697525.00",
             "shortfall": "697525.00", "sweep_in": "697525.00", "sweep_source": "TREASURY", "treasury_available": "9000000.00", "pb_financing": "0.00",
             "fx_leg": null, "ok": true, "confirm_required": false, "formula": "own settled + receivables due by settlement − payables − reserved"},
 "borrow": null, "margin": null,
 "hedges": {"candidates": [ {"id": "PUT_7PCT", "label": "Protective put 315 Jan-16-2027", "legs": [...], "cost": "4875.00", "protection": "floor 315.00 (−7.2 %)", "quality": "delayed", "as_of": 1792526320} ], "status": "ready"},
 "compliance": [{"rule": "Reg SHO 203(b) locate", "status": "n/a", "message": "not a short sale"}],
 "risk": [{"code": "CONCENTRATION", "message": "AAPL would be 17 % of consolidated NAV"}],
 "conventions": ["FILL_PRICE", "PARTICIPATION", "SESSION", "COST_BASIS"]}
```
For a short sale `borrow = {needed: true, qty, qty_available, fee_rate, rebate_rate, tier, lender, collateral,
valid_until, basis}`; for options `margin = {requirement, formula, detail}`; for futures `margin = {initial,
maintenance, per_contract}`; a foreign need carries `fx_leg = {buy_ccy, sell_ccy, amount, rate, settle_date}` to be
submitted first (two commands; no automatic FX). The preview is debounced by the UI and re-run every 5 s while open.

### 10.4 State machine

```
            submit (validated)                          matcher: full fill
  [preview] ----------------------> WORKING ---------------------------------------> FILLED
                 |                   |  \ matcher: partial fill                        ^
                 | rejected          |   `-----------> PARTIAL ------------------------'  (remaining fills)
                 v                   |                    |       |
         order_rejections row        | user cancel        | cancel| DAY close / GTC 90 d / instrument expiry
                                     v                    v       v
                                 CANCELLED            CANCELLED EXPIRED
  WORKING/PARTIAL --(fill cannot be funded)--> SUSPENDED --(order.resume after a deposit/transfer)--> WORKING
  hedge leg: WAITING_PARENT --(primary fill)--> WORKING (sized to the primary's filled qty × ratio)
```
`triggered` is a flag on STOP/STOP_LIMIT orders. Terminal statuses: FILLED, CANCELLED, EXPIRED. Every transition writes
`order_events` (with the quote used) and an `events` row. `expires_ts`: DAY = `sessions.day_order_expiry(group, now)`;
GTC = created + 90 days (also expires when the instrument expires). Who moves what: the API submits/cancels/resumes;
the matcher fills and triggers; the lifecycle expires; nothing else.

### 10.5 Matching rules (`orders.evaluate`, pure, table-tested)

```
1. order.status not in (WORKING, PARTIAL)                          -> NONE  NOT_OPEN
2. now > order.expires_ts                                           -> NONE  EXPIRED_PENDING   (the lifecycle expires it; never fills after expiry)
3. not session.open                                                 -> NONE  AWAITING_SESSION
4. ok, reason = is_executable(rec, session, now); not ok            -> NONE  reason           (STALE, NO_QUOTE, QUOTE_FROM_PREVIOUS_SESSION, PROVIDER_SESSION_ENDED, ...)
5. px_exec: options -> ask (BUY) / bid (SELL) from the contract record (needs bid > 0 and ask >= bid);
            equities/ETFs: Cboe underlying ask/bid when a fresh chain for that underlying is already cached (never fetched for this), else Yahoo last;
            futures: last; FX: mid ± 0.2 pip (BUY adds, SELL subtracts); basis recorded in fills.price_basis
6. MARKET                                                           -> FILL at px_exec
7. LIMIT: marketable = px_exec <= limit (BUY) / >= limit (SELL)
     first_eval_done == 0 and marketable                            -> FILL at px_exec            (at or better)
     first_eval_done == 1 and (last <= limit (BUY) / last >= limit (SELL))   -> FILL at limit    (LIMIT_RULE)
     else                                                           -> NONE  NOT_MARKETABLE
8. STOP: not triggered and last >= stop (BUY) / last <= stop (SELL) -> TRIGGER, then step 6 in the same pass (fills at px_exec, the print that triggered it; gap-through disclosed)
   STOP_LIMIT: TRIGGER, then step 7
8a. TRAILING_STOP (sentence TRAILING): at submit trail_ref_px = last and stop_px = last − trail (SELL) / last + trail (BUY),
   trail = trail_amt or last × trail_pct/100 (re-derived from the ratcheted reference for trail_pct). Each observed last:
   SELL: if last > trail_ref_px -> trail_ref_px = last, stop_px = last − trail (ratchets up only); if last <= stop_px -> TRIGGER then step 6.
   BUY:  if last < trail_ref_px -> trail_ref_px = last, stop_px = last + trail (ratchets down only); if last >= stop_px -> TRIGGER then step 6.
   The ratchet is persisted on the order (order.match with action RATCHET, no fill; one event per change) so a restart keeps it.
9. qty = min(remaining, cap) from §10.6; qty == 0                   -> NONE  NO_NEW_BAR | NO_VOLUME_THIS_BAR
10. Decision carries bar_ts = rec.bar.t (None when no bar) and quote_time, so the fill's idem_key
    match:<order_id>:<quote_time>:<bar_ts|none> makes a second evaluation of the same observation a no-op.
```
The matcher evaluates every open order of an instrument each time the worker refreshes it, once more at submit (a
marketable order fills immediately against the cached quote), and sweeps all open orders every 15 s. One `order.match`
command per order per decision: one order's funding failure cannot roll back another's fill. The handler re-reads
`orders.status`, `filled_qty` and `expires_ts` inside the transaction (a cancel that committed between the read and
the fill wins) and re-checks `is_executable` with the payload's quote and `ctx.now`.

### 10.6 Partial fills by observed volume (`fill_cap`)

```
bar = rec.bar     (latest completed 1-minute bar: t <= now − 60)
if order.last_bar_ts is not None and bar is not None and bar.t <= order.last_bar_ts: cap = 0      # no new observation yet (NO_NEW_BAR)
elif bar is None or bar.v is None:              cap = remaining                                  # no volume information: fills in full (NO_VOLUME)
elif bar.v == 0:                                cap = 0                                          # a completed bar with zero volume: nothing traded (NO_VOLUME_THIS_BAR)
else:                                           cap = max(lot, floor(participation × bar.v / lot) × lot)
options:  cap = ask_size (BUY) / bid_size (SELL) contracts; size None -> remaining (disclosed); size 0 -> 0
futures:  as equities (Yahoo minute bars carry volume)
FX:       remaining (no volume concept; disclosed)
```
After a fill the order stores `last_bar_ts = bar.t` and `last_quote_time`; the next slice needs a newer bar. The
preview's `cap_note` states the bar volume and the cap. `participation` is the setting `participation_rate` (default
0.10), copied onto the order at submit and printed in every preview (PARTICIPATION, NO_VOLUME).

### 10.7 Applying a fill (`execution.apply_fill`, inside `order.match` / `order.submit`, one transaction)

1. **Economics**: `gross_c = inst.contract_value_c(price, qty)`; `fee_c` from the account's schedule (§12.1);
   `fx_rate` = 1 for USD, else the pair's mid from `ctx.fx` (its record goes into `quote_json.fx`).
2. **Funding before posting** (BUY securities/options/bonds: `need_c = gross_c + fee_c`; futures: `IM_c + fee_c`;
   SELL_SHORT: nothing beyond the borrow collateral already posted at submit): `execution.fund(ctx, account, ccy,
   need_c, "fill", fill_id, settle_date)` → own available (at the fill's settle date) → SWEEP_IN of exactly the
   shortfall (transfer linked to the fill, `shortfall_c` recorded) → PB draw up to headroom (PB accounts with
   `pb_financing`) → else `ConflictError INSUFFICIENT_FUNDS`: the order becomes **SUSPENDED** with the reason (resting
   orders) and an Attention WARN; `order.resume` re-queues it; new orders at submit are refused. Foreign-currency needs
   are met from the account's then Treasury's cash in that currency; otherwise refused with `INSUFFICIENT_FUNDS_CCY`
   and the FX leg hint (never automatic FX).
3. **Fill row** (`fills`, idem `match:…` or the job key), `quote_json` = the full `QuoteRecord`, `price_basis`,
   `bar_ts`, `cap_qty`, `cap_note`.
4. **Postings** per §5.4 (one entry `fill:<fill_id>`); **lots** opened / relieved FIFO (`lot_relief` rows);
   **settlements** rows (`settle:<fill_id>:CASH`) with `due_date = cal.settlement_date(trade_date, settle_days)`;
   futures get the `futures_settle` seed for the day's VM (fill price for the "today's fills" term) and the IM entry.
5. **Shorts**: a fill that opens/increases a short consumes the order's borrow (`lending.consume_for_short`); a fill
   that reduces a short returns the now-unneeded borrowed quantity in the same transaction (`lending.return_borrow`
   with reason `cover`): collateral back `Dr 1010 / Cr 1400`, fees settled `Dr 2310 / Cr 1010`, rebate `Dr 1010 / Cr 1410`.
6. **Option margin**: after an option fill, `options.margin_requirement_c` is recomputed for the account and 1310 is
   moved to the new requirement (`omargin:{account}:{ts}`, fund() for an increase).
7. **Order update**: `filled_qty`, `avg_px`, status, `last_bar_ts`, `first_eval_done = 1`, `order_events` row,
   events ORDER_PARTIAL / ORDER_FILLED.
8. **Hedge package**: if the order is a primary with `package_id`, child orders move WAITING_PARENT → WORKING with
   `qty = floor(filled_qty × ratio)` (resized on each partial: `floor(filled × ratio) − child.filled`); they match on
   their own quotes afterwards (sequential; the hedge depends on the primary fill). Children are cancelled when the
   primary ends with no fill.
9. The bus runs the scoped integrity check (§6.8 L-set) before COMMIT.

### 10.8 Sweep-in and the order of funding (`execution.fund`, `treasury.sweep_in`)

`needed − available(account, ccy, settle_date)` is the shortfall. Order: own available → Treasury sweep-in of exactly
the shortfall (one transfer, `kind SWEEP_IN`, `linked_type/linked_id`, `shortfall_c`, idem `xfer:{transfer_id}`,
event SWEEP_IN) → PB financing draw (PB accounts with the switch on, up to headroom) → refuse with the reason and
the three amounts. Sweep-in applies to fills, settlement payables, borrow collateral, collateral calls, futures IM,
VM losses and option margin. A sweep-in above `sweep_confirm_threshold_c` needs `confirm_large` at submit (lifecycle
sweeps never need confirmation; they are shown in Transactions and the Attention strip when above the threshold).

### 10.9 Hedge candidates (`hedges.candidates`, pops up on the ticket as soon as an instrument is entered)

`GET /api/hedges?account_id&instrument_id&side&qty` → `[Candidate]`, 202 `{status: "fetching"}` while the chain loads
(shared single-flight with the chain page). Each candidate: `{id, label, legs: [{instrument_id, side, qty, price_basis,
est_price, quote}], cost, protection_level, net_cash, greeks, chain_quality, as_of, editable: {strike, expiry, ratio}}`.

| primary | candidates (strikes snap to listed; expiry = nearest listed ≥ 90 calendar days) |
|---|---|
| long equity / ETF | `PUT_7PCT` protective put K ≈ 0.93 × S, n = floor(shares/100); `COLLAR_7` that put + call K ≈ 1.07 × S (sold at bid); `PUT_SPREAD` 0.93 / 0.85; `INDEX_OVERLAY` short ES (NQ for Nasdaq names), n = round(notional × beta / (ES × 50)), beta from the master, labelled static |
| short equity | `CALL_7PCT` 1.07 × S; `CALL_SPREAD` 1.07 / 1.15; `COLLAR_SHORT` |
| long / short option | `DELTA_HEDGE` opposite underlying shares = −delta × contracts × 100 rounded to 100 |
| futures | `CALENDAR` opposite contract of the next listed month; options on futures NOT IN THIS BUILD |
| FX spot | forward (Build 2, computed) — NOT IN THIS BUILD label in Build 1 |
| bond (Build 2) | ZN / ZB DV01-matched: n = round(position DV01 / contract DV01) with contract DV01 from `tables.FUT_DV01` (ZT 1.9, ZF 4.2, ZN 6.5, ZB 15 × price × 10 bp, labelled approximate) |

Cost uses the chain's ask (buys) / bid (sells); protection level = K/S − 1; the panel shows the chain's freshness.
Choosing a candidate submits the primary and the legs as one `order.submit` with `hedge`: the primary WORKING, legs
WAITING_PARENT with `ratio = leg_qty / primary_qty`; packages with a stock or futures primary are sequential
(disclosed "sequential; a leg can rest").

**Strategy templates and all-or-none option packages (B7, B8; sentence AON).** The hedge panel's second tab lists the
60 templates of `strategies.py` (§13.16) grouped as on the old Calculations page; picking one calls
`strategies.package_legs` (strike rules → listed strikes, tenor → nearest listed expiry ≥ today + months, sizing from
`units`), prices every leg from the cached chain (ask for buys, bid for sells; stock legs from the instrument record;
futures from the front month; TRS legs via `otc.preview` in eligible PB accounts, else the row says NOT ELIGIBLE / NOT
AVAILABLE HERE) and shows cost, net cash, delta/vega/theta, incremental option margin (§13.3) and the expiry payoff
grid (±35 %, intrinsic at the near expiry, disclosed). **Option-only packages fill all-or-none:** `fill_mode = AON`;
the matcher evaluates the package as a unit (`orders.evaluate_package`: every leg `is_executable` from the same chain
observation with `bid > 0 and ask ≥ bid` and displayed size ≥ the leg's remaining quantity — else NONE `AON_SIZE` /
`AON_STALE` and nothing fills) and applies all legs in **one** `order.match_package` command (idem
`matchpkg:{package_id}:{quote_time}`), computing the option margin once for the whole package. Packages with a stock
or futures leg stay SEQUENTIAL (the owner's B7: "the hedge only executes if the main order fills; resized if the main
order partly fills"). Candidate `TRS_OVERLAY` (receive/pay TRS on the primary's notional through the account's PB)
appears only in PB accounts of eligible lifetimes.

---

## 11. Lifecycle jobs, keyed to the real clock (`lifecycle.py` + product handlers)

### 11.1 Scheduler

`LifecycleScheduler.tick()` every 30 s: for each job in `REGISTRY` (order below), `job.due(conn, clock, cal, sessions)`
returns the keys that should run now and are not in `jobs_done`; each key runs as `bus.execute(Command("lifecycle.<job>"
or the product kind, {key…}, request_id=f"job:{name}:{key}", actor="lifecycle"))`; the handler writes `jobs_done` in the
same transaction (DONE or SKIPPED with a note). Because `due()` derives keys from **state** (dates since the last
accrual, settlements with past due dates, positions with expired instruments) and not from "what happened while we
ran", catch-up after downtime is automatic: the first tick after a 7-day gap posts exactly 7 accruals per object, in
date order, each with its own key. A failing key is recorded in `job_failures` and Attention (WARN; ERROR after 5),
retried with backoff 1/2/4/8/16 min then hourly. `POST /api/jobs/run` (loopback) runs a tick now.

Order within a tick: SETTLE_DUE → ACCRUE_DAILY → PAY_MONTHLY → DIVIDEND (§13.15) → PC_PAY (§13.14) → SWAP_FIX →
SWAP_PAY → TRS_RESET → XCCY_EVENTS (§13.11–13.12) → OPTION_EXPIRY → FUTURES_VM → FUTURES_FINAL → SWAP_VM →
OPTION_MARGIN_MARK → COLLATERAL_MARK → BORROW_CLEANUP → SWEEP_OUT → MARKS_RECORD → SNAPSHOT → EXPIRE_ORDERS →
HOUSEKEEPING → INTEGRITY_FULL. Jobs whose product module is not merged yet are skipped with a note in `/api/jobs`
(never an error, never a fake posting). `due()` of every job considers only accounts of OPEN lifetimes.

### 11.2 Job table

| job | when (ET) | key | what it does | idem |
|---|---|---|---|---|
| SETTLE_DUE | from 00:30 on `due_date` (business days), every tick | `settle_id` | one PENDING settlement → cash: receivable Dr 1010 / Cr 1200|1250; payable Dr 2100|2350 / Cr 1010 with `fund()` first (sweep-in `xfer:…` linked to the settlement). Foreign payables use the account's cash in that currency, else Treasury's, else Attention ERROR `SETTLEMENT_UNFUNDED` (stays PENDING, retried). | `settle:{settle_id}` |
| ACCRUE_DAILY | from 00:30 for every calendar night D < today not yet accrued per object | `{kind}:{ref}:{D}` | §11.3: borrow fee and rebate, currency loan interest, PB financing interest, Treasury cash interest; bonds Build 2. Base for night D = the balance open at 17:00 ET of D. Rate = latest published observation ≤ D (RATE_LAG); none within 10 days → Attention WARN, the night is skipped and retried (never defaulted). | `accr:{kind}:{ref}:{D}` |
| PAY_MONTHLY | 1st calendar day 00:45 | `{account}:{yyyy-mm}` | settles accrued fees/rebates/interest of the previous month: 2310 → 1010, 1010 ← 1410, 2300 → 1010, 1230 → 1010 (Treasury); loan interest is paid at repayment, not monthly | `pay:…:{yyyy-mm}` |
| OPTION_EXPIRY | expiry date from 16:20 (index options 16:35), retried every 5 min until 20:00, then with the next morning's `daily()` close / `prev_close` | `{account}:{option_id}` | underlying CLOSE mark of the expiry date (§11.4) → intrinsic ≥ 0.01: EXERCISE (long) / ASSIGNMENT (short) fills at the strike; else EXPIRY fill at 0; index options cash-settle intrinsic. Needs-a-borrow cases SKIP with Attention (§5.4). Missing close → SKIPPED `EXPIRY_PENDING_CLOSE`, retried. | `expiry:{account}:{option_id}` |
| FUTURES_VM | `settle_time + 5 min` on trading days of the group | `{account}:{inst}:{date}` | `S_t` = last record with `quote_time ≤ settle_time` of that date fetched after it (else `YahooChart.daily()` close for the date, labelled "observed close (daily bars)"); `VM = qty_prev × (S_t − S_{t−1}) × mult + Σ today's fills q_i × (S_t − p_i) × mult`; posts 4400 vs 1010 (`fund()` on a loss; §11.6 overdraft rule); writes `futures_settle` and `marks(SETTLE)`. Missing print → SKIPPED, retried; never invented. | `vm:{account}:{inst}:{date}` |
| FUTURES_FINAL | last trade date, after VM | `{account}:{inst}` | FUTURES_FINAL fill at `S_t` closing the position; IM released; instrument EXPIRED | `final:{account}:{inst}` |
| OPTION_MARGIN_MARK | 17:10 on trading days and after every option fill | `{account}:{date}` | recompute §13.3; move 1310 to the requirement; `margin_state` row | `omargin:{account}:{date}` |
| COLLATERAL_MARK | 17:10 on trading days (CLOSE mark of the day) | `{borrow_id}:{date}` | `required = to_c(1.02 × qty_outstanding × close)`; `delta = required − collateral_c`; one movement (call with `fund()`; return); `borrows.collateral_c = required`, `last_mark_date` | `cmark:{borrow_id}:{date}` |
| BORROW_CLEANUP | every tick | `{borrow_id}` | returns borrowed quantity not backing a short (order cancelled / expired / partly filled) | `collat:{borrow_id}:return:cleanup:{date}` |
| SWEEP_OUT | 17:30 once per calendar day, per account with `sweep_out = 1` | `{account}:{ccy}:{date}` | `excess = settled − payables pending − reserved − option margin top-up expected (0) − futures maintenance shortfall (0 if none) − buffer`; if `excess > 0` → SWEEP_OUT transfer to Treasury; never sweeps a currency negative | `sweep:out:{account}:{ccy}:{date}` |
| MARKS_RECORD | 16:20 (US equities/options), `settle_time + 5` (futures), 17:05 (FX) | `{inst}:{date}` | records the observed CLOSE/SETTLE mark of every held / working instrument from the cache (`quote_time` on that date and ≥ session end − 60 s); after downtime from `daily()` bars, note "recorded late" | `marks` PK |
| SNAPSHOT | 17:45 every calendar day | `{account}:{date}` (+ `ALL`) | `nav_snapshots` from §6.3–6.5 with `partial` and `missing`; `flows_day_c`, `pnl_day_c` | `snap:{account}:{date}` |
| EXPIRE_ORDERS | at every session close + every tick for GTC age | `{order_id}` | DAY → EXPIRED at `expires_ts`; GTC after 90 days; orders on expired instruments; an unfilled short's borrow returned in the same command | `expire:{order_id}` |
| HOUSEKEEPING | 00:15 daily; first business day monthly | `{date}` | list the next futures months, mark expired options/futures, refresh FINRA file pointer, prune `quote_cache` rows of instruments nobody holds/watches (never `order_events`) | `hk:{date}` |
| INTEGRITY_FULL | hourly at :05 and at startup | `{YYYY-MM-DDTHH}` | `accounting.integrity("full")` → `meta.integrity_json`, Attention, INTEGRITY_HOLD on an L-failure | `integrity:{hour}` |
| DIVIDEND (§13.15) | 00:40 on the ex-date (dividends table filled by the worker from Yahoo chart events for held/shorted names); pay date | `{account}:{inst}:{exdate}` | long: Dr 1210 / Cr 4200 `qty × amount` on the ex-date (position at the prior close); short: Dr 5400 / Cr 2320; cash on `pay_date` (Yahoo) else ex + 15 business days (DIVIDEND); a split event → Attention WARN only | `div:…`, `divpay:…` |
| PC_PAY (§13.14) | `next_payment_date` 00:40 | `{loan_id}:{date}` | pays the accrued coupon `Dr 1010 / Cr 1220`; rolls `next_payment_date` + payment_months (following); at maturity repays face `Dr 1010 / Cr 1130 / 4000` | `pcpay:{loan_id}:{date}` |
| SWAP_FIX (§13.11) | each reset date 00:40 | `{contract}:{date}` | fixes the floating leg from the latest published index ≤ reset date (RATE_LAG; none within 10 days → SKIPPED + WARN) and restarts the accrual segment | `swapfix:{contract}:{date}` |
| SWAP_PAY | each payment date 00:45 | `{contract}:{date}` | settles the accrued receive and pay legs net in cash (`fund()` for a net payment) | `swappay:{contract}:{date}` |
| TRS_RESET (§13.12) | each reset date 16:25 (the underlying's CLOSE of the reset date) | `{contract}:{date}` | return leg `(S_t − S_reset) × units` + dividends accrued, financing accrued → cash; notional ← `S_t × units`; segment restart | `trsreset:{contract}:{date}` |
| XCCY_EVENTS | start date, every FX-reset date (MTM XCCY) 17:05, maturity | `{contract}:{date}:{kind}` | notional exchanges and FX notional resets at the FX CLOSE mark (§13.12) | `xccy:…`, `xccyfx:…` |
| SWAP_VM (§13.11) | 17:05 every calendar day on which a curve / underlying mark exists (weekends: the last mark → Δ = 0 → SKIPPED "no change") | `{contract}:{date}` | `Δ = mark_t − mark_{t−1}` (CTM: vs the last VM mark; STM: mark since the last settle); one movement per contract per day per §5.4 (`fund()` for a payment; §11.6 overdraft rule); writes `otc_vm`, updates `vm_balance_c` / `mark_c`; missing curve → SKIPPED, retried | `swapvm:{contract}:{date}` |
| PB_MONTHLY_MIN (§12.4) | with PAY_MONTHLY | `{account}:{yyyy-mm}` | charges `max(0, monthly_minimum_c − fees charged in the month)` to 5000 | `pay:pbmin:…` |
| COUPON / MATURITY (bonds) / REPO_ACCRUE | Build 2 | per §13.8–13.9 | | |

ACCRUE_DAILY kinds now also include `swaprcv` / `swappay` (IRS legs, §13.11), `trsfin` (TRS financing, §13.12),
`pccpn` / `pcoid` (private credit, §13.14) and `pbcredit` (PB credit interest on positive settled USD, §12.4).

Every accrual uses calendar days (weekends included); fills, settlements, expiries and marks only on trading days of
the instrument's group.

### 11.3 Accrual method (cumulative rounding; convention ACCRUAL)

For `(kind, ref)` the `accrual_state` row holds the segment: `segment_start` (the first night of the current base and
rate), `base_c`, `rate`, `day_basis`, `accrued_through`, `cum_c`. For night D (first night = the night the balance was
opened, if open at 17:00 ET of that day): `n = D − segment_start + 1`; `cum_new = to_c(base_c × rate × n / basis)`;
posted amount = `cum_new − cum_c`; then `cum_c = cum_new`, `accrued_through = D`. When the base or rate changes (a
collateral mark, a partial repayment, a new rate observation), the segment restarts at that night with `cum_c = 0`.
Thus the sum of the nightly postings always equals `to_c(base × rate × n / basis)` for the segment — never a drift.

| kind | base | rate | basis |
|---|---|---|---|
| bfee (borrow fee) | borrowed shares' market value `qty_outstanding × mark_D` (CLOSE of D, else the last CLOSE before D — weekends use Friday's) | tier rate (§12.2) | 360 |
| rebate | cash collateral `borrows.collateral_c` | `max(0, SOFR_D − 0.50 %)` (0 for HTB tier T3) | 360 |
| loan | principal | policy index of the currency + 0.75 % | 360 (GBP 365) |
| pb | `balance(2700)` | SOFR + PB spread | 360 |
| cashint | Treasury settled USD at 17:00 ET of D, if positive | `max(0, SOFR_D − 0.50 %)` | 360 |
| bond (Build 2) | face | coupon, ACT/ACT accrued(D) − accrued(D−1) | — |

### 11.4 Observed closes and downtime

- The US CLOSE mark is captured at **16:20 ET** (not 16:02) from a record whose `quote_time` is on that date and ≥
  the regular session end − 60 s; index options at 16:35. Futures SETTLE at `settle_time + 5 min`.
- If the server was off, `MARKS_RECORD` and `OPTION_EXPIRY`/`FUTURES_VM` use `YahooChart.daily(symbol, 10)` for the
  missed dates (`method daily_close`, note "recorded late"); only if no daily bar exists for the date (symbol
  delisted) does the job stay SKIPPED with an Attention item for the owner.
- Reconciliation: when the next session's `prev_close` differs from a recorded CLOSE by more than one tick, an INFO
  attention item reports it (`CLOSE_REVISED`); postings already made are not reversed (disclosed).

### 11.5 Sweep-out worked example (from §19.2)

US_EXCHANGE after the SPY buy, before settlement: settled 3,846,625.00, payables 3,846,625.00, no orders, buffer
10,000.00 → `excess = 3,846,625 − 3,846,625 − 0 − 10,000 = −10,000 < 0` → no transfer (job SKIPPED "nothing idle").
After T+1 settlement settled 0 → nothing. An account with settled 50,000.00 and no needs → 40,000.00 returns.

### 11.6 Calls that cannot be funded (the overdraft rule)

A VM loss, collateral call, option-margin increase or settlement payable that `fund()` cannot cover (Treasury empty,
no PB headroom) is **still posted** — the ledger records the loss or obligation honestly — and the account's 1010 goes
negative. The posting command creates, in the same transaction, an Attention item ERROR `FUNDING_SHORTFALL` for
`(account, ccy)` with the amount; L18 accepts a negative 1010 only while that item is open; new orders in the account
are refused with 409 `FUNDING_SHORTFALL`; the item auto-resolves when 1010 ≥ 0 again (deposit, transfer, closing
positions). No automatic liquidation (risk only warns).

---

## 12. Fee, rate and contract tables (`tables.py`; every number is a disclosed, modelled assumption)

### 12.1 Fees (`FEES`, shown in every preview with `fee_basis`)

| product | DEFAULT schedule (US Exchange, Debt, Credit, Foreign) | basis sentence |
|---|---|---|
| US equities / ETFs / ADRs / REITs / preferreds | USD 0.005 per share, minimum USD 1.00 per fill | "USD 0.005/share, min 1.00" |
| listed options | USD 0.65 per contract per fill; exercise / assignment / expiry free | "USD 0.65/contract" |
| futures | USD 2.25 per contract per side, charged in cash the same day | "USD 2.25/contract/side" |
| FX spot | no commission; dealt at mid ± 0.2 pip against the user; the cost vs mid is shown as `spread_cost` | FX_SPOT |
| bonds (Build 2) | USD 0.02 per 100 face, minimum USD 5.00 | "USD 0.02/100 face, min 5.00" |
| PB accounts | the broker's schedule (§12.4) replaces the equity / option / futures rows | per PB |

All fees are quantised to cents HALF_EVEN after the multiplication; fees post to 5000 with the instrument.

### 12.2 Rates (`RATES`; indices from FRED, observation date shown in every memo)

| item | rate | basis | accrual |
|---|---|---|---|
| idle Treasury USD cash | SOFR − 0.50 % (floor 0) | ACT/360 | nightly on settled 1010:USD ≥ 0; paid monthly |
| PB margin financing | SOFR + PB spread (§12.4) | ACT/360 | nightly on 2700; paid monthly |
| stock borrow fee | tier rate: GC 0.30 %, T1 1.00 %, T2 5.00 %, T3 25.00 % | ACT/360 | nightly on qty × mark |
| cash collateral rebate | max(0, SOFR − 0.50 %) (0 for T3) | ACT/360 | nightly on collateral |
| currency loans | policy index + 0.75 %: USD SOFR; EUR ECBDFR; GBP IUDSOIA; JPY IRSTCI01JPM156N (monthly, labelled); CHF IRSTCI01CHM156N; CAD IRSTCI01CAM156N; AUD IRSTCI01AUM156N | ACT/360 (GBP ACT/365) | nightly on principal; paid at repayment |
| repo / reverse repo (Build 2) | SOFR + 0.05 % (UST) / + 0.35 % (IG corp); SOFR − 0.05 % lending; haircuts UST 2 %, IG 5 %, equities 15 % | ACT/360 | nightly |
| `POLICY_INDEX` | the FRED series above; `DAYCOUNT_CASH = {"GBP": 365, "*": 360}`; bonds ACT/ACT | | |

**Borrow availability tiers (`HTB`, heuristic from observable data; convention BORROW_AVAIL):**

| data (FINRA daily short volume ratio of the last session; ADV from the master) | tier | fee | availability |
|---|---|---|---|
| in the master, ratio < 0.60 and ADV ≥ 1 M shares | GC | 0.30 % | full |
| ratio ≥ 0.60, or ADV in [250 k, 1 M) | T1 | 1.00 % | full |
| ADV < 250 k | T2 | 5.00 % | `min(qty, 0.25 × ADV)` |
| no FINRA row for the last 3 sessions, or preferred / ADR with ADV < 100 k | T3 | 25.00 % | `min(qty, 0.10 × ADV)` |
| not in the master, no ADV, or `shortable` false | NONE | — | `available = False` → NO_BORROW |

Recalls are **never generated**: when a shorted name's FINRA ratio is ≥ 0.80 for three consecutive sessions an INFO
attention item says "hard-to-borrow tightening: a lender could recall; consider covering". No automatic buy-in
exists (5700 is never posted by the system).

### 12.3 Futures contract table (`instruments.FUTURES_SPECS`; margins static — convention FUT_MARGIN)

| root | name | group | Yahoo suffix | multiplier (USD per point) | tick | IM / MM | listed months | last-trade rule | settle time ET |
|---|---|---|---|---|---|---|---|---|---|
| ES | E-mini S&P 500 | CME_EQUITY_INDEX | CME | 50 | 0.25 | 17,500 / 15,900 | H M U Z | 3rd Friday, 09:30 | 16:00 |
| NQ | E-mini Nasdaq-100 | CME_EQUITY_INDEX | CME | 20 | 0.25 | 26,000 / 23,600 | H M U Z | 3rd Friday | 16:00 |
| YM | E-mini Dow | CME_EQUITY_INDEX | CBT | 5 | 1.00 | 11,000 / 10,000 | H M U Z | 3rd Friday | 16:00 |
| RTY | E-mini Russell 2000 | CME_EQUITY_INDEX | CME | 50 | 0.10 | 9,000 / 8,200 | H M U Z | 3rd Friday | 16:00 |
| CL | WTI crude | NYMEX_ENERGY | NYM | 1,000 | 0.01 | 6,500 / 5,900 | monthly | 3 business days before the 25th of the month preceding delivery | 14:30 |
| BZ | Brent | NYMEX_ENERGY | NYM | 1,000 | 0.01 | 6,500 / 5,900 | monthly | last business day of the 2nd month preceding delivery | 14:30 |
| NG | Henry Hub | NYMEX_ENERGY | NYM | 10,000 | 0.001 | 4,500 / 4,100 | monthly | 3 business days before the 1st of the delivery month | 14:30 |
| GC | Gold | COMEX_METALS | CMX | 100 | 0.10 | 12,000 / 10,900 | G J M Q V Z | 3rd last business day of the delivery month | 14:30 |
| SI | Silver | COMEX_METALS | CMX | 5,000 | 0.005 | 11,000 / 10,000 | H K N U Z | same | 14:30 |
| HG | Copper | COMEX_METALS | CMX | 25,000 | 0.0005 | 6,000 / 5,500 | H K N U Z | same | 14:30 |
| ZB | 30-year T-bond | CME_RATES | CBT | 1,000 | 0.03125 | 5,000 / 4,500 | H M U Z | 7th business day before the last business day of the delivery month | 16:00 |
| ZN | 10-year note | CME_RATES | CBT | 1,000 | 0.015625 | 2,600 / 2,400 | H M U Z | same | 16:00 |
| ZF | 5-year note | CME_RATES | CBT | 1,000 | 0.0078125 | 1,500 / 1,400 | H M U Z | last business day of the delivery month | 16:00 |
| ZT | 2-year note | CME_RATES | CBT | 2,000 | 0.0078125 | 1,100 / 1,000 | H M U Z | same | 16:00 |
| 6E | Euro FX | CME_FX | CME | 125,000 | 0.00005 | 2,800 / 2,500 | H M U Z | 2nd business day before the 3rd Wednesday | 16:00 |
| 6J | Japanese yen | CME_FX | CME | 12,500,000 | 0.0000005 | 3,000 / 2,700 | H M U Z | same | 16:00 |
| 6B | British pound | CME_FX | CME | 62,500 | 0.0001 | 2,400 / 2,200 | H M U Z | same | 16:00 |
| ZC | Corn (cents/bu) | CBOT_GRAINS | CBT | 50 | 0.25 | 1,500 / 1,400 | H K N U Z | business day before the 15th of the contract month | 14:20 |
| ZW | Wheat (cents/bu) | CBOT_GRAINS | CBT | 50 | 0.25 | 2,200 / 2,000 | H K N U Z | same | 14:20 |
| ZS | Soybeans (cents/bu) | CBOT_GRAINS | CBT | 50 | 0.25 | 2,800 / 2,500 | F H K N Q U X | same | 14:20 |

Yahoo quotes grains in cents per bushel (`ZC=F` 525.25), so the multiplier is 50 USD per point and the price stays as
quoted; rates futures come from Yahoo already in decimal points (112.515625). Month codes F G H J K M N Q U V X Z.
Listed = the next 4 months per root (HOUSEKEEPING keeps the window rolling; expired contracts stay in the master as
EXPIRED). The spec row carries `settle_time_et`, `trade_date_rule = "next_business_day_after_18:00"`.

### 12.4 Prime-broker schedules (`PB_SCHEDULES`; opened on demand; "modelled assumptions" on the opener modal)

| broker | id | equities (per share, min) | options / contract | futures / side | financing | borrow over GC | OTC (Build 2) |
|---|---|---|---|---|---|---|---|
| Goldman Sachs | PB_GS | 0.0030 / 1.00 | 0.55 | 2.00 | SOFR + 1.25 % | +0.10 % | IRS 1.0 bp, TRS SOFR + 0.60 % |
| JPMorgan | PB_JPM | 0.0030 / 1.00 | 0.55 | 2.00 | SOFR + 1.20 % | +0.10 % | 1.0 bp, +0.55 % |
| Morgan Stanley | PB_MS | 0.0035 / 1.00 | 0.60 | 2.10 | SOFR + 1.30 % | +0.12 % | 1.2 bp, +0.60 % |
| Deutsche Bank | PB_DB | 0.0035 / 1.00 | 0.60 | 2.10 | SOFR + 1.40 % | +0.15 % | 1.5 bp, +0.70 % |
| Barclays | PB_BARC | 0.0035 / 1.00 | 0.60 | 2.10 | SOFR + 1.35 % | +0.12 % | 1.2 bp, +0.65 % |
| Citi | PB_CITI | 0.0030 / 1.00 | 0.55 | 2.00 | SOFR + 1.30 % | +0.10 % | 1.0 bp, +0.60 % |
| UBS | PB_UBS | 0.0035 / 1.00 | 0.60 | 2.10 | SOFR + 1.35 % | +0.12 % | 1.2 bp, +0.65 % |
| BNP Paribas | PB_BNP | 0.0040 / 1.00 | 0.65 | 2.25 | SOFR + 1.45 % | +0.15 % | 1.5 bp, +0.70 % |
| BofA Securities | PB_BOFA | 0.0030 / 1.00 | 0.55 | 2.00 | SOFR + 1.25 % | +0.10 % | 1.0 bp, +0.60 % |

PB financing headroom = 50 % of the long market value in that PB account (Reg-T initial, disclosed) − `balance(2700)`,
capped by `accounts.pb_limit_c` (default 0 = no cap beyond headroom). Margin is per PB: a long at one PB never offsets a
short at another; swap IM/VM (§13.11) and option/futures margin are held in the PB account that booked them.

**Editable schedules (B4).** `account.open_pb` copies the broker's row into `pb_schedules.schedule_json`; Settings → Prime
brokers edits it per PB (`PATCH /api/accounts/{id}/schedule`, light command `account.update_schedule`; every change is an
event SETTINGS_CHANGED with before/after; past postings are never restated). Fields (all decimal strings / cents):
`equity_per_share`, `equity_min_c`, `option_per_contract`, `futures_per_side`, `financing_spread` (debit balances: SOFR +
spread), `credit_interest_spread` (interest on positive settled USD cash: `max(0, SOFR − spread)` nightly `accr:pbcredit`,
default 0.75 %), `gc_rate` (default 0.30 % + the "borrow over GC" add-on), `htb_add`, `otc_spread_mult` (multiplies the
IRS half-width and the TRS spread, default 1.0), `swap_margin_mode` (CTM | STM, default CTM), `monthly_minimum_c`
(default 0; PB_MONTHLY_MIN charges the shortfall). `prime.schedule(account)` reads the row; `tables.PB_SCHEDULES` is
only the seed.

### 12.5 Compliance limit tables (`COMPLIANCE_LIMITS`, static, each with "verify the current rule text")

Option position limits by underlying class: 25,000 / 50,000 / 75,000 / 200,000 / 250,000 contracts (default 250,000
for broad ETFs/indices); CFTC/CME spot-month limits per root (ES 20,000, NQ 20,000, CL 6,000, NG 2,000, GC 6,000, SI
3,000, ZN 100,000 …); 13D/G 5 %, Section 16 10 %, 13h-1 2 M shares / USD 20 M per day, 13F USD 100 M, Form SHO USD 10 M
gross short, Rule 201 trigger 10 % below the prior close, EU/UK 0.1 % / 0.5 %; PDT equity floor USD 25,000; ECP floor
USD 10 M; QP thresholds per profile (§2.1).

**Per-book compliance profiles (`tables.BOOK_PROFILES`, the 17:41 amendment):** `INDIVIDUAL` (Personal) → Reg T 50 %
initial / FINRA 4210 maintenance, pattern-day-trader (4+ day trades in 5 business days with NAV < 25,000), cash-account
good-faith / free-riding (selling before the purchase settled) shown pre-trade; 13D/G and Section 16 when they apply.
`FUND` → 13F notice when 13F securities exceed USD 100 M, 13h-1 at the daily/monthly thresholds, 13D/G, Section 16,
Form SHO / 13f-2, option and futures limits. `INSTITUTION` → 13F from the start (informational "13F applies: file
quarterly"), 13h-1, 13D/G, Section 16, position limits / accountability levels, Form SHO / 13f-2; private markets:
accredited / QP status, Rule 144 holding periods, Form D (issuer's filing, informational), "13F does not cover private
shares". All inform-only.

---

## 13. Product rules

### 13.1 Short sales and securities lending (`lending.py`; conventions SHORT_COLLATERAL, BORROW_AVAIL)

1. `locate(services, account, inst, qty)` (pure, from the master + the last FINRA file + the mark): tier and fee
   from §12.2; `collateral_c = to_c(1.02 × qty × mark)`; `valid_until = now + 120 s`; `basis` text for the ticket.
2. `order.submit` for SELL_SHORT: the borrow is opened **first, in the same transaction** (`collat:{borrow_id}:open`:
   `Dr 1400 / Cr 1010` with `fund()` → sweep-in linked to the borrow), `borrows.order_id` = the order, `reason =
   'pending short sale'`; if the locate says unavailable → `NO_BORROW`, nothing posted; partial availability → the
   order quantity is scaled to `qty_available` with `orders.reason` and the preview said so.
3. The short fill (`Dr 1200 / Dr 5000 / Cr 2600`) opens a SHORT lot at the sale price; `consume_for_short` clears the
   borrow's `reason`. Sale proceeds settle T+1 into 1010 as **ordinary settled cash** (no restricted-cash account).
   Net effect after T+1 = the real-world one: 102 % of the short's value is held at the lender (1400, not spendable;
   the Cash panel line reads "held at lender (102 % collateral)") and the account's own cash is down by 2 % plus fees.
4. Nightly: fee on `qty × mark_D` and rebate on collateral (§11.3). Daily 17:10 collateral mark both ways
   (`cmark`), one movement per borrow per day; a call is paid by `fund()` (Treasury sweep), excess returns to 1010.
5. BUY_TO_COVER relieves SHORT lots FIFO (realised to 4000), and in the same transaction returns the covered
   quantity's borrow pro rata: collateral back (`Dr 1010 / Cr 1400`), accrued fees paid (`Dr 2310 / Cr 1010`), accrued
   rebate received (`Dr 1010 / Cr 1410`). A borrow can never be returned while it still backs a short
   (`BACKS_SHORT`).
6. `credit.return_stock` (manual) and BORROW_CLEANUP return borrowed quantity that backs nothing (cancelled or expired
   short orders) and are the only other ways a borrow closes.
7. Recalls: INFO notices only (§12.2). Buy-ins are never synthesised. Securities lending **out** of inventory and repo
   are Build 2 (§13.8–13.9); payments in lieu on shorts are §13.15.
8. Integrity: L12 (every short has a live borrow; every open borrow has a short or a reason), L13 (1400 == collateral).

### 13.2 Equities / ETFs (`execution.py`, `products/equities` rules live in `instruments` + `tables`)

Fills per §10.7; settlement T+1 (`SETTLE_DUE`); FIFO lots (`FIFO`, `COST_BASIS`); a SELL that would flip the position
through zero is refused (`OVERSELL`) — the owner enters SELL for the long quantity and SELL_SHORT (with a borrow) for the
rest. Dividends and payments in lieu: §13.15 (Yahoo chart events; until WP-18 lands the positions row says "dividends:
not yet built"). Splits: Build 2 (Attention WARN when Yahoo reports a split event on a held name: "manual review").

### 13.3 Listed options (`products/options.py`; conventions EXERCISE, OCC, OPT_MARGIN)

- Contracts come from the Cboe chain (`ensure_option`): equity/ETF options AMERICAN / PHYSICAL × 100; index options
  (`SPX`, `SPXW`, `NDX`, `RUT`, `VIX`) EUROPEAN / CASH. Sides BTO / STO / BTC / STC; executions only at the Cboe NBBO
  (`bid > 0 and ask ≥ bid`, fresh); a Cboe outage makes options `unavailable` → no fills (no model fills). Caps by
  displayed size (§10.6). Marks: Cboe mid, else `option_model` (`computed`).
- **Margin (`margin_requirement_c`, per account, USD, moved to 1310 — cash actually leaves 1010, so buying power is
  honest; L16 ties it):** per short contract × 100: naked call `max(premium + 0.20 × S − max(0, K − S), premium +
  0.10 × S)`; naked put `max(premium + 0.20 × S − max(0, S − K), premium + 0.10 × K)`; index options 15 % / 10 %;
  covered call (long shares ≥ 100 per contract in the same account) 0 and the shares are reserved (cannot be sold
  while the call is open: `SHARES_RESERVED`); vertical spread (same expiry, opposite long covering) = width × 100 net
  of credit; cash-secured put = naked formula (shown). `premium` = the contract's current mark × 100; `S` = the
  underlying mark. Recomputed after every option fill and at 17:10; the formula and detail are in the preview.
- **Expiry (`options.expire`, key `expiry:{account}:{option_id}`):** the underlying's CLOSE mark of the expiry date
  (§11.4); `intrinsic_call = max(0, S − K)`, `intrinsic_put = max(0, K − S)`; `≥ 0.01` → EXERCISE (long) /
  ASSIGNMENT (short) fills at K with origin EXERCISE / ASSIGNMENT, commission-free, premium rolled into the stock
  basis/proceeds (§5.4); `< 0.01` → EXPIRY fill at 0 (long realises −cost, short realises +premium). Index options
  settle intrinsic in cash. Cash needs follow `fund()`; an unfunded exercise obligation uses the overdraft rule
  (§11.6). Needs-a-borrow cases SKIP with Attention and are retried (the owner can buy shares first); nothing is
  cash-settled or invented. The option instrument becomes EXPIRED; 1310 is recomputed.
- **Early exercise** by the owner only (`options.exercise`, long American, during the option session, at the
  strike); early assignment of short options is not simulated (disclosed as a limitation).

### 13.4 Futures (`products/futures.py`; conventions FUT_SETTLE, FUT_MARGIN)

- Fill: fee same day (`Dr 5000 / Cr 1010`); IM per contract moved to 1300 (`im:{fill_id}`, `fund()` first) on
  opening, released on closing; no cost posting (`cost_c` of futures lots = 0 by convention; `avg_px` carried on the
  lot's `unit_cost` for display). Position = Σ lots; `futures_settle` holds `settle_px`.
- **VM (`futures.vm`, `vm:{account}:{inst}:{date}`)** per §11.2 with `S_t` the observed print at/before the group's
  settle time (table §12.3) or the daily close after downtime. Loss → `fund()` → overdraft rule. L15 ties 1300.
- **Final (`futures.final`)** on the last trade date after that day's VM: FUTURES_FINAL fill at `S_t` (qty → 0,
  P&L already in 4400), IM released, instrument EXPIRED; no new orders from the expiry date (`LAST_SESSION`).
- Maintenance: shown in Risk (`maintenance = Σ MM × |qty|` vs account equity); never liquidated.

### 13.5 FX spot (`fx.py`; convention FX_SPOT)

Any account with an FX allowance (§7.2) may convert. `spot_preview` → rate = Yahoo mid ∓ 0.2 pip against the user
(pip 0.0001; JPY pairs 0.01), amount in either currency, value date T+2 US business days, `spread_cost_c`, both legs'
settled/pending effect. `fx.spot` requires `is_executable` on the pair (FX_SPOT session), posts the two-leg entry
(§5.4), two settlement rows (`BUY_CCY` +, `SELL_CCY` −) due T+2, `fx_deals` row with the full quote. Crosses without a
direct Yahoo quote go through USD (two deals, disclosed). Forwards: Build 2.

### 13.6 Currency loans (`credit.py`)

`credit.loan_draw {account, currency, amount, term_days | null}` (CREDIT account; PB accounts for PB loans): lender
"Money market (paper)"; rate floating = policy index + 0.75 %, reset nightly from the latest observation; `Dr 1010:ccy
/ Cr 2750:ccy` — the drawn currency is **settled cash at once** and can be spot-converted the same day (the FX settles
T+2 while the loan accrues from the draw night). Currencies without a policy series (`POLICY_INDEX`) are refused
(`CURRENCY_UNSUPPORTED`). `credit.loan_repay {loan_id, amount}` pays accrued first, then principal; refused with
`INSUFFICIENT_CASH_CCY` and the hint "buy EUR x with Convert" when the account's settled cash in that currency is
short (no automatic FX). Maturity (`term_days`): the lifecycle attempts repayment on the maturity date; if cash is
short the loan rolls with an Attention WARN (no invented default). Interest is `Financing` in P&L.

### 13.7 Prime brokers (`prime.py`; Build 1 = accounts, schedules, DVP trading, financing; OTC = Build 2)

`account.open_pb {broker}` creates `PB_<key>` (idempotent per broker; 409 EXISTS) with its schedule; the account
trades the US set DVP with the PB's fees (the PB "delivers or receives the shares" = ordinary settlement rows in that
account); `pb_financing` switch enables `credit.pb_draw/repay` and the automatic draw in `fund()`; interest nightly
(`accr:pb`), paid monthly; PB credit interest on positive settled USD and the monthly minimum per §12.4. Risk shows
exposure per PB. OTC swaps (IRS / TRS / cross-currency) via PB are in this build per §13.11–§13.12 (eligible
lifetimes only, §13.10); OTC options, FRAs, caps and forwards answer 400 `NOT_IN_THIS_BUILD` until Build 2.

### 13.8 Build 2 — Debt Instruments (`products/bonds.py`, `quotes/rates.py`)

Benchmarks per tenor (1M 3M 6M 1Y bills; 2 3 5 7 10 20 30Y) listed on the first business day of each month: issue =
listing date, maturity = issue + tenor, coupon = par yield at listing rounded to 1/8 % (bills zero coupon), semi-annual,
ACT/ACT, named "US Treasury 4.250 % 15-Aug-2036 (model benchmark)"; curated corporates from `universe.bonds`
(coupon, years → maturity = 2026-09-18 + years, rating → OAS bucket), label "corporate, computed price". Marks per
§8.6 (`computed`, never executable in Build 1; Build 2 enables DEBT execution at the computed clean price + accrued,
disclosed as "model price"). Fill: `Dr 1110 clean / Dr 1220 accrued / Dr 5000 / Cr 2100` T+1; nightly accrual
`Dr 1220 / Cr 4300`; coupons `Dr 1010 / Cr 1220` on schedule (rolled following); maturity = MATURITY fill at 100;
shorts allowed with a borrow (coupons in lieu 5400). US_BONDS session 08:00–17:00.

### 13.9 Build 2 — the rest

- **Foreign listings:** `ensure_foreign_listing` from Yahoo meta; session from `exchange_sessions`; T+2 (per venue);
  `price_scale` 0.01 for pence; FOREIGN_FX account; FX needs via the FX leg.
- **FX forwards:** `fx.forward` at the CIP rate (`computed`), `fx_deals(kind FORWARD)`; no cash until value date; PV
  overlay `(F_now − K) × amount × DF` in NAV; at value date two settlement legs at K.
- **Repo / reverse repo (`repo.py`):** `repo.open {side, bond, face, term}`: price = bond mark (computed), principal =
  face × px/100 × (1 − haircut); REPO `Dr 1010 / Cr 2500` and the bond flagged encumbered (`BOND_ENCUMBERED` on sells);
  REVERSE `Dr 1500 / Cr 1010`; nightly accrual; close at maturity.
- **Securities lending out:** lend held shares to the street at the tier rate (income 4350-style), recallable by the
  owner; shown on the Credit page.
- **Dividends:** moved to Build 1 — §13.15.
- **Recall notices** from observable data stay INFO; **splits** adjust lots (`split:{inst}:{date}`), options flagged.
- **PB OTC:** swaps moved to Build 1 — §13.11–§13.12; OTC options / FRAs / caps / forwards stay Build 2 (priced with
  `finsim.engines.otc_pricing.black76` / `cap_pv` from the live curve, labelled computed, under the same `otc_contracts`
  table).
- **SEC shares outstanding** weekly (`company_tickers.json`, companyfacts, ≤ 10 req/s, GOV_UA) for 13D/G notices.

---

### 13.10 Books and lifetimes (`books.py`, `lifetimes.py`; conventions BOOKS, LIFETIME, ELIGIBILITY, QP; the 17:09 / 17:13 / 17:41 amendments)

- **Three books on first run** (`config.BOOKS`): Personal (P, INDIVIDUAL, USD 1,000,000), Fund (F, FUND, USD
  25,000,000), Institutional (I, INSTITUTION, USD 1,000,000,000). A **lifetime** is one run of a book: `P1`, `P2`, …;
  every account id is `{lifetime_id}:{role}`, so every ledger row, order, fill, borrow, loan, swap, snapshot and
  settlement belongs to exactly one lifetime through its account (nothing else is keyed by book). Books never transfer
  to each other (`treasury.transfer` across lifetimes → 400 `CROSS_BOOK`); consolidated views simply sum.
- **Eligibility (`books.eligibility`)**: `nav_c` = `nav({lifetime}:ALL)`; `ecp = nav_c ≥ ECP_FLOOR_C`; `pb_otc =
  profile in (FUND, INSTITUTION) or nav_c ≥ settings.pb_otc_threshold_c` (the Fund and Institutional books are eligible
  from day one — their first lifetimes start at or above the threshold; a FUND lifetime reset to less than the threshold
  is judged by NAV like the Personal book); `has_relationships` = any `agreements` row; once relationships exist they
  stay (a NAV fall below the threshold produces an INFO attention `ELIGIBILITY_BELOW_THRESHOLD`, nothing closes);
  `private_markets = nav_c ≥ settings.qp_threshold_c[profile]` (0 for INSTITUTION → always). The sidebar shows the
  state ("Prime brokers & OTC: unlock at USD 25,000,000 NAV — now 1,000,000") with the sentences ELIGIBILITY / QP.
- **Onboarding (`book.onboard_pb {lifetime_id, broker}`)**: requires `pb_otc`; records three `agreements` rows
  (ISDA_MASTER, CSA, PBA, `signed_date` = today) and calls `account.open_pb` in the same transaction; idempotent per
  (lifetime, broker). The opener modal shows the schedule and the one-sentence meaning of each agreement (ISDA Master +
  CSA for OTC/collateral terms; GMRA repo and MSLA/GMSLA securities lending are named as the real-world documents for the
  Build 2 products). In an ineligible lifetime the modal is disabled with the reason; the API answers 400 `NOT_ELIGIBLE`.
- **Private Markets account**: created with the lifetime for INSTITUTION; for the others `books.unlock_private()` (called
  by the SNAPSHOT job and by `GET /api/books`) creates `{lifetime}:PRIVATE` the first time `private_markets` is true
  (the account then stays; an INFO attention announces it).
- **Reset lifetime (`book.reset_lifetime {book_code, starting_capital_c, confirm: "RESET", name?}`)** — one command,
  one transaction, request_id from the UI (typed confirmation "RESET"): (1) every WORKING/PARTIAL/SUSPENDED/
  WAITING_PARENT order of the lifetime → CANCELLED with reason "lifetime reset" (order_events + events; borrows backing
  only cancelled short orders are returned per §13.1 step 6 in the same transaction so L12 holds at the freeze); (2)
  `integrity("full", lifetime_id)` on the closing lifetime (a failure does NOT block the reset — it is recorded on the row
  and as an ERROR attention; nothing is deleted); (3) the lifetime row gets `ended_ts`, `final_nav_c` (nav at the current
  marks, `final_quality`), `final_realised_c` (Σ natural 4000+4400+4450 of its accounts), `final_pnl_c` = `final_nav_c −
  starting_capital_c − Σ deposits + Σ withdrawals`, `reason`, `status CLOSED`; a last `nav_snapshots` row
  `{lifetime}:ALL` for today is written (so History shows the final day); every account of the lifetime → `status
  CLOSED` (`ledger.post` refuses CLOSED accounts, so the archive is read-only by construction; L23); open borrows/loans/
  swaps/private loans keep their rows and status (frozen, shown in History with "frozen at reset"); (4) a new lifetime
  `{code}{seq+1}` is created with the typed capital (`init:capital:{new}`), the catalogue accounts, no PB accounts,
  sweeps on. Nothing in `instruments`, `marks`, `quote_cache`, `curve_points`, `news_items` changes (shared reference
  data). The UI switches to the new lifetime; Settings → Lifetimes lists past lifetimes (name, start, end, starting
  capital, final NAV, P&L, reason, integrity ok/failed) and "History" opens `#/h/{lifetime}/…` read-only pages
  (Portfolio, Accounting incl. transactions CSV, Activity) served by the same read models with the archived scope.
- **Settings (`book.update_settings`, light)**: `pb_otc_threshold_c`, `qp_threshold_c`, `book_defaults` (names and
  starting capitals for NEW lifetimes) — the live lifetime's history is never rewritten; the sidebar name of a book
  changes at once (display only).
- Performance statistics (§6.9), P&L baselines and integrity are per lifetime; `ALL_BOOKS` consolidates the three OPEN
  lifetimes for Portfolio and Accounting (sentence BOOKS).

### 13.11 PB OTC swaps — IRS core, variation margin, resets (`products/swaps.py`; conventions SWAP_MARK, SWAP_VM, SWAP_RESET; the 17:03 / 17:07 amendments)

- **Booking (`otc.preview` → `otc.book {account_id, kind: "IRS", terms}`)** in a PB account of a lifetime that passes
  `require_pb_otc`. IRS terms: `{notional_c, currency: "USD", pay_fixed: bool, tenor_months, fixed_rate | null (null =
  par), fixed_basis: "30/360", fixed_months: 6, float_index: "SOFR", float_basis: "ACT/360", float_months: 3,
  fixing: "TERM" | "COMPOUND_ARREARS"}` (`COMPOUND_ARREARS` compounds the daily observations of the period — the test
  uses TERM; other currencies use `tables.POLICY_INDEX` (ESTR/SONIA) where FRED has them, else `CURRENCY_UNSUPPORTED`).
  The par rate comes from the live par curve (discount factors bootstrapped from `curve_points`, linear in maturity,
  semi-annual; the FixedCurve fixture of §19.15 gives dfs directly); the PB deals at **par ± half-width ×
  `otc_spread_mult`** (pay fixed: par + hw; receive fixed: par − hw) — the only "quote" is this disclosed model rate
  (there is no observable OTC market; sentence SWAP_MARK). Booking posts IM `Dr 1330 / Cr 1010` (`fund()`), IM =
  `dv01_c × SWAP_IM_BP` (static, disclosed), creates the `otc_contracts` + `instruments(kind SWAP)` rows, the schedules
  (`finsim.engines.otc_pricing.schedule`, modified following on the trading calendar), and records `mark_c = mark0`
  (= −hw × annuity: the dealer's spread is the day-one P&L, shown in the preview as "cost of dealing"). Fees: none
  beyond the half-width (disclosed).
- **Mark (`swaps.mark`)**: clean PV = PV(receive leg) − PV(pay leg) from the latest curve and the booked fixings
  (`finsim.quant.swaps.irs_pv` with floats → `D(repr())`), excluding the current period's accrued coupons (those are in
  1225/2340); `dv01_c` = PV change for +1 bp parallel (same function). Quality `computed`; inputs {curve as_of, fixings}.
  Missing curve → the mark keeps its last value labelled stale and SWAP_VM skips.
- **Variation margin (job SWAP_VM, 17:05 every calendar day, key `swapvm:{contract}:{date}`)**: `Δ = mark_t −
  mark_{t−1}`. CTM (default): Δ < 0 → we post `|Δ|` (`Dr 1320 / Cr 1010`, `fund()` → Treasury sweep; §11.6 overdraft
  rule), Δ > 0 → we receive Δ (`Dr 1010 / Cr 2330`); `vm_balance_c += Δ`; when the balance crosses zero the single entry
  carries both the release of the held side and the posting of the other (so 1320 = max(0, −balance), 2330 = max(0,
  balance): L22). VM is collateral, not P&L: the P&L is the mark overlay. STM: Δ settles as P&L (`Dr 1010 / Cr 4450` or
  reverse) and `mark_c` resets to 0 (futures-style). Δ = 0 (weekend, unchanged curve) → SKIPPED "no change".
- **Accruals (ACCRUE_DAILY kinds swaprcv / swappay, §11.3 cumulative rounding per leg segment)**: fixed leg `notional ×
  fixed_rate × n / 360` (30/360 → each night counts as 1/360 of the year's coupon; the payment amount is the 30/360
  period fraction, so the last night of a period posts the difference between `to_c(period coupon)` and the cumulative
  — never a drift); floating leg at the fixed rate of the period, ACT/360. Receive leg `Dr 1225 / Cr 4450`, pay leg
  `Dr 4450 / Cr 2340`.
- **Resets and payments**: SWAP_FIX on each floating reset date fixes the period rate from the latest observation ≤ the
  reset date (RATE_LAG) and restarts the floating segment; SWAP_PAY on each payment date settles both legs net
  (`swappay:{contract}:{date}`: `Dr 2340 pay accrued / Cr 1225 receive accrued / Cr or Dr 1010 net`; `fund()` for a net
  payment). Mark and VM are re-measured against the post-reset terms so nothing is paid twice (the accrued coupons are
  never in the mark). Maturity: the last payment, IM released, status MATURED. `otc.terminate` (owner): the termination
  value = the current mark (model, disclosed) `Dr 1010 / Cr 4450` or reverse, VM balance unwound, IM released.
- Risk (§14): DV01 and exposure per PB from the current terms; margin headroom per PB includes 1330 and the VM balance.
  Cash & financing tab: an "OTC margin" block (IM, VM posted, VM received, next payment). Transactions show every VM,
  accrual, fixing and payment.

### 13.12 TRS and cross-currency swaps (`products/swaps_trs.py`; the 17:07 amendment)

- **TRS** terms: `{underlying_id, units, receiver: bool, spread (PB schedule TRS spread × otc_spread_mult), reset_months:
  1, tenor_months}`. Booking: `reset_px` = the underlying's observed mark (must be executable-fresh or CLOSE), notional
  `= units × reset_px`; IM = `to_c(0.10 × notional)` for TRS and `to_c(0.02 × USD-equivalent notional)` for XCCY
  (static, disclosed: "real PB schedules vary"), posted `Dr 1330 / Cr 1010` with `fund()`. Mark = `(S_now −
  reset_px) × units` (receiver sign; payer negative) from the underlying's mark (quality follows it). VM per §13.11.
  Financing accrues nightly (kind `trsfin`: notional × (SOFR + spread) × 1/360, `Dr 4450 / Cr 2340` for the receiver,
  reversed for the payer). Dividends on the underlying between resets (from the `dividends` table) accrue `Dr 1225 /
  Cr 4450` for the receiver (payer reversed). **TRS_RESET** (16:25 on each reset date, the underlying's CLOSE): the
  return leg `(S_t − reset_px) × units` and the dividend accrual settle in cash (`Dr 1010 / Cr 4450` or reverse), the
  financing accrued is paid (`Dr 2340 / Cr 1010`), `reset_px = S_t`, notional reset, VM balance re-measured against the
  post-reset mark (0 at the reset instant) so nothing is paid twice.
- **Cross-currency** terms: `{pay_ccy, recv_ccy, pay_notional_c, recv_notional_c (at the deal FX rate), pay_rate,
  recv_rate (fixed or float per leg), basis per leg, months, mtm_reset: bool}`. Start: notional exchange
  (`xccy:{id}:start`: `Dr 1010:recv / Cr 2360:recv` and `Dr 1360:pay / Cr 1010:pay`, each leg balanced in its own
  currency at the deal rate; `fund()` for the paid currency from that currency's cash, else Treasury's, else refuse
  `INSUFFICIENT_FUNDS_CCY` with the FX hint — never automatic FX). Coupons: each leg as an IRS leg in its currency
  (accruals in that currency; base at the command's rate). Maturity: the reverse exchange. **MTM reset** (`mtm_reset`):
  on each reset date at the FX CLOSE mark the USD leg's notional is re-struck to `pay_or_recv_notional × fx_t` and the
  difference settles in cash (`xccyfx:{id}:{date}`: notional up → we receive the difference `Dr 1010:USD / Cr 2360:USD`
  when USD is the received leg; mirrored otherwise). Mark: the two legs' clean PVs in their currencies converted at
  `fx()` plus the unreset notional FX effect; VM per §13.11.

### 13.13 Private Markets (`products/private.py`; conventions PRIVATE_MARK, PRIVATE_TRADE, QP; the 17:48 amendment)

- **Company master (`data/private_companies.json`, seeded into `private_companies` / `private_rounds` / `instruments`
  kind PRIVATE on first run; HOUSEKEEPING re-seeds new rows from the file, never overwriting owner-recorded rounds)**:
  ~25 real companies (Anthropic, OpenAI, SpaceX, xAI, Stripe, Databricks, Anduril, Scale AI, Perplexity, Mistral, Safe
  Superintelligence, Anysphere (Cursor), Groq, Revolut, ByteDance, Canva, Epic Games, Discord, Fanatics, Kraken, Ramp,
  Rippling, Deel, Vercel, Cerebras …). Each row: `name, sector, country, status (PRIVATE | FILED_IPO | PUBLIC + ticker),
  last_round: {round_date, round_type, post_money_usd, price_per_share, lead_investors, source_url, as_of}`. **The
  package that writes this file researches every figure with web search at build time and cites it; a figure that cannot
  be sourced is `null`, never estimated**; the test asserts that every non-null number has a `source_url` and `as_of`.
  The UI shows the source and as-of date next to the mark. `private.add_company` / `private.record_round` (light
  commands; dialog: date, round type, post-money, price per share, lead investors, source URL — the source URL is
  mandatory) append `recorded_by = owner` rows; a company set to PUBLIC with a ticker switches its mark to that listed
  instrument from the listing date (the PRIVATE lots stay, valued at the listed mark; sale through the normal ticket is
  Build 2 — the owner sells "secondary" at the listed price until then, disclosed).
- **Marks**: `private_mark()` = the latest round's `price_per_share`; when only `post_money_c` is known, `post_money ×
  owned_fraction` cannot be computed without shares outstanding → the mark is **unavailable** with the note "valuation
  only: no price per share disclosed" (the amendment's "valuation × owned fraction" needs a share count the sources
  rarely give; left blank, never guessed). Quality `private`, never dimmed as stale, label "last round <date>".
- **Trades (`private.trade {account_id, company_id, side: BUY | SELL, qty, price, kind: SECONDARY | ROUND, fee_pct,
  settle_days, lockup_months, request_id}`)** in `{lifetime}:PRIVATE` after `require_private`: price defaults to the last
  round's price (± the discount/premium field in the ticket); fee `to_c(qty × price × fee_pct)` (default 3 %); BUY needs
  `fund()` for gross + fee at the fill (settlement T+`settle_days` **calendar** days — the SETTLE_DUE job pays it on or
  after the due date; the preview says "pending until <date>"); the fill (`origin NEGOTIATED`, `price_basis negotiated`,
  `quote_json = {mark: last round, source_url}`) posts per §5.4 and opens a LONG lot with `lock_until = trade_date +
  lockup_months` (calendar months). SELL: FIFO lots; inside the lock-up a **warning** `LOCKUP` ("lock-up until <date>;
  transfer restrictions apply (Rule 144)") — never a block; realised to 4000. "Participate in round" = BUY with
  `kind ROUND` at the round price (fee 0 by default; disclosed). No counterparties, no fills, no prices are invented: the
  preview says in one sentence that private secondaries are negotiated and execute at the price you enter (sentence
  PRIVATE_TRADE). Sides SELL_SHORT / BUY_TO_COVER are `NOT_TRADEABLE_HERE`.
- Risk (§14): bucket "Private / illiquid" (% of NAV, top names, lock-up calendar); liquidity shows private MV as
  illiquid; scenario shocks are not applied to private marks (table note). Compliance (§12.5 profiles).

### 13.14 Private credit (`products/private_credit.py`; convention PRIVATE_CREDIT; B10, directive §11)

- **Commit (`pc.commit {account_id (CREDIT), company_id | borrower, currency, principal_c, issue_price ("98.00" =
  2 % OID), index: "SOFR", spread, term_months, payment_months: 3, request_id}`)**: cash out `principal × issue_price /
  100` (`Dr 1130 / Cr 1010`, `fund()`); `private_loans` row + `instruments(kind BOND, support COMPUTED, id "PC-000001")`;
  `oas_issue` = the latest FRED `BAMLH0A0HYM2` (null when unavailable → the mark is "amortised cost (no spread
  observation)"). Coupon = index + spread, floating, reset nightly from the latest observation (RATE_LAG).
- **Nightly (ACCRUE_DAILY kinds `pccpn`, `pcoid`)**: coupon `principal × (index + spread) × n / 360` (cumulative rounding
  per segment; `Dr 1220 / Cr 4300` loan id); OID accretion straight-line `(principal − cash_out) × n / total_nights`
  (total nights = calendar days start → maturity; `Dr 1130 / Cr 4300`).
- **Quarterly payment (PC_PAY on `next_payment_date`)**: `Dr 1010 / Cr 1220` of the accrued coupon; the date rolls by
  `payment_months` (modified following). **Amendment fee (`pc.amend_fee {loan_id, amount_c, memo}`)**: `Dr 1010 / Cr
  4320`, owner-recorded (no invented events). **Repayment / maturity (`pc.repay {loan_id, amount_c}`; PC_PAY at
  maturity)**: accrued coupon first, then principal `Dr 1010 / Cr 1130 amortised cost / Cr(Dr) 4000 difference`
  (e.g. a par repayment before full accretion realises the unaccreted OID). No defaults, ratings or reviews are
  simulated (the old engine's `_review`/`_h_defaulted` are not carried over).
- **Mark (`pc_mark`)**: `pc_model` per §8.6: `amortised_cost_c × (1 − ΔOAS × T_remaining)` — the amortised cost
  (1130 balance) adjusted first-order for the change in the FRED high-yield OAS since commitment times the remaining
  life in years (ACT/365), `to_c` HALF_EVEN; equals the amortised cost when the OAS is unchanged or unobserved (note
  "amortised cost (no spread observation)"); quality `computed`; shown with the inputs (OAS as-of at issue and now,
  days remaining). P&L: `pc_mark − amortised cost` is unrealised; coupons and OID are income (4300), fees income (4320).
  No PV-of-cash-flows model is attempted: it would need a term structure of private-loan spreads that no free source
  gives, and a disclosed first-order adjustment is more honest than a precise-looking number.

### 13.15 Dividends and payments in lieu (`products/dividends.py`; convention DIVIDEND; B9/B11, moved from Build 2)

- The quote worker (`RatesRefresher` slot, daily after 18:00 ET and at startup) fetches `YahooChart.events(symbol)`
  (`/v8/finance/chart/{sym}?range=3mo&interval=1d&events=div,splits`) for every instrument held long or short in any
  OPEN lifetime and upserts `dividends(instrument_id, ex_date, amount, pay_date NULL, source "Yahoo chart events")`
  (Yahoo gives the ex-date and amount only — never a pay date — so `pay_date` stays NULL and the convention ex + 15
  business days applies, labelled on the row); a `splits` event raises Attention WARN `SPLIT_MANUAL_REVIEW` for held
  names (lots are not adjusted in this build; §13.9).
- **DIVIDEND job (00:40 on the ex-date)**: for each account holding the name at the prior close (the position at
  `ex_date − 1` from `v_positions` and fills before the ex-date's session): long `Dr 1210 / Cr 4200 to_c(qty ×
  amount)` (`div:{account}:{inst}:{exdate}`); short `Dr 5400 / Cr 2320` (payment in lieu); **DIVPAY** on the pay date
  `Dr 1010 / Cr 1210` or `Dr 2320 / Cr 1010` (`divpay:…`). TRS receivers/payers accrue the same amounts on their
  contracts (§13.12). Catch-up after downtime: `due()` derives keys from `dividends` rows with `ex_date ≤ today` not in
  `jobs_done`. Positions show `income_pending_c` and the row note "dividend 0.26 ex 2026-11-07, pays 2026-11-30
  (convention)".

### 13.16 Strategy templates (`strategies.py`; B8 — the old Calculations page; the build prompt's "38" was an earlier count, the shipped catalogue has 60)

- `STRATEGIES` is a verbatim copy of `finsim/engines/playbook.py::STRATEGIES` (60 entries, 12 `GROUPS`: long stock +
  hedge, short stock + hedge, outright bullish/bearish, income, bullish spreads, bearish spreads, volatility & range,
  calendar & diagonal, total return swaps, futures, relative value, advanced & repair) with the leg rules unchanged:
  `kind STOCK | OPTION | FUTURE | TRS`, `side`, `size` (multiple of the base units), option `type C | P`, `strike "ATM" |
  "pct:<n>" | "zero_cost"`, optional `tenor` (months) per leg, `other: true` for the second name (relative-value
  templates).
- **Resolution (pure, tested in `test_terminal_strategies.py`)**: `resolve_strike(rule, S, listed)` → `target = S` (ATM)
  or `S × (1 + n/100)`; the nearest listed strike (ties → the lower, as the old `min(key=(abs, k))`); an absolute
  override (|value| ≥ 60) picks the nearest listed to that value, a small override is a percent. `zero_cost_call(rows,
  put_ask, S)` → the call strike above spot whose bid is nearest the put's ask (fallback `pct:+10`). `expiry_for_tenor`
  → the nearest listed expiry on or after `today + months` (calendar months; `None` → the template is "not available:
  no listed expiry ≥ tenor"). Sizing per leg: stock `qty = round(units × size)`; option `contracts = max(1, round(units
  × size / 100))`; futures `n = max(1, round(units × size × S / (F × multiplier)))` against the front ES (NQ for Nasdaq
  underlyings) — notional-matched; TRS `units × size` through the account's PB (eligible lifetimes only, else the leg is
  marked NOT ELIGIBLE and the template cannot be submitted from that account). Prices: options ask/bid from the chain,
  stock from the instrument record (Cboe underlying ask/bid when a fresh chain is cached, else last), futures last, TRS
  the PB's dealt spread. Outputs per leg: `instrument_id, side, qty, est_price, cash, delta_shares, vega, theta, strike,
  expiry, pct_from_spot`; totals: net cash, net premium, delta, vega, theta, incremental option margin (§13.3),
  futures IM; the payoff grid ±35 % at the near expiry with max gain / loss / breakevens as the old `_payoff`
  (unlimited flags for naked legs; a second name assumed to move with the underlying, disclosed).
- **Execution**: `order.submit` with `package` (§10.1) → `fill_mode AON` for option-only templates (one underlying,
  one or two expiries), else SEQUENTIAL with the stock/futures leg as the primary and the option/TRS legs as hedge
  legs (`ratio` = leg qty / primary qty; resized on partial fills; a short stock leg goes through the borrow first).
  Everything the old Calculations page previewed (every leg off today's quotes, option margin, the dealer spread for
  TRS) stays in the preview (directive §8). The 38/60 templates appear in the ticket's hedge panel ("Strategies" tab)
  and in the instrument workspace's chain panel ("Package builder").

## 14. Risk warnings and compliance notices (`risk.py`, `compliance.py`) — inform only

`risk.report(services, scope)` → `RiskView {exposure_by_class, exposure_by_account, exposure_by_currency, greeks_by_underlying
(delta/gamma/vega/theta from the chain's greeks × contracts × 100, signed), dv01 (futures ZN/ZB via tables.FUT_DV01;
bonds Build 2), fx_exposure, scenarios (equities ±5/10/20 % with option re-pricing through option_model; rates
±25/50/100 bp; vol ±5 pts; FX ±5 %), concentration (top 10 % NAV), financing_liquidity (available, Treasury headroom,
margin excess = NAV − Σ MM, collateral posted, PB headroom per PB incl. OTC IM/VM (§13.11), excess liquidity and
encumbrance (§6.0)), private_illiquid (MV, % of NAV, top names, lock-up calendar; scenario shocks are NOT applied to
private marks — table note), otc (per PB: contracts, DV01 from the current post-reset terms, mark, VM balance, next
reset/payment), warnings}`. Scenario re-pricing: equities/ETFs/futures linear; options through `option_model`; IRS
through `swaps.mark` with the curve shifted; TRS with the underlying; FX legs at the shifted rate; private marks and
private loans unchanged (disclosed). **Warnings** (preview, Risk page, Attention strip; never block): concentration >
25 % NAV; gross leverage > 2×; option margin > 50 % of account NAV; stale-mark positions; loans > 50 % of Treasury
capital; futures maintenance shortfall; OTC VM posted > 10 % of the PB account's NAV; private / illiquid > 30 % of NAV.
Every figure ties to `accounting.positions`; scopes are per lifetime or `ALL_BOOKS` (§6.0). Compliance notices follow
the book's profile (§12.5).

`compliance.pretrade(...)` and `compliance.holdings(...)` → `[Notice {rule, status: ok|notice|breach, threshold, value,
data_used, message, verify: "verify the current rule text"}]`: Reg SHO 203(b) locate (satisfied by the borrow leg);
Rule 201 price test (quote `day_low ≤ 0.90 × prev_close` → short only on an uptick: notice); Reg T / FINRA 4210 50 %
initial on margined equity (informational; trades are fully funded); PDT (4+ day trades in 5 business days with NAV
< 25,000, informational); 13D/G 5 % and Section 16 10 % of `shares_outstanding` (from the master; "shares outstanding
as of <date>"); 13h-1 (2 M shares or USD 20 M in a day); Form SHO / 13f-2 (USD 10 M gross short); 13F (USD 100 M in
13F securities); listed option position limits; CFTC/CME spot-month limits; self-trading (an opposite-side working
order or a fill within 60 s in another own account on the same instrument); wash sale (IRS 1091: a loss sale with a buy
of the same instrument within 30 days, informational); EU/UK 0.1 % / 0.5 % short disclosure for foreign names (Build 2).
None of this is a compliance system (TERMINAL_DECISIONS §4).

---

## 15. HTTP API (`api.py`)

Conventions: JSON in/out, `Content-Type: application/json`, `Cache-Control: no-store`; loopback only (bind 127.0.0.1,
no access key in this build). Every mutating route (POST/PATCH/DELETE) requires `request_id` in the body (or header
`X-Request-Id`); missing → 400 `BAD_REQUEST_ID`. Cents fields are decimal strings under the same name without `_c`;
dates ISO; epochs as integers plus `*_ny` display strings. Mark-dependent fields are `{value, quality, as_of, source}`.
Errors: `{"error": {"code", "message", "details"}, "request_id"}` with 400 validation, 404 not found, 409 conflict,
500 internal (`incident_id`), 503 retry (`Retry-After: 1`). A replayed request returns the stored status and body plus
`X-Replayed: 1`. `/api/shutdown` and `/api/jobs/run` are loopback-only (403 otherwise).

| method | path | params / body | response | errors |
|---|---|---|---|---|
| GET | /api/health | — | `{app: "finsim-terminal", version, schema, pid, uptime_s, started_ts, db_path, db_size, state_version, offline: bool, clock: {utc, ny, trading_day, accounting_day}, integrity: {ok, hold, checked_at, problems}, providers: {yahoo|cboe|treasury|fred|finra|frankfurter: {ok, last_ok_ts, last_error, consecutive_errors, down_until_ts, refused_ua?}}, workers: {quotes|lifecycle|matcher|supervisor: {alive, last_tick_ts, stalled, errors_1h, last_error}}, lifecycle: {due, failing: [...]}, attention_open, calendar: {file, fallback_years}, lock: {path, pid}}` | never blocks on the writer lock; answers only after a complete start |
| POST | /api/shutdown | loopback | `{shutting_down: true}` | 403 |
| GET | /api/state | — | bootstrap bundle: `{accounts, settings, sessions, clock, attention, integrity, version, conventions: {key: sentence}}` | |
| GET | /api/sessions | — | `{group: SessionState}` + rule sources | |
| GET | /api/calendar | `year?` | holidays, early closes, source | |
| GET | /api/accounts | — | `[{account_id, name, kind, broker, status, sweep_out, sweep_buffer, pb_financing, nav, available_usd, working_orders, pending_settlements, attention_count}]` | |
| POST | /api/accounts/pb | `request_id, broker` | the new account + schedule | 400 UNKNOWN_BROKER, 409 EXISTS |
| PATCH | /api/accounts/{id} | `request_id, sweep_out?, sweep_buffer?, pb_financing?, pb_limit?` | the account | 404 |
| GET | /api/accounts/{id} | — | `{account, cash: {ccy: CashView}, buying_power: {ccy: BuyingPower}, nav, pnl: {TODAY, MTD, YTD, ITD}, positions_count, working_orders, pending_settlements: [...], borrows, loans, margin: {futures_required, futures_posted, options_required, options_posted}, collateral: {held_at_lender}, sweeps_today, attention, marks_partial, as_of}` | 404 |
| GET | /api/accounts/{id}/positions | `kind?` | `[PositionView]` with `mark: MarkView`, `lots` | 404 |
| GET | /api/accounts/{id}/positions/{instrument_id} | — | `{position, lots, fills, entries, borrow, marks_history}` | 404 |
| GET | /api/accounts/{id}/orders | `status=open|all, limit?, offset?` | `[OrderView]` (with the last event, fills) + rejections (from `order_rejections`) | 404 |
| GET | /api/accounts/{id}/fills | `from?, to?, instrument?` | `[Fill]` with `quote` provenance | 404 |
| GET | /api/accounts/{id}/transactions | `from?, to?, kinds?, instrument?, limit?, offset?, format=json|csv` | `[TxnRow]` (every journal entry; lines expanded; net cash by ccy) | 404 |
| GET | /api/accounts/{id}/cash | — | cash by ccy + transfers history + settlements ladder (next 5 business days) + loans + borrows + margin | 404 |
| GET | /api/accounts/{id}/markets | `tab?` | the account page lists (US_EXCHANGE: equities watch+held / options underlyings / futures front months by group; CREDIT: loans, borrows, rates; FOREIGN_FX: fx pairs (+ listings Build 2); DEBT: curve + bonds (LIMITED); PB: as US_EXCHANGE + OTC placeholder) each row with `mark` and `support` | 404 |
| GET | /api/portfolio | — | consolidated: `{nav, pnl: {...}, by_account, by_class, positions, marks_partial}` | |
| GET | /api/treasury | — | `TreasuryView` + advances per account + capital history + sweep log | |
| POST | /api/treasury/deposit | `request_id, currency, amount, memo?` | `{transfer, treasury}` | 400 AMOUNT |
| POST | /api/treasury/withdraw | same | same | 409 INSUFFICIENT_FUNDS |
| POST | /api/transfers | `request_id, from_account, to_account, currency, amount, memo?` | `{transfer, from_cash, to_cash}` | 409 INSUFFICIENT_FUNDS |
| GET | /api/instruments | `q, kinds?, account?, limit?` | search rows `[{instrument_id, symbol, name, kind, currency, support, quote}]` | |
| GET | /api/instruments/resolve | `symbol` | the instrument, or 202 `{status: "resolving"}`; 404 NOT_FOUND with Yahoo's reason; 400 UNSUPPORTED (foreign/crypto) | |
| GET | /api/instruments/{id} | — | `Instrument + quote + mark + session + support + spec + positions in each account` | 404 |
| GET | /api/instruments/{id}/bars | `range=1d` | `{bars: bars_tail (or the worker's 1-minute bars when subscribed), prev_close, session}` | 404 |
| GET | /api/instruments/{id}/chain | `expiry?` | `{underlying: QuoteRecord, expiries, rows: [{strike, call: ChainRow+instrument_id+quality, put: ...}], as_of, delay_label, quality}` or 202 `{status: "fetching", retry_ms: 1500}` | 404, 409 NO_CHAIN, 503 CBOE_REFUSED_UA |
| GET | /api/quotes | `ids=a,b,c` (≤ 200) | `{quotes: {id: QuoteRecord + quality + age_s}, sessions, server_time}`; side effect `cache.subscribe(ids, 90)` | 400 TOO_MANY |
| GET | /api/curve | — | `{par, as_of, shifts, quality}` | |
| GET | /api/rates | — | `{SOFR: {value, as_of}, policy: {ccy: {...}}, oas: {...}}` | |
| POST | /api/orders/preview | §10.1 fields (no request_id) | `Preview` (§10.3); validation failures as `blockers`, not errors | 400 malformed |
| POST | /api/orders | `request_id` + §10.1 | `{order, fills, sweeps, borrow?, hedge_package?, warnings}` | 400 validation; 409 QUOTE_STALE / NO_BORROW / INSUFFICIENT_FUNDS / CONFIRM_REQUIRED / PREVIEW_STALE (body carries the fresh preview and `diff`) / INTEGRITY_HOLD / FUNDING_SHORTFALL |
| GET | /api/orders/{id} | — | `OrderView + events + fills + entries` | 404 |
| DELETE | /api/orders/{id} | `request_id` | `{order}` (CANCELLED) | 409 NOT_OPEN |
| POST | /api/orders/{id}/resume | `request_id` | `{order}` | 409 NOT_SUSPENDED |
| GET | /api/credit/locate | `account_id, instrument_id, qty` | `LocateQuote` | 404 |
| POST | /api/credit/borrows | `request_id, account_id, instrument_id, qty` | `{borrow, collateral_transfer?}` | 409 NO_BORROW / INSUFFICIENT_FUNDS |
| POST | /api/credit/borrows/{id}/return | `request_id, qty` | `{borrow}` | 409 BACKS_SHORT |
| POST | /api/credit/loans | `request_id, account_id, currency, amount, term_days?` | `{loan, rate: {index, as_of, value, spread}}` | 400 CURRENCY_UNSUPPORTED |
| POST | /api/credit/loans/{id}/repay | `request_id, amount` | `{loan, paid_interest, paid_principal}` | 409 INSUFFICIENT_CASH_CCY |
| POST | /api/credit/pb/{account_id}/draw, /repay | `request_id, amount` | `{account, cash}` | 409 |
| POST | /api/fx/preview | `account_id, buy_ccy, sell_ccy, amount, amount_ccy` | `{mid, rate, buy, sell, spread_cost, value_date, quote, legs}` | 409 NO_QUOTE |
| POST | /api/fx/spot | `request_id` + preview fields | `{deal, settlements, cash}` | 409 QUOTE_STALE / INSUFFICIENT_FUNDS_CCY |
| POST | /api/fx/forward | Build 2 | | 400 NOT_IN_THIS_BUILD |
| POST | /api/options/exercise | `request_id, account_id, instrument_id, qty` | `{fills, position}` | 409 NOT_LONG / SESSION_CLOSED |
| GET | /api/hedges | `account_id, instrument_id, side, qty` | `[HedgeCandidate]` or 202 while the chain is fetching | |
| GET | /api/accounting/balance-sheet | `scope=ALL|id` | `BalanceSheet` (rows at cost + overlays + NAV + memo ties) | 404 |
| GET | /api/accounting/pnl | `scope, period` | `PnlView` with explain and residual | 404 |
| GET | /api/accounting/trial-balance | `scope` | rows per (gl, ccy) with debit/credit totals, `balanced` | |
| GET | /api/accounting/journal | `scope?, from?, to?, gl?, instrument?, limit?, offset?` | entries with lines | |
| GET | /api/accounting/journal/{entry_id} | — | the entry, lines, command, linked refs | 404 |
| GET | /api/accounting/integrity | `mode=ledger|full` | `IntegrityReport` (runs synchronously on a reader) | |
| GET | /api/risk | `scope?` | `RiskView` | |
| GET | /api/compliance | `scope?` | `[Notice]` (holdings) | |
| GET | /api/activity | `limit?, account?, type?, since?` | merged newest first: events, fills, transfers, job results, attention | |
| GET | /api/attention | `all?=1` | rows | |
| POST | /api/attention/{id}/resolve | `request_id` | row | 404, 409 PERSISTENT (integrity / provider items cannot be resolved while they persist) |
| GET | /api/jobs | — | `{jobs: [{name, last_run_ts, last_key, next_due, failing}]}` | |
| POST | /api/jobs/run | `request_id, job?` (loopback) | `{ran: [...]}` | 403 |
| GET / PATCH | /api/settings | PATCH `request_id, {key: value}` | settings map (incl. `pb_otc_threshold_c`, `qp_threshold_c`, `book_defaults`, `private_defaults`) | 400 |
| GET | /api/books | — | `[{book_code, name, profile, lifetime: {lifetime_id, seq, started, starting_capital}, nav, eligibility: Eligibility, accounts: [...], past_lifetimes: n}]` (the sidebar switcher; also triggers `books.unlock_private`) | |
| GET | /api/books/{code}/lifetimes | — | every lifetime of the book newest first with the stored final figures and `integrity_ok` | 404 |
| POST | /api/books/{code}/reset | `request_id, confirm: "RESET", starting_capital, name?` | `{closed: LifetimeView, opened: {lifetime_id, accounts}}` | 400 CONFIRM, 400 AMOUNT, 409 (an integrity failure is reported inside the body, not an error) |
| POST | /api/books/{code}/onboard | `request_id, broker` | `{agreements: [...], account}` | 400 NOT_ELIGIBLE, 400 UNKNOWN_BROKER, 409 EXISTS |
| GET | /api/history/{lifetime}/… | the read routes of this table under an archived scope (`/positions`, `/portfolio`, `/accounting/*`, `/activity`, `/export/transactions.csv`) | the same shapes, `read_only: true`, marks from the stored final snapshot | 404 |
| GET / PATCH | /api/accounts/{id}/schedule | PATCH `request_id, {field: value}` | the PB's `PbSchedule` (§12.4) | 400 field, 404 not a PB |
| POST | /api/otc/preview | `account_id, kind: IRS|TRS|XCCY, terms` | `{par_rate, dealt_rate, half_width, mark0, dv01, im, schedule: [...], margin_mode, conventions, inputs}` | 400 NOT_ELIGIBLE / CURRENCY_UNSUPPORTED, 409 NO_CURVE / QUOTE_STALE (TRS underlying) |
| POST | /api/otc/contracts | `request_id` + preview fields | `{contract, im_transfer?, entries}` | 400 / 409 as preview, 409 INSUFFICIENT_FUNDS |
| GET | /api/otc/contracts | `account_id?` / `scope?` | `[ContractView]` with mark, DV01, VM balance, IM, accrued legs, next events, `history: {vm: [...], resets: [...]}` on `?detail=1` | |
| POST | /api/otc/contracts/{id}/terminate | `request_id` | `{contract, termination_value, entries}` | 409 NOT_OPEN |
| GET | /api/private/companies | `q?` | rows with the last round, source_url, as_of, mark, status, ticker | |
| POST | /api/private/companies | `request_id, name, sector, country, status, last_round?` (source_url required for any number) | the row | 400 SOURCE_REQUIRED |
| POST | /api/private/companies/{id}/rounds | `request_id, round_date, round_type, post_money?, price_per_share?, lead_investors?, source_url` | the row + the new mark | 400 SOURCE_REQUIRED |
| POST | /api/private/preview, /api/private/trades | `account_id, company_id, side, qty, price, kind, fee_pct?, settle_days?, lockup_months?` (+ `request_id`) | preview `{gross, fee, net, settle_date, lock_until, mark, warnings: [LOCKUP?], sentence}` / `{fill, settlement, lot}` | 400 NOT_ELIGIBLE_QP / PRICE_REQUIRED, 409 INSUFFICIENT_FUNDS |
| POST | /api/credit/private | `request_id, account_id, company_id | borrower, currency, principal, issue_price, index, spread, term_months, payment_months?` | `{loan, mark, schedule}` | 400 CURRENCY_UNSUPPORTED, 409 INSUFFICIENT_FUNDS |
| POST | /api/credit/private/{id}/fee, /repay | `request_id, amount, memo?` | `{loan, entries}` | 409 |
| GET | /api/credit/private | `scope?` | `[PrivateLoanView]` (amortised cost, accrued, mark + inputs, next payment, OID accreted) | |
| GET | /api/strategies | `account_id?, instrument_id?` | `{groups, strategies: [{key, group, title, simple, thesis, best_when, risk, legs, available: bool, reason?}]}` | |
| POST | /api/strategies/preview | `account_id, key, params: {underlying, other?, units, tenor_months, strikes?}` | `PackageSpec` + per-leg prices + totals + payoff grid + `fill_mode` (202 while the chain loads) | 400 NOT_AVAILABLE (no listed expiry etc.) |
| GET | /api/dividends | `account_id?` | upcoming/recent ex-dates for held names with amounts, source, convention pay date | |
| GET | /api/news | `limit?, source?` | `{items: [{source, title, publisher, link, time, tickers, category}], attention: {integrity, warnings, compliance, collateral_calls_today, recalls, working_orders, providers}, fetched: {yahoo, fed, newspaper}, editions: [...]}` (numbers only from `accounting` / `attention`) | |
| GET | /api/portfolio/performance | `scope?` | `PerfView` (§6.9) or `{status: "insufficient history"}` | |
| GET | /api/unsupported | — | the §20 list with the state and the UI wording (rendered in Settings and in search) | |
| GET / POST / DELETE | /api/watchlists | `request_id, account_id, name, instrument_id` | lists | |
| GET | /api/export/transactions.csv, /api/export/positions.csv | `scope, from?, to?` | CSV | |
| GET | / and /static/* | — | the UI (`no-store`) | |

`api.Router.handle(method, path, query, body, client_ip) -> (status, body, headers)` is tested without sockets.
Mutating handlers build a `Command` and map `bus.execute` to HTTP; read handlers call `Accounting`, `QuoteCache`,
`MarkProvider`, `InstrumentMaster`, `SessionTable` on the request thread with a pooled reader — never the writer
lock, never the network. The only fetch-triggering reads (`/chain`, `/hedges`, `/resolve`, `/bars` for an
unsubscribed symbol) enqueue work and answer 202 until the cache has it.

---

## 16. Server, threading and operations (`server.py`)

```python
class TerminalServer(ThreadingHTTPServer):
    allow_reuse_address = False; daemon_threads = True; request_queue_size = 64
    def server_bind(self):      # Windows: setsockopt(SOL_SOCKET, SO_EXCLUSIVEADDRUSE, 1) before bind -> a second bind fails (WinError 10048)
class Runtime:                  # everything the process owns, passed as `services`
    db, clock, cal, sessions, instruments, cache, chains, providers, worker, marks, accounting, bus, matcher, lifecycle, router, httpd, log, lock, settings, started_ts,
    news (NewsStore | None), books (the books module bound to the runtime), rates, curve   # a missing optional product module leaves its attribute None
def serve(port=DEFAULT_PORT, home=None, db=None, offline=False) -> int     # exit code
```

Startup sequence (exit codes in brackets):
1. Acquire the single-instance lock `<home>/terminal.lock`: open, `msvcrt.locking(fd, LK_NBLCK, 1)` on Windows
   (`fcntl.flock` elsewhere), held for the process lifetime (the OS releases it on death, so no stale-lock
   heuristics); write `{pid, port, started_ts, version}` for messages [3: "another FinSim Terminal (PID n, started …)
   owns <home>"]. Never `os.kill(pid, 0)` anywhere (on Windows it terminates the process).
2. Logging: `finsim.log.get_logger("terminal", <home>/terminal.log, "INFO")` — a rotating file (5 MB × 3); under
   `pythonw` `sys.stdout/stderr` are `None`, so no StreamHandler writes are relied on; an import-time failure (for
   example a stray `ZoneInfo` call) is caught by `app.serve`, logged with the traceback to the file, and the process
   exits 5 so `status` can show the log tail.
3. Open the DB: backup → migrate [4: newer schema] → seed if empty → `init:capital:1` if absent → `integrity("ledger")`
   recorded (a failure sets INTEGRITY_HOLD and an ERROR attention; the server still starts so the owner can see it).
4. Load `InstrumentMaster`, `QuoteCache.load_persisted()` (records come back `close`/`stale`; the UI shows "last valid
   mark 16:00 yesterday" rather than nothing), curve/rates stores.
5. Bind the port exclusively [3: port in use — the message names the lock PID if any].
6. Start workers: `QuoteWorker` (scheduler + 2 fetch threads), `Matcher` (woken by the worker; 15 s sweep),
   `LifecycleScheduler` (30 s; first tick = catch-up, in its own thread so health answers at once), `Supervisor`
   (main thread, 30 s: restarts a dead worker thread, reports the death as an ERROR attention; a worker silent for
   > 3 of its cycles is itself an attention item "quote worker stalled").
7. `serve_forever()`. Health answers only from this point.

Threads: HTTP request threads (short-lived daemon), quote scheduler (1), fetch pool (2), matcher (1), lifecycle (1),
supervisor (main). The writer `RLock` is the only lock that serialises anything; a command holds it 1–20 ms; the
scoped integrity check is indexed on touched keys. Reads are lock-free (WAL, pooled connections). Network I/O never
happens while holding the lock. Chain fetches are serialised (single-flight) so the SPX parse never overlaps another.

Provider errors: counted in `provider_health` and surfaced in `/api/health.providers` and the Attention strip (one
open WARN per provider, auto-resolved on the next success; "Yahoo unreachable since 14:02 — quotes are stale;
executions paused"). Retries 3 (1/2/4 s); 429 → 60 s; 5 consecutive failures → 120 s doubling to 10 min; a
disconnected machine costs one request per provider every few minutes and the UI says "offline since 10:12, last
valid marks shown".

Memory budget: instruments ~1.5 k × 1 KB, cache 2 k × 1 KB, two reduced chains ~1.2 MB, runtime ~40 MB: < 150 MB
steady; SPX parse peaks ~120 MB for a second.

Graceful shutdown (`POST /api/shutdown`, Ctrl-C, SIGTERM, `stop`): set the stop event → workers finish their current
unit (≤ 3 s; a command in flight commits or rolls back) → `cache.persist()` → `httpd.shutdown()` from a helper thread →
`server_close()` → `db.close()` (WAL checkpoint) → release the lock → exit 0.

`serve --offline`: `FakeProvider` instead of the network; refused unless `--home` differs from the default and
`FINSIM_TERMINAL_DB` is unset; `/api/health.offline = true`; the UI paints the permanent red banner.

---

## 17. UI (`terminal/static/`, vanilla JS + CSS, no build step, no CDN, no frameworks)

### 17.1 Files

```
index.html                  shell: header, sidebar, main, right dock, attention strip, modal/toast roots; loads css + js in order
terminal.css                tokens + components (one file)
core.js                     T.api (fetch wrapper: request_id per click, retry same id ×3 on 503/network, X-Replayed aware), T.state (store + subscribe),
                            T.router (hash routes), T.poll (named pollers; pause when document.hidden; immediate fetch on resume),
                            T.fmt (numbers from decimal strings via Intl), T.keys (hotkeys), T.ui (toast, modal, confirm), T.conv (convention sentences from /api/state)
components/sidebar.js       mounts components/bookswitcher.js on top; then Daily News, Portfolio, Treasury, the lifetime's accounts with NAV/available,
                            "+ Open prime broker account" (with the eligibility state), global pages (§17.2 order)
components/header.js        NY clock, session chips, active-account card (§17.7), PAPER tag, integrity dot, offline banner
components/attention.js     the strip (health.attention + integrity + provider states), resolve buttons (not for persistent items)
components/freshness.js     T.px(rec, {size}) price cell renderer + tooltip (§17.3)
components/grid.js          dense table: sticky header, sort, filter, keyboard row nav, Copy, CSV
components/ticket.js        the single ticket (fields, keyboard map, live preview, submit, PREVIEW_STALE diff, CONFIRM_REQUIRED confirm)
components/preview.js       preview panel (legs, economics, funding, borrow, margin, warnings, compliance, blockers, conventions)
components/hedge.js         hedge candidate panel (auto-populated on instrument entry; pick/edit strike, expiry, ratio)
components/chain.js         option chain (calls | strike | puts; bid/ask/size/iv/delta/oi; freshness per row; click bid/ask -> ticket leg)
components/chart.js         canvas 1-minute line + volume from bars_tail / bars (no library)
pages/markets.js            account Markets tab (product tabs; row Enter -> instrument workspace; b/s -> ticket prefill; add symbol = resolve)
pages/instrument.js         workspace: quote header with freshness, chart, chain (if optionable), position & lots in this account, working orders, ticket docked
pages/positions.js          account positions (+ Close / Hedge / Exercise / Return borrow actions)
pages/orders.js             working + history (incl. rejections) with reasons; cancel with x; fills with the quote used
pages/cash.js               Cash & financing: cash by ccy (settled / pending / available / held at lender), settlements ladder, loans, borrows & collateral,
                            futures and option margin, sweeps today, FX convert form (preview -> confirm), loan form, per-account sweep switch
pages/transactions.js       per-account ledger-derived transactions (filters, CSV)
pages/treasury.js           capital (Add funds), cash by ccy, advances by account (click -> account), interest, sweep log, manual transfer, reserve
pages/portfolio.js          consolidated positions / P&L by account and class (B16 columns, §17.5); NAV + TODAY/MTD/YTD/ITD tiles; Performance tab (mounts components/performance.js)
pages/accounting.js         tabs: P&L (explain + residual), Balance sheet (scope incl. ALL), Ledger browser, Trial balance, Integrity (last report, "run now")
pages/risk.js               exposures, greeks, DV01, scenarios, concentration, warnings, compliance notices ("warnings only")
pages/activity.js           merged feed with filters
pages/watchlists.js         lists with live prices; add by symbol (resolve)
pages/settings.js           hotkeys, defaults, per-account sweep switches/buffers, PB financing, participation rate, session overrides, theme, Data Status panel, legacy link (8766);
                            sections: Books (names, starting capitals for new lifetimes, thresholds), Lifetimes (Reset lifetime + past lifetimes; mounts components/lifetimes.js),
                            Prime brokers (fee schedules; mounts components/pbschedule.js), Private defaults, Unsupported in this build (the §20 list from /api/unsupported)
pages/glossary.js           renders glossary-data.js: every convention sentence (§1.3) and every term used on screen; see-also as arrays (§17.8)
pages/news.js               Daily News (first in the sidebar): "Requires your attention" strip + headlines by source (§17.9)
pages/private_markets.js    Private Markets account page: companies grid (last round, source, as-of, mark), Record a reported round / Add a private company dialogs; mounts components/privateticket.js
pages/history.js            read-only pages of an archived lifetime (#/h/{lifetime}/portfolio | accounting | activity) with the "read-only, frozen at <date>" banner
components/bookswitcher.js  compact three-entry switcher at the top of the sidebar (name, NAV, eligibility dot); Alt+B cycles; the selected book is remembered in localStorage
components/performance.js   Portfolio -> Performance tab (statistics table, monthly table, data window, the sentence PERF)
components/lifetimes.js     Reset lifetime dialog (typed "RESET", starting capital) and the past-lifetimes table with History links
components/pbschedule.js    per-PB editable fee schedule form (fields of §12.4) with before/after confirmation
components/swapticket.js    OTC booking form (IRS / TRS / XCCY terms) -> /api/otc/preview -> confirm; contracts table with VM/reset history (PB page OTC tab; eligible lifetimes only)
components/privateticket.js negotiated-price ticket (qty, price with discount/premium, fee %, settlement lag, lock-up) with the PRIVATE_TRADE sentence and the LOCKUP warning
components/privatecredit.js private-credit commit form + loans table (Credit Instruments page, "Private credit" section)
glossary-data.js            the glossary entries (data only; copied from finsim/static/glossary.js and amended, §17.8)
```

### 17.2 Look

`terminal.css` **reuses the visual style of the old app (B2, directive −3 "keep the visual style")**: its `:root` block
is copied verbatim from `finsim/finsim/static/style.css` — `--bg #0b0e14; --bg2 #11151d; --bg3 #171c26; --line
#232a37; --fg #d7dce5; --fg2 #8b93a3; --fg3 #5d6474; --accent #f0b429; --green #3ecf8e; --red #ff5c5c; --blue
#58a6ff; --purple #b58cff; --mono "IBM Plex Mono","SF Mono",Menlo,Consolas,monospace; --sans "Inter",system-ui,
-apple-system,"Segoe UI",Roboto,sans-serif; --radius 6px; --shadow …` together with its `:root[data-theme="light"]`
block, plus the aliases this design uses: `--panel: var(--bg2); --panel-2: var(--bg3); --text: var(--fg); --muted:
var(--fg2); --up: var(--green); --down: var(--red); --warn: var(--accent); --stale: var(--purple)`. The old file is not
served or imported (the old static folder is not part of the terminal); `test_terminal_static.py` asserts that every
token of the old `:root` is present with the same value. Base 12.5 px, numbers mono and right-aligned, 28 px rows,
sticky headers, 1 px lines, visible focus rings, reduced motion respected. Layout grid `220px sidebar | 1fr main |
380px dock` (the dock holds the ticket on account pages; `t` collapses it); works at 1100 px and 1440 px. Header:
**FINSIM PAPER TERMINAL** with the permanent `PAPER` tag, NY clock, session chips (`US EQ · OPEN · closes 16:00`,
`CME · BREAK 17:00–18:00`), the active account card.

**Sidebar (B16 order, with the book switcher on top):** `[Personal · Fund · Institutional]` switcher → Daily News →
Portfolio → Treasury → US Exchange → Debt Instruments → Credit Instruments → Foreign Markets & FX → Private Markets
(when the lifetime has it; otherwise a dimmed row "unlocks at USD 5,000,000 NAV") → each opened prime broker → "+ Open
prime broker account" (dimmed with the ELIGIBILITY reason when ineligible) → Risk → Accounting → Activity → Watchlists →
Settings → Glossary. Every page follows the selected book (its open lifetime); Portfolio and Accounting offer an "All
books" toggle (`ALL_BOOKS`). "Add funds" and "Reset lifetime" are per book. The attention strip sits under the header: red (ERROR), amber (WARN), blue (INFO); integrity failures,
INTEGRITY_HOLD, FUNDING_SHORTFALL and provider pauses cannot be resolved away while they persist. Offline mode paints
a red full-width banner "FAKE DATA — not market prices".

### 17.3 Freshness on every price (`components/freshness.js`)

`T.px(rec, {size})` renders `<span class="px q-delayed">339.50</span><span class="fr">≤15m · 15:46:50</span>`; classes
`q-live` (reserved), `q-delayed` (green hollow dot + delay), `q-close` (grey, "CLOSE 16:00"), `q-computed` (purple,
"MODEL", italic), `q-private` (blue, "LAST ROUND 2026-09-02", never dimmed), `q-stale` (amber, "STALE 23m",
struck-through), `q-unavailable` (red dash, "N/A"). This is the freshness-display resolution stated at the top of
`BUILD_PROMPT_2026-10-20.md`: no source/delay text in table cells, a small indicator on every price, everything on
hover and in the drawer, a Data Status panel. Tooltip: source,
provider time, fetched time, delay label, the rule that produced the state, price basis, and for computed marks the
inputs with their times. The trading tables carry no source/delay text in the cells (the owner's amendment −2), but
every price carries the small freshness indicator and the hover; positions, markets, watchlists, chain, ticket,
preview, portfolio and risk all use it — **nothing shows a bare number**. The Data Status panel (Settings) and the
attention strip list each provider's status and delay. Nothing is hidden; nothing clutters the ticket.

### 17.4 State and polling (`core.js`)

`T.state = {clock, health, accounts, active_account, account_detail, positions, orders, quotes: Map, watchlists,
selection: {instrument}, ticket, preview, settings, conventions}`. Pollers (name → interval): `health` 10 s (a changed
`state_version` triggers `accounts` + `positions` + `orders` refresh and the strip); `clock` 30 s; `accounts` 10 s;
`account_detail` 10 s; `positions` 10 s; `orders` 5 s while any order is working else 30 s; `quotes` 5 s while a
visible session is OPEN else 15 s (ids = visible rows + ticket instrument + hedge legs; ≤ 200); 2 s while a MARKET
order of the visible account is working; `chain` 60 s on the instrument page (30 s while the ticket targets an
option); `preview` re-runs every 5 s while a preview is open. After every successful write the page repaints from the
response immediately. Polling pauses when the tab is hidden and resumes with an immediate fetch.

Request ids: `crypto.randomUUID()` per click, kept in a pending-submit map; a 503 retries the same id up to 3 times;
**a network error after submit re-GETs `/api/accounts/{id}/orders?status=all` before enabling the button again, so a
lost response never produces a duplicate order**; `X-Replayed: 1` is shown as "already submitted".

### 17.5 Pages (hash routes)

- `#/a/{account}/markets[/{tab}]` — product tabs; dense grid (symbol, name, last, chg %, bid/ask when known, volume,
  freshness, position); typeahead "add symbol" (resolve, 202 spinner); row Enter → workspace; `b`/`s` → ticket.
- `#/a/{account}/i/{instrument}` — **Instrument workspace**: quote header (last, bid/ask, day range, prev close,
  session, freshness, support / limitation text), 1-minute chart, position & lots for this account, working orders on
  it, option chain (if optionable; expiry selector; click bid/ask → ticket leg), no invented content; the ticket is
  docked right with the hedge panel already populated for the default side/qty.
- `#/a/{account}/positions` — positions grid; actions Close (ticket prefilled opposite side, full qty), Hedge (ticket
  with hedge panel open), Exercise (options), Return borrow; detail drawer: fills, settlement, cash flows, mark
  history, linked hedge, linked borrow.
- `#/a/{account}/orders` — working (cancel `x`), history with reasons, rejections, fills with the quote used.
- `#/a/{account}/cash` — §17.1 cash page.
- `#/a/{account}/transactions` — every ledger entry of the account (filters, CSV).
- `#/news` (Daily News, §17.9), `#/treasury`, `#/portfolio` (tabs Positions | Performance), `#/accounting`, `#/risk`,
  `#/activity`, `#/watchlists`, `#/settings`, `#/glossary`, `#/h/{lifetime}/portfolio | accounting | activity` (History).
- **Portfolio columns (B16):** account, instrument, class, signed quantity (footer: gross long, gross short), average
  cost, mark (with freshness), market value, today's P&L, unrealised, realised to date, income to date, financing to
  date, % of NAV, days held; grouped by class (securities, futures, listed options, OTC derivatives, FX, financing,
  private / illiquid, private credit) with subtotals; filters (account, class, text); Copy and CSV. Every figure from
  `accounting.positions` / `nav` / `pnl` (B12) — the page computes nothing.
- "+ Open prime broker account" → one small modal (pick broker, see schedule, the three agreements it records, Open).
  "Add funds" → one small modal (amount, memo, Add). "Reset lifetime" → one small dialog (starting capital, typed
  RESET). No multi-step forms. The PB page's OTC tab mounts `components/swapticket.js` (eligible lifetimes) or shows
  the ELIGIBILITY sentence. The Credit page has sections Cash loans · Securities lending (borrows) · Private credit
  (mounts `components/privatecredit.js`) · Repo (NOT IN THIS BUILD). Unsupported products (§20) render as grey rows
  with "NOT IN THIS BUILD" wherever a list would show them and in Settings → Unsupported.

### 17.6 The ticket (`components/ticket.js`) — one component, every account page

Fields: account (fixed to the page's account, shown), instrument (typeahead, resolve on Enter), side (`BUY` / `SELL` /
`SELL SHORT` / `BUY TO COVER`; options `BTO`/`STO`/`BTC`/`STC` by context), qty (+ lot stepper), type (`MKT` / `LMT` /
`STP` / `STP LMT` / `TRAIL` with trail amount or %), limit, stop, TIF (`DAY`/`GTC`), hedge panel (auto; tabs
"Hedges" = §10.9 candidates and "Strategies" = the 60 templates of §13.16 with units / tenor / strike overrides and the
payoff grid; AON packages show the AON sentence and a single Submit), Preview (auto, debounced 300 ms; manual
`Enter`), Submit (`Ctrl+Enter`; enabled only when the preview is fresh (< 120 s), executable-or-resting, and has no
blockers). The preview panel shows every leg (price + freshness + basis), fees, net cash, settle date, fill estimate
(participation cap), the funding plan (settled / pending / available / **sweep-in from Treasury** / PB financing / FX
leg), borrow terms for shorts (availability, tier, fee, rebate, collateral 102 % amount), margin for written options
and futures, risk warnings (amber), compliance notices (blue), blockers (red, with codes), the convention sentences
that apply. On 409 `PREVIEW_STALE` the panel shows the diff (`339.50 → 341.10 (+0.47 %)`) with **Accept & submit** /
**Cancel**. On 409 `CONFIRM_REQUIRED` it shows "This order sweeps 2,846,625.00 from Treasury — Confirm" and resubmits
with `confirm_large: true`. After a submit the panel shows the result (fills with prices, resting qty, sweep executed,
"resting until Mon 09:30") and the positions/orders grids refresh.

Keyboard map (global unless noted): `/` symbol search; `g m` / `g p` / `g o` / `g c` / `g x` markets / positions /
orders / cash / transactions of the active account; `g t` Treasury; `g f` portfolio; `g a` accounting; `g r` risk;
`g l` activity; `Alt+1…9` switch account (sidebar order); `t` toggle ticket dock; in the ticket: `b s x c` sides, `m l
k j` types, `+ −` qty by lot (`Shift` ×10), `h` hedge panel, `1…5` pick candidate / `0` none, `Enter` preview,
`Ctrl+Enter` submit, `Esc` clear; grids: `↑↓` move, `Enter` open, `b`/`s` prefill; `x` cancel order; `?` help overlay.

### 17.7 What the user always sees (header card, handoff §10)

Active account name; NAV (equity); buying power (USD, with the "incl. Treasury" sub-line and the formula on hover);
settled / pending cash; current positions count; working orders count; pending settlements count; borrows & collateral
held at lender; loans; realised / unrealised P&L today; the session chip; the integrity dot (green / red with the
check name); the `PAPER` tag. Every price on the page carries its freshness. Unsupported products show a grey **NOT IN
THIS BUILD** row state and cannot be ticketed; `LIMITED` products show the limitation text in the workspace header.

---

### 17.8 Glossary (`static/glossary-data.js` + `pages/glossary.js`; B16, B17, directive §3.8 / §7)

- `glossary-data.js` defines `window.TERMINAL_GLOSSARY = {categories: [[key, label], …], terms: [[term, category,
  definition, see_also: [term, …]], …]}` — **see-also is an array**, which fixes the old comma bug (app.js:970 split
  `see.split(',')`, so "Structured credit (CLO, CMBS, RMBS, ABS)" broke into four dead links). The data starts as a
  copy of `finsim/finsim/static/glossary.js` (222 entries) with: the `career` category and every game term removed
  (Career save, Instruction window, #Tag, Starting capital, missions, investors …); every definition that describes
  the simulator's mechanics rewritten for the terminal's (fills at the observed delayed print, no next-session
  bars); unsupported products kept with the suffix "— not in this build" (so links from the UI resolve); and new
  entries for every term the terminal introduces: every §1.3 convention key (verbatim sentence as the definition),
  Treasury (directive §7 definition), Available, Excess liquidity, Encumbrance, Sweep-in / Sweep-out, Book, Lifetime,
  Reset lifetime, History, Eligible Contract Participant, ISDA Master Agreement, Credit Support Annex, Prime Brokerage
  Agreement, GMRA, MSLA/GMSLA, Qualified purchaser, Accredited investor, Rule 144, Form D, Private secondary, Lock-up,
  Last-round mark, Variation margin (swaps), Collateralised-to-market vs settled-to-market, TRS reset, Cross-currency
  notional exchange, MTM cross-currency swap, Trailing stop, All-or-none package, Participation cap, Freshness states
  (delayed / close / computed / private / stale / unavailable), OID accretion, Amendment fee, Pattern day trader,
  13F, 13h-1, Form SHO / 13f-2, Rule 201, Reg T, Daily News, Requires your attention, Performance statistics (each
  statistic's formula from §6.9). `test_terminal_glossary.py` asserts: every see-also target exists as a term; every
  §1.3 key has an entry whose definition equals the sentence; every term named in this list is present; no entry
  contains "simulator", "next session" or "career".
- `pages/glossary.js` renders by category with anchors `#/glossary/{slug}` and the search box; every page links terms
  with `T.gl(term)`; a link to a removed old page (B17) resolves to the term's new home (Portfolio, account page or
  Accounting) — the data carries `home` for those entries.

### 17.9 Daily News (`news.py`, `pages/news.js`; B16, directive §3.1; iron rule 2)

- **Sources (fetched by the quote worker's refresher slot, never on a request thread, cached in `news_items`):**
  (1) Yahoo Finance news search `NEWS_YAHOO` for the market queries `SPY ^GSPC ^TNX ^VIX` and for every ticker held or
  working in any OPEN lifetime (≤ 40 queries, 15 min, `BROWSER_UA` — Yahoo only, per the UA policy); items from
  `news[]` with `title, publisher, link, providerPublishTime, relatedTickers`; (2) the Federal Reserve press feed
  `NEWS_FED_RSS` hourly with `GOV_UA = LoganTerminal/1.0 (loganshaffer87@gmail.com)` (the old `realnews.py` sent a
  browser UA to the Fed — not allowed here); `item/title, link, pubDate` parsed with `xml.etree`; (3) the repository's
  own newspaper editions from `<repo>/site/reports/index.json` (read from disk every 5 min when its mtime changes; each
  entry `{date, slot am|pm|learn, title, path, summary, headlines[], reading_minutes}`) → one item per edition
  (title + summary, link `NEWS_PAPER_URL + path`) and one per `headlines[]` entry for today's and yesterday's `am`/`pm`
  editions (the `learn` slot is skipped: it is a lesson, not news). Everything fetched is **untrusted data**: titles are
  HTML-unescaped, tag-stripped, truncated to 300 chars and shown as text; any item whose text matches the
  instruction-like patterns (`ignore (all|previous) instructions`, `system prompt`, `you are now`) is still shown
  verbatim but counted in `/api/health.news.injection_flags` and the health footer (CLAUDE.md iron rule 2); links open
  in a new tab; nothing is interpreted, scored or summarised. Failures leave the last items with "last fetched hh:mm";
  the provider state appears in the Data Status panel like every other provider.
- **Page (`#/news`, first in the sidebar):** top strip **"Requires your attention"** = `news.attention_summary(scope)`:
  integrity (ok / the failing check), INTEGRITY_HOLD, open attention items by severity, risk warnings count, compliance
  notices count, collateral calls today (COLLATERAL_MARKED events with a positive delta and swap VM payments),
  recall notices, working-orders count, pending settlements, provider pauses — every number from `accounting` /
  `attention` / `risk` / `compliance`, nothing computed on the page (directive §3.1); then three columns: Markets &
  your names (Yahoo), Federal Reserve, Logan's Daily Newspaper (today's editions with "read the edition" links), each
  item with publisher and time, newest first; the sentence NEWS in the footer. No Major P&L table lives here (the
  Portfolio tiles are one click away; the strip links to them).

## 18. Launcher / CLI (`terminal/app.py`) and `finsim/finsim/__main__.py`

`python -m finsim <cmd>` (from `C:\Users\logan\intelligence-terminal\finsim`) dispatches to `finsim.terminal.app.main`
(`__main__.py` is re-pointed; the old `demo`/`phone` subcommands are not exposed; `legacy` keeps the old app reachable).

| command | behaviour | exit |
|---|---|---|
| `serve [--port 8765] [--home DIR] [--db FILE] [--offline] [--log-level INFO]` | foreground server (§16); prints `ready http://127.0.0.1:8765/ (finsim-terminal 1.0.0, db …)` | 0 ok; 3 another instance / port; 4 newer schema; 5 startup error (traceback in the log) |
| `stop [--timeout 15] [--force]` | `POST /api/shutdown`; waits for `/api/health` to stop answering and the lock to release; then reads the lock file's PID and checks liveness with `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) + GetExitCodeProcess == 259` and the image name (`QueryFullProcessImageNameW` contains `python`); `--force` → `taskkill /PID` only when both hold (never blindly; never `os.kill`) | 0 stopped; 1 nothing running; 2 still alive |
| `status` | served version from `/api/health` vs the code version on disk (**warns "restart to serve the new code"** when they differ), `app` marker (warns "a non-terminal server answers on 8765" when `finsim-terminal` is absent), pid, uptime, db path/size, log path and tail (last 15 lines when not running or unhealthy), lock state, workers/providers, integrity, last lifecycle tick, Startup entry present, backups count | 0 running; 1 not running |
| `install [--no-open]` | (1) records `sha256(finsim.db)` into `<home>/finsim.db.sha256` (baseline for the byte-identical check); (2) stops any server answering on 8765 whose health lacks `"app":"finsim-terminal"` (the old 0.11.0 engine) via its own loopback `/api/shutdown` and waits for exit; (3) deletes the old scheduled task `FinSim Server` (`schtasks /Delete /F /TN "FinSim Server"`), the old `<home>/finsim-server.vbs`, `finsim-open.vbs`, `make-shortcuts.vbs`; (4) writes `<home>/FinSim Server.vbs`, `<home>/FinSim Terminal.vbs`, `<home>/finsim.ico`; copies the server VBS into the Startup folder as **`FinSim Server.vbs`** (replacing the old entry of that name); creates Desktop (via `_known_desktop`) + Start Menu `FinSim.lnk` → `wscript "FinSim Terminal.vbs"`; (5) starts the terminal through the VBS, waits for health with the marker, prints the final state and the PowerShell sequence; (6) prints `sha256(finsim.db)` again and "unchanged" | 0 |
| `open` | if health answers with the marker → open the window; else start through `FinSim Server.vbs`, wait ≤ 60 s (a child that exits is reported with the log tail), then `finsim.app.open_window` (Chrome/Edge/Brave `--app` with profile `<home>/terminal-window-profile`; fallback default browser). Never spawns a second server (the lock and the exclusive bind make a duplicate exit 3, which `open` reports) | 0 / 1 |
| `uninstall [--purge]` | removes the Startup entry, shortcuts, VBS files, window profile; stops the server; `--purge` deletes `terminal.db` only after the owner types its path (never `finsim.db`) | 0 |
| `legacy [--port 8766] [--refresh]` | copies `<home>/finsim.db` to `<home>/legacy-view.db` with the sqlite3 backup API (once; `--refresh` re-copies) and runs the OLD `finsim.api.server.serve(db=legacy-view.db, port=8766)` on the **copy**, so `finsim.db` is never opened; prints the URL; documented as the read-only archive view | 0 |

Python resolution at install: `py -3.12 -c "import sys;print(sys.executable)"` → `pythonw.exe` next to it (fallback
`%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe`); the absolute path is written into the VBS.
`launcher_files(home, pythonw, repo_dir, port) -> dict[path, str | bytes]` is pure and tested (ASCII, marker check,
port, `-m finsim serve`).

`FinSim Server.vbs` (ASCII only; Startup folder; modelled on `finsim2/install/ShafferDeskTerminal-Server.vbs`):
```vbscript
' FinSim Paper Terminal - starts the local server at login. If the terminal already answers it does nothing; if the
' OLD simulation engine answers (no "finsim-terminal" marker) it asks it to shut down first, then starts the terminal.
Option Explicit
Dim sh, fso, http, state, py, repo, home, txt
Set sh = CreateObject("WScript.Shell"): Set fso = CreateObject("Scripting.FileSystemObject")
py = "{PYTHONW}": repo = "{REPO_FINSIM_DIR}": home = "{FINSIM_HOME}"
If Not fso.FileExists(py) Then WScript.Quit 1
If Not fso.FileExists(repo & "\finsim\terminal\__init__.py") Then WScript.Quit 1
state = "down"
On Error Resume Next
Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0"): http.setTimeouts 1000, 1000, 2000, 2000
http.Open "GET", "http://127.0.0.1:{PORT}/api/health", False: http.Send
If Err.Number = 0 Then
  If http.Status = 200 Then
    txt = http.responseText
    If InStr(txt, """finsim-terminal""") > 0 Then state = "terminal" Else state = "other"
  End If
End If
Err.Clear
If state = "other" Then
  http.Open "POST", "http://127.0.0.1:{PORT}/api/shutdown", False: http.setRequestHeader "Content-Type", "application/json": http.Send "{}"
  WScript.Sleep 5000
End If
On Error GoTo 0
If state = "terminal" Then WScript.Quit 0
sh.Environment("Process")("PYTHONPATH") = repo
sh.Environment("Process")("FINSIM_HOME") = home
sh.CurrentDirectory = repo
sh.Run """" & py & """ -m finsim serve --port {PORT}", 0, False
```
`FinSim Terminal.vbs` runs `pythonw -m finsim open` the same way. The single-instance lock and the exclusive bind
make the VBS probe a courtesy, not the guard: two VBS launches at login still end with exactly one server.

Final PowerShell sequence (printed by `install`, repeated in `docs/TERMINAL_RUNBOOK.md`):
```powershell
cd C:\Users\logan\intelligence-terminal\finsim
py -3.12 -m finsim stop                 # stops the OLD 0.11.0 engine too (same /api/shutdown route); prints the finsim.db sha256
py -3.12 -m finsim install --no-open    # replaces Startup\FinSim Server.vbs, removes the "FinSim Server" task, starts 1.0.0
py -3.12 -m finsim status               # must show finsim-terminal 1.0.0, integrity ok, the Startup entry, finsim.db unchanged
py -3.12 -m finsim open
```

---

## 19. Hand-worked test scenarios (`finsim/tests/test_terminal_*.py`, unittest, no network, temp SQLite, FakeClock, FakeProvider / FixedMarks)

Fixture (`tests/terminal_helpers.py`): `make_runtime(tmpdir, at="2026-10-20T14:00:00-04:00", offline=True)` → a
`Runtime` with `FakeClock`, `FakeProvider` (quotes set by the test: `fp.set("AAPL", last="339.50",
quote_time=now-120, bar=(t, 61900))`, chains, rates, daily bars), the real `TradingCalendar` with the repo's holiday
file (2026-10-16 Fri, 10-19 Mon, 10-20 Tue, 10-21 Wed, 10-22 Thu, 10-23 Fri, 10-26 Mon are business days; 2026-11-20
is the November expiry Friday; 2026-11-26 Thanksgiving; 2026-11-27 early close 13:00), the real `CommandBus`, no HTTP.
Helpers: `bal(rt, account, gl, ccy="USD", inst=None)` natural-sign cents; `fund(rt, account, amount_c)` (Treasury
transfer); `submit(rt, **req)`; `tick(rt, at)`; `assert_integrity(tc, rt, mode="full")`. Every expected number is worked
by hand in the test comment; tests never copy the app's output. Amounts below in USD unless stated; `to_c` = cents
HALF_EVEN.

### 19.1 Initial capital and deposits (`test_terminal_treasury.py`)

- Start: `init:capital:1` → TREASURY 1010 USD 10,000,000.00; 3000 10,000,000.00; NAV(ALL) 10,000,000.00; P&L ITD 0.00.
- Reopen the same file → the init is skipped (row exists); 1010 still 10,000,000.00.
- `treasury.deposit` 2,500,000.00 (`ui:d1`) → 1010 12,500,000.00; 3000 12,500,000.00; L6 `capital_explained` ok;
  `pnl(ALL, ITD)` = 12,500,000 − 12,500,000 flows = 0.00.
- Replay `ui:d1` → `replayed=True`, balances unchanged. `ui:d1` with amount 1.00 → 409 `REQUEST_ID_REUSED`.
- Deposit without `amount` → 400 `AMOUNT`; amount 0 → 400. Withdraw 20,000,000.00 → 409 `INSUFFICIENT_FUNDS`, nothing posted.
- `treasury()`: capital 12,500,000; retained 0; advances 0; cash 12,500,000 (M3 identity).

### 19.2 Transfer, sweep-in, confirm threshold, sweep-out, funding refusal (`test_terminal_sweeps.py`)

- Transfer 1,000,000.00 TREASURY → US_EXCHANGE (`ui:t1`): T `Dr 3100 / Cr 1010 1,000,000`; A `Dr 1010 / Cr 3100
  1,000,000`. T settled 9,000,000.00; A settled 1,000,000.00; NAV(ALL) 10,000,000.00; L5 Σ3100 = 0.
- Tue 2026-10-20 14:00 ET: BUY 5,000 SPY MARKET, last 769.32 (delayed, quote_time now−120), bar volume 120,000 →
  cap 12,000 ≥ 5,000 → full fill at 769.32 (basis `last`). gross `5,000 × 769.32 = 3,846,600.00`; fee `5,000 ×
  0.005 = 25.00`; need 3,846,625.00; available 1,000,000.00 → shortfall **2,846,625.00** > 2,000,000.00 → the first
  submit returns 409 `CONFIRM_REQUIRED` {sweep_in: 2846625.00}; nothing posted, no commands row. Resubmit the same
  request_id with `confirm_large: true` → SWEEP_IN transfer (`xfer:…`, linked to the fill, `shortfall_c`
  284662500): T `Dr 3100 / Cr 1010 2,846,625.00`, A `Dr 1010 / Cr 3100 2,846,625.00`; fill entry `Dr 1100 SPY
  3,846,600.00 / Dr 5000 25.00 / Cr 2100 3,846,625.00`; settlement −3,846,625.00 due 2026-10-21.
- Expected: T settled **6,153,375.00** (10,000,000 − 1,000,000 − 2,846,625); T 3100 debit 3,846,625.00; A settled
  3,846,625.00, payable 3,846,625.00, available 0.00; position SPY 5,000 cost 3,846,600.00 avg 769.32;
  NAV(A) at 769.32 = 3,846,625 − 3,846,625 + 3,846,600 = **3,846,600.00**; NAV(ALL) = 6,153,375 + 3,846,600 =
  **9,999,975.00** = 10,000,000 − 25.00 fee. L19 sweep linkage holds.
- Sweep-out at 17:30: excess = 3,846,625 − 3,846,625 − 0 − 10,000 = −10,000 < 0 → job SKIPPED. Wed 00:30 SETTLE_DUE:
  `Dr 2100 / Cr 1010 3,846,625.00` → A settled 0.00; buying power = 0 + Treasury 6,153,375.00. A second account with
  settled 50,000.00 and no needs → SWEEP_OUT 40,000.00 (buffer 10,000.00).
- Funding refusal: Treasury settled 100.00 (after a withdrawal in the test), A 0.00, BUY 1,000 AAPL at 339.50 →
  preview blocker `INSUFFICIENT_FUNDS` {need 339,505.00, own 0.00, treasury 100.00}; submit 409; no rows.

### 19.3 Equity buy, fee, T+1 settlement, P&L today (`test_terminal_equities.py`)

- Fri 2026-10-16: fund A 500,000.00; snapshots run Fri/Sat/Sun 17:45 (NAV 500,000.00 each).
- Mon 2026-10-19 10:00 ET: BUY 1,000 AAPL MARKET, last 339.50, bar volume 120,000 (cap 12,000). gross 339,500.00;
  fee `1,000 × 0.005 = 5.00`. Entry `Dr 1100 AAPL 339,500.00 / Dr 5000 5.00 / Cr 2100 339,505.00`; settlement due
  Tue 10-20; LONG lot 1,000 @ 339.50 cost 339,500.00.
- Reads Mon 10:01: settled 500,000.00; payable 339,505.00; available (settle 10-20) **160,495.00**; MV at 339.50
  339,500.00; unrealised 0.00; NAV = 500,000 − 339,505 + 339,500 = **499,995.00**; P&L TODAY vs Sun snapshot = −5.00
  (explain: fees −5.00; residual 0.00).
- Tue 00:35 tick: SETTLE_DUE `Dr 2100 / Cr 1010 339,505.00` → settled **160,495.00**, payable 0, NAV unchanged
  499,995.00. Tick again → no second settlement (jobs_done + idem). Running the job with the clock at Mon 16:00 →
  nothing settles (due date not reached).
- Tue mark 342.00 → MV 342,000.00; unrealised **+2,500.00**; NAV 502,495.00; P&L TODAY vs Mon 17:45 snapshot
  (499,995.00 at 339.50) = **+2,500.00**; explain Δunrealised 2,500.00; residual 0.00.

### 19.4 FIFO sell with realised P&L (`test_terminal_equities.py`)

- Lots: A 1,000 AAPL @ 339.50 (Mon, fee 5.00), B 500 @ 335.00 (Tue, fee 2.50, cost 167,500.00). Wed SELL 1,200 MARKET
  last 345.00: gross `1,200 × 345 = 414,000.00`; fee `1,200 × 0.005 = 6.00`; FIFO relief `1,000 × 339.50 = 339,500.00`
  + `200 × 335.00 = 67,000.00` = **406,500.00**; realised = 414,000 − 406,500 = **+7,500.00**.
- Entry `Dr 1200 413,994.00 / Dr 5000 6.00 / Cr 1100 AAPL 406,500.00 / Cr 4000 AAPL 7,500.00` (414,000.00 both sides).
  `lot_relief`: (A, 1,000, 339,500.00, 345,000.00, +5,500.00), (B, 200, 67,000.00, 69,000.00, +2,000.00). Remaining lot
  B 300 @ 335.00 cost **100,500.00**; `v_positions` qty 300; L9: 1100 AAPL = 339,500 + 167,500 − 406,500 = 100,500 ✓.
  Realised ITD 7,500.00; fees ITD 13.50. Settlement Thu: `Dr 1010 / Cr 1200 413,994.00`.
- SELL 400 with a 300 position → 400 `OVERSELL`; SELL 300 + SELL_SHORT 100 with a borrow is the accepted form.

### 19.5 Short sale: borrow, 102 % collateral, weekend accruals, collateral mark, cover (`test_terminal_shorts.py`)

Setup: A settled 200,000.00, nothing pending. ABC last 50.00 (bar volume 100,000 → cap 10,000); FINRA ratio 0.30 for
the last session, ADV 5,000,000 → tier GC 0.30 %; SOFR 3.87 (obs 2026-10-15) → rebate `3.87 − 0.50 = 3.37 %`.
- Fri 2026-10-16 10:00 ET: SELL_SHORT 1,000 ABC MARKET. Borrow first: collateral `to_c(1.02 × 1,000 × 50.00) =
  51,000.00` → `Dr 1400 ABC 51,000.00 / Cr 1010 51,000.00` (settled 149,000.00; no sweep). Fill at 50.00: gross
  50,000.00; fee 5.00; `Dr 1200 49,995.00 / Dr 5000 5.00 / Cr 2600 ABC 50,000.00`; SHORT lot 1,000 @ 50.00 proceeds
  50,000.00; settlement +49,995.00 due Mon 10-19.
- Reads Fri: settled 149,000.00; receivable 49,995.00; held at lender 51,000.00; available (settle Mon) 198,995.00;
  position −1,000; MV −50,000.00; unrealised 0.00; NAV = 149,000 + 49,995 + 51,000 − 50,000 = **199,995.00** =
  200,000 − 5 ✓. L12 (borrow 1,000 ≥ 1,000) and L13 hold.
- Mon 10-19 00:35 tick: settlement `Dr 1010 / Cr 1200 49,995.00` (settled 198,995.00). Accruals for the nights of Fri
  16, Sat 17, Sun 18 (mark for all three = Friday's CLOSE 50.00):
  - fee, base `1,000 × 50.00 = 50,000.00`, 0.30 %, ACT/360, cumulative: night 1 `to_c(50,000 × 0.003 × 1/360) =
    to_c(0.416667) = 0.42` → posts **0.42**; night 2 `to_c(0.833333) = 0.83` → posts **0.41**; night 3 `to_c(1.25) =
    1.25` → posts **0.42**. 2310 = 5300 = 1.25.
  - rebate, base 51,000.00, 3.37 %: night 1 `to_c(4.774167) = 4.77` → **4.77**; night 2 `to_c(9.548333) = 9.55` →
    **4.78**; night 3 `to_c(14.3225) = 14.32` → **4.77**. 1410 = 4350 = 14.32.
  Keys `accr:bfee:{b}:2026-10-16/17/18`, `accr:rebate:…`. A second tick posts nothing.
- Mon 17:10 COLLATERAL_MARK with CLOSE 52.00: required `to_c(1.02 × 1,000 × 52) = 53,040.00`; delta **+2,040.00** →
  `Dr 1400 / Cr 1010 2,040.00` (settled 196,955.00); `borrows.collateral_c` 53,040.00. Variant CLOSE 48.00: required
  48,960.00 → **−2,040.00** returns (`Dr 1010 / Cr 1400`).
- Position at 52.00: MV −52,000.00; unrealised `50,000 − 52,000 = −2,000.00`. NAV Mon close = 196,955.00 + 53,040.00
  + 14.32 − 52,000.00 − 1.25 = **198,008.07**; check 200,000 − 5 − 2,000 + 14.32 − 1.25 = 198,008.07 ✓. P&L ITD
  (funding 200,000) = −1,991.93 = income 14.32 − financing 1.25 − fees 5.00 + unrealised −2,000.00 ✓ residual 0.
- Mon night accruals (posted Tue 00:35; new segments because the bases changed): fee `to_c(52,000 × 0.003/360) =
  to_c(0.433333) = 0.43`; rebate `to_c(53,040 × 0.0337/360) = to_c(4.965133) = 4.97`. 2310 = 1.68; 1410 = 19.29.
- Tue 10:00 BUY_TO_COVER 1,000 @ last 51.00: gross 51,000.00; fee 5.00; relief of the SHORT lot: proceeds 50,000 −
  cost 51,000 → realised **−1,000.00**; entry `Dr 2600 ABC 50,000.00 / Dr 4000 ABC 1,000.00 / Dr 5000 5.00 / Cr 2100
  51,005.00`. Borrow return in the same command: `Dr 1010 53,040.00 / Cr 1400 53,040.00`; fees `Dr 2310 1.68 / Cr 1010
  1.68`; rebate `Dr 1010 19.29 / Cr 1410 19.29`; borrow CLOSED; L12 holds with no short and no borrow.
  Settled = 196,955 + 53,040 − 1.68 + 19.29 = **250,012.61**; payable 51,005.00 (Wed). NAV flat = 250,012.61 −
  51,005.00 = **199,007.61** = 200,000 − 5 − 5 − 1,000 + 19.29 − 1.68 ✓.
- No borrow: a symbol not in the master → 409 `NO_BORROW`, nothing posted. Partial: tier T3 (no FINRA rows for 3
  sessions) with ADV 8,000 → `qty_available = 800` → the order is scaled to 800 with `reason` and the preview said so.
  A `credit.return_stock` while the short is open → 409 `BACKS_SHORT`.

### 19.6 Currency loan and FX spot T+2 (`test_terminal_fx.py`)

CREDIT account; ECBDFR 2.00 (obs 2026-10-19) → loan rate 2.75 %; EURUSD mid 1.1238 (delayed, fresh); Treasury USD
available. Tue 2026-10-20 11:00 ET.
- `credit.loan_draw` EUR 1,000,000.00: `Dr 1010:EUR 1,000,000.00 / Cr 2750:EUR 1,000,000.00` (base 1,123,800.00
  each at 1.1238). EUR settled at once.
- `fx.spot` sell EUR 1,000,000 buy USD: rate `1.1238 − 0.00002 = 1.12378`; USD `to_c(1,000,000 × 1.12378) =
  1,123,780.00`; `spread_cost` 20.00 shown. Entry (two legs): USD `Dr 1250:USD 1,123,780.00 / Cr 3900:USD
  1,123,780.00`; EUR `Dr 3900:EUR 1,000,000.00 / Cr 2350:EUR 1,000,000.00` with `fx_rate` 1.12378 (base 1,123,780.00
  each; 3900 nets to 0 in base). Settlements +1,123,780.00 USD and −1,000,000.00 EUR due **Thu 10-22**.
- Reads Tue (fx 1.1238): EUR settled 1,000,000.00, payable 1,000,000.00, available 0.00; USD receivable
  1,123,780.00; loan EUR 1,000,000.00. NAV = 1,123,800 + 1,123,780 − 1,123,800 − 1,123,800 = **−20.00** (= the
  spread). `fx_result`: A/L EUR lines 1010 +1,000,000 (base +1,123,800), 2750 −1,000,000 (−1,123,800), 2350
  −1,000,000 (−1,123,780): Σ amount −1,000,000 × 1.1238 = −1,123,800.00; Σ base −1,123,780.00 → **−20.00** ✓;
  explain residual 0.00.
- Wed 10-21 00:35 (fx now 1.1300): accrual for the night of Tue `to_c(1,000,000 × 0.0275 × 1/360) = to_c(76.388889) =
  76.39`: `Dr 5150:EUR 76.39 / Cr 2755:EUR 76.39` (base at 1.13 → 86.32). NAV at 1.13 = 1,130,000 + 1,123,780 −
  1,130,000 − 1,130,000 − 86.32 = **−6,306.32**. Explain: financing −86.32; `fx_result` = Σ amount (1,000,000 −
  1,000,000 − 1,000,000 − 76.39 = −1,000,076.39) × 1.13 → −1,130,086.32, minus Σ base (1,123,800 − 1,123,800 −
  1,123,780 − 86.32 = −1,123,866.32) → **−6,220.00**; total −6,306.32 ✓, residual 0.00.
- Thu 10-22 00:35 settlement (fx 1.13): `Dr 2350:EUR 1,000,000.00 / Cr 1010:EUR 1,000,000.00` (base ±1,130,000.00);
  `Dr 1010:USD 1,123,780.00 / Cr 1250:USD 1,123,780.00`; accrual night Wed: cumulative 2 nights `to_c(152.777778) =
  152.78` → posts 76.39. USD settled 1,123,780.00; EUR settled 0.00; 2755 152.78. NAV = 1,123,780 − 1,130,000 −
  `to_c(152.78 × 1.13) = 172.64` = **−6,392.64** (= −6,306.32 − 86.32); `fx_result` still −6,220.00 (the zero-balance
  1010/2350 EUR lines keep residual carrying −6,200 and +6,220).
- `credit.loan_repay` 1,000,152.78 with EUR settled 0 → 409 `INSUFFICIENT_CASH_CCY` "buy EUR 1,000,152.78 with Convert".
- Thu 10:00 `fx.spot` buy EUR 1,000,500 (mid 1.1300 → rate 1.13002): USD `to_c(1,000,500 × 1.13002) = 1,130,585.01`;
  legs `Dr 1250:EUR 1,000,500 / Cr 3900:EUR` at 1.13002 (base 1,130,585.01) and `Dr 3900:USD 1,130,585.01 / Cr
  2350:USD 1,130,585.01`; value **Mon 10-26**. Mon 00:35 settlement (fx 1.13): `Dr 1010:EUR 1,000,500 / Cr 1250:EUR`
  (base 1,130,565.00 at 1.13); USD payable 1,130,585.01 vs USD settled 1,123,780.00 → `fund()` sweeps **6,805.01**
  from Treasury (SWEEP_IN linked to the settlement) then `Dr 2350:USD / Cr 1010:USD 1,130,585.01` → USD settled 0.00.
  Accruals for the nights of Thu 22, Fri 23, Sat 24, Sun 25 (cumulative on 1,000,000 at 2.75 %): cum 229.17 (posts
  76.39), `to_c(305.555556) = 305.56` (posts 76.39), `to_c(381.944444) = 381.94` (posts **76.38**), `to_c(458.333333)
  = 458.33` (posts 76.39) — the cumulative method shows one 76.38 and the sum is exactly 458.33.
- Mon 10:00 repay: `Dr 2755:EUR 458.33 / Dr 2750:EUR 1,000,000.00 / Cr 1010:EUR 1,000,458.33`; loan CLOSED; L14 ok;
  EUR cash left 41.67. CREDIT NAV at 1.13 = `to_c(41.67 × 1.13) = 47.09`; funding received 6,805.01 → P&L ITD =
  **−6,757.92**: financing base = 86.32 × 5 + 86.31 (the 76.38 night) = −517.91; `fx_result` = to_c(41.67 × 1.13)
  − Σ base = 47.09 − 6,287.10 = **−6,240.01** (Σ base by gl: 1010 −6,152.91; 2750 +6,200.00; 2350 +6,220.00; 1250
  +20.01; 2755 0.00); −517.91 − 6,240.01 = −6,757.92 ✓ residual 0.00. Economics: 1,123,780 received − 1,130,585.01
  paid + 47.09 left = −6,757.92 ✓.
- Borrow USD and buy stock the same day: USD loan 500,000 in CREDIT, transfer to US_EXCHANGE, buy → available rises
  by the draw at once; interest accrues from the draw night.

### 19.7 Options: margin, expiry, exercise, assignment (`test_terminal_options.py`)

US_EXCHANGE settled 1,000,000.00. Chain (fake Cboe, delayed, chain quote_time = underlying last_trade_time now−120):
SPY 2026-11-20 C760 bid 15.10 / ask 15.20 (ask_size 50); P740 bid 9.80 / ask 9.90 (bid_size 50); SPY last 769.32.
- BTO 10 C760 @ ask 15.20: premium `10 × 15.20 × 100 = 15,200.00`; fee `10 × 0.65 = 6.50`; `Dr 1700 15,200.00 / Dr 5000
  6.50 / Cr 2100 15,206.50` (T+1). STO 5 P740 @ bid 9.80: premium 4,900.00; fee 3.25; `Dr 1200 4,896.75 / Dr 5000
  3.25 / Cr 2900 4,900.00`.
- OPTION_MARGIN_MARK after the STO (premium = mark 9.85 mid → the test pins bid=ask=9.80 so mark 9.80; S 769.32):
  per contract `max(9.80 + 0.20 × 769.32 − max(0, 769.32 − 740), 9.80 + 0.10 × 740) = max(9.80 + 153.864 − 29.32,
  83.80) = max(134.344, 83.80) = 134.344`; × 100 × 5 = **67,172.00** → `Dr 1310 / Cr 1010 67,172.00`; `margin_state`
  67,172.00; L16 holds. After settlements settled = 1,000,000 − 15,206.50 + 4,896.75 − 67,172 = 922,518.25.
- Fri 2026-11-20 16:25 ET, SPY CLOSE 772.00 (quote_time 16:00:00 that day; `marks` CLOSE row). OPTION_EXPIRY:
  C760 intrinsic 12.00 ≥ 0.01 → EXERCISE fill BUY 1,000 SPY @ 760 (origin EXERCISE, fee 0): `Dr 1100 SPY 775,200.00
  (760,000 + 15,200) / Cr 1700 15,200.00 / Cr 2100 760,000.00` (due Mon 11-23; no sweep needed); LONG lot 1,000 @
  775.20. P740 intrinsic 0 → EXPIRY fill at 0: `Dr 2900 4,900.00 / Cr 4000 4,900.00`. Margin mark: `Dr 1010 / Cr 1310
  67,172.00`. Result: realised **+4,900.00**; position 1,000 SPY cost 775,200.00; MV at 772 = 772,000.00; unrealised
  **−3,200.00**; realised + unrealised = +1,700.00 = (12,000 intrinsic − 15,200 paid) + 4,900 kept ✓; fees 9.75;
  both instruments EXPIRED; `jobs_done` has both keys; a second run posts nothing.
- Variant CLOSE 735.00: P740 ITM 5.00 → ASSIGNMENT BUY 500 SPY @ 740: `Dr 1100 SPY 365,100.00 (370,000 − 4,900) / Dr
  2900 4,900.00 / Cr 2100 370,000.00` (lot 500 @ 730.20); C760 OTM → `Dr 4000 15,200.00 / Cr 1700 15,200.00`; MV
  367,500.00; unrealised +2,400.00; realised −15,200.00.
- Missing close (record `unavailable`) → SKIPPED `EXPIRY_PENDING_CLOSE`, attention, position open; a later tick with
  the mark completes it once. Early exercise Thu 11-19 by `options.exercise` 10 → the same bookings with
  `exercise:{account}:{inst}:{request_id}`.
- Margin formula pin (product S7): S 339.50, K 330 put, premium 4.00: `max(4 + 67.90 − 9.50, 4 + 33.00) = 62.40` ×
  100 = **6,240.00** per contract.

### 19.8 Futures: IM, two days of VM, close-out (`test_terminal_futures.py`)

US_EXCHANGE settled 100,000.00; ES spec multiplier 50, IM 17,500 / contract.
- Mon 2026-10-19 10:00 ET: BUY 2 ESZ26 MARKET last 7,754.25 (bar volume 1,000 → cap 100 ≥ 2). Fee `2 × 2.25 = 4.50`:
  `Dr 5000 / Cr 1010 4.50`; IM `2 × 17,500 = 35,000.00`: `Dr 1300 ESZ26 35,000.00 / Cr 1010`; settled **64,995.50**;
  LONG lot 2 @ 7,754.25 (cost_c 0); `futures_settle` seed.
- Mon 16:05 FUTURES_VM, SETTLE 7,760.00 (observed at 16:00): `VM = 0 + 2 × (7,760.00 − 7,754.25) × 50 = 2 × 5.75 ×
  50 = 575.00` → `Dr 1010 575.00 / Cr 4400 ESZ26 575.00`; settled 65,570.50; `futures_settle(10-19)` 7,760.00. Rerun
  → nothing.
- Tue 16:05 SETTLE 7,741.50: `VM = 2 × (7,741.50 − 7,760.00) × 50 = −1,850.00` → `Dr 4400 1,850.00 / Cr 1010
  1,850.00`; settled **63,720.50**; 4400 cumulative **−1,275.00** = 2 × (7,741.50 − 7,754.25) × 50 ✓. Wed intraday mark
  7,750.00 → unrealised `2 × (7,750 − 7,741.50) × 50 = 850.00` (computed, not posted). NAV(A) Tue close = 63,720.50 +
  35,000.00 = **98,720.50** = 100,000 − 4.50 − 1,275.00 ✓.
- Thu 10:00 SELL 2 @ 7,745.00: fee 4.50; IM released `Dr 1010 / Cr 1300 35,000.00` (L15: flat → 0). Thu 16:05 SETTLE
  7,750.00: `VM = carried 2 × (7,750 − 7,741.50) × 50 = 850.00 + fills (−2) × (7,750 − 7,745) × 50 = −500.00 =
  +350.00`; 4400 total **−925.00** = 2 × (7,745 − 7,754.25) × 50 ✓; settled = 63,720.50 − 4.50 + 35,000 + 350 =
  **99,066.00** = 100,000 − 9.00 − 925.00 ✓; position 0.
- Unfunded VM loss: the account holds only its IM (settled 0 after a sweep-out), Treasury emptied by a withdrawal in
  the test → the VM −1,850.00 is still posted: 1010 = −1,850.00, attention ERROR `FUNDING_SHORTFALL`, L18 accepts;
  a new order in the account → 409 `FUNDING_SHORTFALL`; a transfer of 2,000.00 → 1010 150.00 and the item resolves.

### 19.9 Orders: stale, session, sleep, limits, partial fills, hedge resize (`test_terminal_orders.py`, `test_terminal_sessions.py`)

- **Stale:** Tue 10:30 ET (OPEN); AAPL `quote_time = now − 1,500` (> 900 + 300) → `freshness` stale; preview
  `executable=false` STALE; MARKET submit → 409 `QUOTE_STALE`; LIMIT BUY 1,000 @ 339.00 → WORKING (first look not
  executable → `first_eval_done` stays 0). Fresh record last 338.90, bar volume 50,000 (cap 5,000): marketable on the
  first executable look → fills 1,000 @ **338.90** (at or better). The same record again → the same idem key → no
  second fill. Variant: first look last 339.20 (not marketable, `first_eval_done = 1`); later last 338.95 → fills at
  the limit **339.00** (LIMIT_RULE). A `computed` mark never fills.
- **Session:** Sat 2026-10-24 12:00 ET: MARKET BUY accepted WORKING `AWAITING_SESSION`, `expires_ts` = Mon 10-26
  16:00 ET; tick Mon 09:31 with a fresh quote (`quote_time ≥ opened_at`) → fills. DAY placed Tue 17:30 → expires Wed
  16:00; 2026-11-27 → expires 13:00; 2026-11-26 CLOSED (holiday); CME_EQUITY_INDEX OPEN Sun 19:00 ET, BREAK Mon 17:30,
  Good Friday 2026-04-03 closed for US_EQUITY and CME; `settlement_date(2026-11-25, 1)` = 2026-11-27;
  `settlement_date(Fri 10-23, 1)` = Mon 10-26.
- **Laptop sleep:** a DAY order placed Tue 10:00; the clock jumps to Thu 09:40 with a fresh quote → `evaluate` →
  `now > expires_ts` → NONE `EXPIRED_PENDING`; EXPIRE_ORDERS marks it EXPIRED; no fill. A record whose `quote_time`
  is Wed 15:59 evaluated Thu 09:31 → `QUOTE_FROM_PREVIOUS_SESSION`, no fill.
- **Stops:** BUY STOP 340.00 with last 339.50 accepted; last 340.10 → TRIGGER then fill at 340.10 (the print that
  triggered it); sell stop above last → 400 `STOP_SIDE`.
- **Partial fills:** MARKET BUY 50,000 SPY; bar (t1, v 120,000) → cap 12,000 → PARTIAL 12,000, `last_bar_ts` t1;
  same bar → cap 0 `NO_NEW_BAR`; bar (t2, v 90,000) → 9,000 (21,000 filled); bar (t3, v 0) → `NO_VOLUME_THIS_BAR`;
  cancel → CANCELLED with `filled_qty` 21,000; `avg_px` = volume-weighted. Options: BTO 25 with ask_size 10 → fills
  10; same observation → nothing. Hedge child PUT_7PCT ratio 0.01: after 12,000 → 120 contracts WORKING; after
  21,000 → 210.
- **Confirm / preview:** submit with an unknown `preview_id` → 409 `PREVIEW_STALE` carrying a fresh preview; price
  moved 0.6 % against the user → 409 with `diff`; `confirm_large` flow as in §19.2.

### 19.10 Ledger unit tests (`test_terminal_ledger.py`)

- An entry with Σ amount ≠ 0 in one currency → `LedgerError`, nothing written. Reusing an `idem_key` → `DuplicatePosting`.
- **Rounding plug:** EUR lines +33.33 (1100), +33.33 (5000), +33.34 (1220), −100.00 (2100) at rate 1.1238: `fx_c`
  gives 37.46, 37.46, 37.47 (sum 112.39) while the largest line would round to −112.38; the plug sets the −100.00
  line's base to **−112.39**; Σ base = 0; L1 holds. (Without the plug the entry fails by 0.01.)
- `relieve_fifo`: lots 1,000 @ 10.00 and 500 @ 11.00; relieve 1,200 with total proceeds 13,200.00 → reliefs
  (1,000, cost 10,000.00, proceeds 11,000.00, +1,000.00) and (200, cost 2,200.00, proceeds 2,200.00, 0.00); a proceeds
  total that does not split evenly (3 lots of 1, proceeds 1.00) puts the residual cent on the last relief.
- `gl_balances` after 100 random balanced entries equals `SELECT SUM(amount_c) … GROUP BY` (L3).

### 19.11 Accrual catch-up and snapshots (`test_terminal_lifecycle.py`)

The borrow of §19.5 and the loan of §19.6 are open; the clock stops Fri 2026-10-16 16:00 ET and restarts Tue 10-20
09:00. One `tick()` posts, in date order, the accruals for the nights of 10-16, 10-17, 10-18 and 10-19 for each object
(4 keys each; totals equal `to_c(base × rate × 4/360)` per segment), Monday's settlements, the Friday CLOSE marks from
`daily()` bars (note "recorded late"), and snapshots for Fri, Sat, Sun, Mon for every account and `ALL`; a second
`tick()` posts nothing. M5 reports no gaps. Sweep-out keys exist once per calendar day.

### 19.12 Infrastructure tests

| test | file | assertion |
|---|---|---|
| Restart persistence | `test_terminal_persistence.py` | run §19.2–19.5 on a file DB; `db.close()`; a new `Runtime` on the same file: identical `cash()`, `positions()`, `lots`, orders, fills, NAV, `state_version`; the init is not re-posted; `integrity("full").ok`; `quote_cache` rows come back as `close`/`stale`, never `delayed` |
| Rollback on a failing write | `test_terminal_bus.py` | a registered test handler posts one valid entry then raises `RuntimeError`: no `journal_entries`, no `commands` row, 500, one attention row; a handler posting an unbalanced entry → `LedgerError` → rolled back; a handler that corrupts `gl_balances` (`+1`) → `IntegrityFailure` (L3) → rolled back, attention ERROR; retrying the same request_id after un-patching succeeds once |
| Idempotent replay | `test_terminal_bus.py` | two `order.submit` with one request_id → one order, `replayed=True`; `order.match` twice with the same idem key → one fill; same id + different payload → 409 `REQUEST_ID_REUSED`; a 503 `RETRY` path (patched `BEGIN IMMEDIATE` raising busy) stores nothing |
| Duplicate server | `test_terminal_server.py` | `serve` on a temp home in a thread; a second `serve` on the same home → exit 3 before opening the DB (lock); a bind on the same port with `allow_reuse_address=False` → `OSError` (Windows 10048); after the first stops, the second starts |
| Health is lock-free | `test_terminal_server.py` | hold the writer lock 2 s → `GET /api/health` answers in < 50 ms with the marker `app: finsim-terminal` |
| Graceful shutdown | `test_terminal_server.py` | loopback `POST /api/shutdown` stops within 3 s; off-loopback → 403; the lock is released |
| Freshness rules | `test_terminal_quotes.py` | table-driven: (quote age, fetch age, session open, error, method, provider_session) → quality and `is_executable` reason |
| Providers parse real shapes | `test_terminal_quotes.py` | fixtures `tests/fixtures/terminal/{yahoo_spy_1m.json, yahoo_jpy_1m.json, cboe_aapl_trimmed.json, treasury_curve.xml, fred_sofr.csv, finra_short.txt}` from the probe; the chain reducer keeps ≤ 400 days / ±35 %; UA policy per host asserted; circuit breaker opens after 5 failures and closes on success |
| API round trip | `test_terminal_api.py` | `Router.handle` without sockets: health shape; preview → submit → order → fills → transactions; error bodies; `X-Replayed`; 400 without request_id; one `http.client` test on a temp port |
| Launcher files | `test_terminal_app.py` | `launcher_files()` is pure: ASCII, names `FinSim Server.vbs`, contains the marker check `"finsim-terminal"`, the shutdown of a foreign server, `-m finsim serve --port 8765`; the install plan lists the old task and files to remove; `--offline` with the default home is refused |
| Reconciliation | `test_terminal_reconcile.py` | §19.1–19.8 in one DB: every L check ok; M checks ok with `FixedMarks`; `balance_sheet(ALL)` assets − liabilities == nav; trial balance debits == credits per currency and in base; NAV(ALL) == 10,000,000 + deposits + Σ realised + Σ income − Σ financing − Σ fees + unrealised + fx_result to the cent; each account's 3100 == Treasury advances; `transactions()` lists every entry of an account exactly once; `pnl(ALL, ITD)` residual 0.00 |
| Compatibility | existing suites | `py -3.12 -m unittest discover -s tests -p "test_terminal_*.py"` and `py -3.14 -m pytest tests/test_terminal_*.py`; the old FinSim subset (`test_app`, `test_tzfallback`, `test_quant`, `test_api`) and `tests/test_fs2_*.py` + `tests/test_desk_*.py` against the baseline captured before any change; `sha256(finsim.db)` identical before and after every terminal run |

### 19.13 Build 2 pins (`test_terminal_marks.py`, written now, run as `skipUnless(BUILD2)` until the packages land)

- Bond: par curve {2Y 4.00 %, 10Y 5.00 %}; a 4.500 % benchmark exactly 10 years out priced on a coupon date at y =
  5.00 %: annuity `(1 − 1.025^−20) / 0.025 = (1 − 0.610271) / 0.025 = 15.58916`; coupons `2.25 × 15.58916 = 35.0756`;
  principal `100 × 0.610271 = 61.0271`; clean **96.1027** per 100; accrued after 45 days of a 184-day period `2.25 ×
  45/184 = 0.5503`.
- CIP forward: S 1.1238, SOFR 3.87, ECBDFR 2.00, 90 days: `F = 1.1238 × (1 + 0.0387 × 90/360) / (1 + 0.0200 ×
  90/360) = 1.1238 × 1.009675 / 1.005 = 1.134673 / 1.005 = 1.129028`, labelled computed with both as-of dates.

---

### 19.14 Books, eligibility, lifetime reset (`test_terminal_books.py` WP-00; `test_terminal_lifetimes.py` WP-23)

- First run (`server.start` / `make_runtime(books="real")`): lifetimes `P1`, `F1`, `I1`; `init:capital:P1` 1,000,000.00,
  `F1` 25,000,000.00, `I1` 1,000,000,000.00; `nav("ALL_BOOKS")` = **1,026,000,000.00**; `I1:PRIVATE` exists at once
  (INSTITUTION); `P1:PRIVATE` does not (1,000,000 < 5,000,000); `F1:PRIVATE` is created by the first `GET /api/books`
  (`books.unlock_private`: 25,000,000 ≥ `qp_threshold_c[FUND]` 25,000,000) — the test asserts it is absent before and
  present after that call. A second start re-posts nothing (three request ids present). `make_runtime()` default = `T1` with 10,000,000.00 and
  the aliases `TREASURY = "T1:TREASURY"`, `US_EXCHANGE = "T1:US_EXCHANGE"` … so every other §19 scenario is unchanged.
- Eligibility: `eligibility(P1)` → `ecp False`, `pb_otc False` ("NAV 1,000,000.00 is below the ECP floor
  10,000,000.00 and the PB/OTC threshold 25,000,000.00"), `private_markets False` (threshold 5,000,000.00); `F1` →
  `pb_otc True` (profile FUND), `private True`; `I1` → both True. `book.onboard_pb {P1, GS}` → 400 `NOT_ELIGIBLE`;
  `otc.book` in P1 → 400 `NOT_ELIGIBLE`. Deposit 24,000,000.00 into P1 → NAV 25,000,000.00 → `pb_otc True`;
  `book.onboard_pb {P1, GS}` → 3 agreements rows (ISDA_MASTER, CSA, PBA, signed 2026-10-20) + `P1:PB_GS`; replay →
  the same rows (idempotent); withdraw 1,000,000.00 → NAV 24,000,000.00 → `pb_otc True` because `has_relationships`
  (INFO `ELIGIBILITY_BELOW_THRESHOLD`), a second broker (`JPM`) → 400 `NOT_ELIGIBLE` (new relationships need the
  threshold). Deposit 4,000,000.00 into a fresh P1 → NAV 5,000,000.00 → `GET /api/books` creates `P1:PRIVATE`.
- Cross-book transfer `P1:TREASURY → F1:US_EXCHANGE` → 400 `CROSS_BOOK`; L25 holds.
- Reset: in P1 fund 100,000.00 to `P1:US_EXCHANGE`, BUY 100 AAPL @ 339.50 (fee max(0.50, 1.00) = **1.00**), a resting
  LIMIT BUY 100 AAPL @ 300.00 (WORKING). Mark 340.00. `book.reset_lifetime {P, 1,000,000.00, "RESET"}` → the LIMIT
  order CANCELLED "lifetime reset"; `lifetimes.P1`: `ended_ts` now, `final_nav_c` = 1,000,000 − 1.00 + 100 × (340 −
  339.50) = **1,000,049.00**, `final_realised_c` 0.00, `final_pnl_c` = 1,000,049.00 − 1,000,000.00 = **49.00**, `status
  CLOSED`, `integrity_json.ok True`; a `nav_snapshots` row `P1:ALL` for today with nav 1,000,049.00; `P1:*` accounts
  CLOSED; `P2` created: `P2:TREASURY` 1010 **1,000,000.00** (`init:capital:P2`), five catalogue accounts, no PB.
  `nav("P1:ALL")` still 1,000,049.00 (frozen marks from the stored snapshot) after the mark moves to 350.00;
  `nav("P2:ALL")` 1,000,000.00; `nav("ALL_BOOKS")` = 1,000,000 + 25,000,000 + 1,000,000,000 = 1,026,000,000.00 (P1
  excluded: CLOSED). `treasury.deposit` into `P1:TREASURY` → 400 `LIFETIME_CLOSED`; `order.submit` in `P1:US_EXCHANGE`
  → 400 `ACCOUNT_CLOSED`; L23 holds; `GET /api/history/P1/accounting/pnl` answers with `read_only: true`. `confirm:
  "reset"` (lower case) → 400 `CONFIRM`. Replaying the reset request id → the stored result, no P3.
- `book.update_settings {book_defaults: {P: {starting_capital_c: 200000000}}}` → the next reset of P starts at
  2,000,000.00; P2's capital stays 1,000,000.00.

### 19.15 IRS swap: dealing, mark, DV01, IM, VM (CTM and STM), accruals, payment (`test_terminal_swaps.py` WP-17)

Fixture: `F1:PB_GS` (GS schedule IRS half-width 1.0 bp, `otc_spread_mult` 1, `swap_margin_mode` CTM), a `FixedCurve`
fixture giving semi-annual discount factors directly for a **flat 4.00 %**: `df(0.5) = 1/1.02 = 0.98039216`, `df(1) =
1/1.0404 = 0.96116878`, `df(1.5) = 1/1.061208 = 0.94232233`, `df(2) = 1/1.08243216 = 0.92384543`; Σ df = 3.80772870;
annuity (α = 0.5) = **1.90386435**. SOFR 3.87 (obs 2026-10-19). The swap: pay fixed, notional 10,000,000.00, 2 years
from Tue 2026-10-20, fixed 30/360 semi-annual, floating SOFR ACT/360 with `float_months 6` and `fixing TERM` (so both
legs share the schedule and the PV identity `PV = N × (par − K) × annuity` holds exactly).
- `otc.preview`: par = `2 × (1 − 0.92384543) / 3.80772870 = 0.15230914 / 3.80772870 = 0.04000000` (4.0000 %); dealt
  rate (pay fixed) = par + 1.0 bp = **4.0100 %**; `mark0 = 10,000,000 × (0.0400 − 0.0401) × 1.90386435 =
  −1,903.864` → **−1,903.86** ("cost of dealing"); `dv01 = 10,000,000 × 0.0001 × 1.90386435` = **1,903.86**; IM =
  1,903.86 × 50 = **95,193.00**.
- `otc.book`: `Dr 1330 SWP-000001 95,193.00 / Cr 1010 95,193.00` (the account held 100,000.00 → no sweep); contract
  OPEN, `mark_c −190386`, `vm_balance_c 0`; L22 ok. NAV(F1:PB_GS) = 100,000 − 95,193 + 95,193 − 1,903.86 =
  **98,096.14** (the overlay is the P&L).
- SWAP_VM day 1 (17:05, curve unchanged): Δ = −1,903.86 − 0 = −1,903.86 → we post: `Dr 1320 1,903.86 / Cr 1010
  1,903.86` (`swapvm:SWP-000001:2026-10-20`); `vm_balance_c −190386`; 1320 = 1,903.86; NAV unchanged 98,096.14 (cash
  −1,903.86, asset 1320 +1,903.86). Rerun → nothing.
- Day 2 curve **+10 bp** (flat 4.10 %): `df = 1/1.0205 = 0.97991181, 1/1.04142025 = 0.96022716, 1/1.06276937 =
  0.94093744, 1/1.08455614 = 0.92203474`; Σ = 3.80311115; annuity = 1.90155558; mark = `10,000,000 × (0.0410 − 0.0401)
  × 1.90155558 = 9,000 × 1.90155558` = **17,114.00**; Δ = 17,114.00 − (−1,903.86) = **19,017.86** → one entry: release
  `Dr 1010 1,903.86 / Cr 1320 1,903.86` and receive `Dr 1010 17,114.00 / Cr 2330 17,114.00`; `vm_balance_c 1711400`;
  1320 = 0, 2330 = 17,114.00 (L22). NAV = 100,000 − 95,193 (cash) − 1,903.86 + 1,903.86 + 17,114.00 (cash) + 95,193
  (1330) − 17,114.00 (2330) + 17,114.00 (overlay) = **117,114.00** = 100,000 + 17,114.00 ✓; P&L explain: Δunrealised
  (swap overlay) +19,017.86 vs the snapshot taken at −1,903.86; residual 0.
- Accruals (nights of 10-20 … 10-24, posted by ACCRUE_DAILY): fixed pay leg `10,000,000 × 0.0401 / 360 = 1,113.8889`
  → cumulative `to_c(1,113.8889) = 1,113.89`, `to_c(2,227.7778) = 2,227.78` (+1,113.89), `to_c(3,341.6667) = 3,341.67`
  (+1,113.89), `to_c(4,455.5556) = 4,455.56` (+1,113.89), `to_c(5,569.4444) = 5,569.44` (+**1,113.88**) → `Dr 4450 / Cr
  2340`; floating receive leg at 3.87 %: `10,000,000 × 0.0387 / 360` = **1,075.00** every night → `Dr 1225 / Cr 4450`.
  Net per night −38.89 (−38.88 on night 5).
- Payment date 2027-04-20 (`swappay:SWP-000001:2027-04-20`, after a catch-up tick posts every night): fixed =
  `10,000,000 × 0.0401 × 180/360` = **200,500.00** (the cumulative method lands exactly there: `to_c(200,500.00)`);
  floating = `10,000,000 × 0.0387 × 182/360` = **195,650.00** (182 actual days Oct 20 → Apr 20); net pay **4,850.00**:
  `Dr 2340 200,500.00 / Cr 1225 195,650.00 / Cr 1010 4,850.00`; 1225 = 2340 = 0 afterwards; SWAP_FIX on 2027-04-20
  re-fixes the floating leg from the latest SOFR ≤ that date.
- STM variant (`swap_margin_mode STM` on the schedule): day 1 `Dr 4450 1,903.86 / Cr 1010 1,903.86`, `mark_c` → 0;
  day 2 `Dr 1010 19,017.86 / Cr 4450 19,017.86`; 4450 cumulative +17,114.00; 1320 = 2330 = 0; NAV identical
  117,114.00 (realised instead of overlay).
- `otc.terminate` at the day-2 mark: `Dr 2330 17,114.00 / Cr 1010 17,114.00` (VM unwound), termination value
  17,114.00 `Dr 1010 / Cr 4450`, IM `Dr 1010 / Cr 1330 95,193.00`; status TERMINATED; L22 all zero. Missing curve →
  SWAP_VM SKIPPED `NO_CURVE`, mark unchanged, attention WARN. `otc.book` in `P1:*` → 400 NOT_ELIGIBLE; in a non-PB
  account → 400 NOT_TRADEABLE_HERE.

### 19.16 TRS and cross-currency (`test_terminal_swaps_trs.py` WP-26)

- TRS receive on 10,000 AAPL in `F1:PB_GS`, `reset_px` 339.50 (fresh delayed mark), notional **3,395,000.00**, spread
  0.60 % (GS); IM `to_c(0.10 × 3,395,000)` = **339,500.00** (`Dr 1330 / Cr 1010`, sweep from F1:TREASURY). Financing
  (kind `trsfin`): `3,395,000 × (0.0387 + 0.0060) / 360 = 151,756.50 / 360 = 421.5458` → **421.55** per night
  (cumulative: night 2 `to_c(843.0917) = 843.09` → +421.54), `Dr 4450 / Cr 2340`. Day 9 mark S 341.00 → mark
  15,000.00; day 10 S 342.00 → mark **25,000.00**, Δ 10,000.00 → `Dr 1010 / Cr 2330 10,000.00`. Dividend 0.26 ex in the
  period → `Dr 1225 / Cr 4450 2,600.00` (TRS receiver). TRS_RESET after 30 nights at CLOSE 345.00: return leg
  `(345.00 − 339.50) × 10,000` = **55,000.00** `Dr 1010 / Cr 4450`; dividend accrual settled `Dr 1010 2,600.00 / Cr
  1225`; financing cumulative `to_c(151,756.50 × 30/360) = to_c(12,646.375)` = **12,646.38** `Dr 2340 / Cr 1010`; the VM
  collateral received against the 55,000.00 mark is returned in the same entry `Dr 2330 55,000.00 / Cr 1010`;
  `reset_px` 345.00, notional **3,450,000.00**, mark 0, `vm_balance_c 0` (L22). Period P&L = 55,000 + 2,600 −
  12,646.38 = **44,953.62**; net cash of the reset entry = 55,000 + 2,600 − 12,646.38 − 55,000 = −10,046.38 (nothing is
  paid twice). Payer variant: every sign reversed.
- XCCY in `F1:PB_GS`: receive USD 11,238,000.00 fixed 4.00 % 30/360 semi / pay EUR 10,000,000.00 fixed 2.50 % 30/360
  semi, deal rate 1.1238, `mtm_reset True`, 2 years. F1:TREASURY holds EUR 10,000,000.00 (a EUR capital deposit in the
  test, base 11,238,000.00). IM `to_c(0.02 × 11,238,000)` = **224,760.00**. Start (`xccy:SWP-000002:start`): `Dr
  1010:USD 11,238,000.00 / Cr 2360:USD 11,238,000.00` and `Dr 1360:EUR 10,000,000.00 / Cr 1010:EUR 10,000,000.00` (EUR
  swept in from Treasury's EUR); each leg balanced in its own currency, base at 1.1238 → L1 ok. Nightly: USD receive
  `11,238,000 × 0.04 / 360 = 1,248.6667` → **1,248.67** `Dr 1225:USD / Cr 4450`; EUR pay `10,000,000 × 0.025 / 360 =
  694.4444` → **694.44** `Dr 4450:EUR / Cr 2340:EUR` (base at that command's rate). FX reset at CLOSE 1.1300
  (`xccyfx:SWP-000002:2026-11-20`): USD notional → `10,000,000 × 1.13` = 11,300,000.00; difference **62,000.00** received
  `Dr 1010:USD 62,000.00 / Cr 2360:USD 62,000.00`. Maturity: `Dr 2360:USD 11,300,000.00 / Cr 1010:USD` and `Dr 1010:EUR
  10,000,000.00 / Cr 1360:EUR`. `fx_result` after the reset at 1.13: the EUR receivable's base carrying +62,000.00
  offsets nothing on the USD side (the 62,000.00 came in as cash), so NAV moves by the FX effect only through 1360 →
  +62,000.00 − 62,000.00 (payable grew) = 0 → the MTM reset neutralises the notional FX exposure ✓ (asserted with
  `pnl` residual 0).

### 19.17 Private markets (`test_terminal_private.py` WP-19)

- Master file: `data/private_companies.json` has ≥ 20 rows; every non-null `post_money_usd` / `price_per_share` has
  a non-empty `source_url` and an `as_of` date; the named companies of §13.13 are present (by `company_id`); a row with
  a number and no source → the test fails (the file is data: this test guards the "never guessed" rule).
- Trade in `I1:PRIVATE` (NAV 1,000,000,000.00; `I1:PRIVATE` settled 0.00): the test inserts `PRV-TESTCO` with a round
  on 2026-09-01 at `price_per_share 100.00` (source "test fixture"). `private.trade BUY 10,000 @ 95.00 (5 % discount),
  fee_pct 0.03, settle_days 30, lockup_months 6` on Tue 2026-10-20: gross `10,000 × 95` = 950,000.00; fee `to_c(950,000
  × 0.03)` = **28,500.00**; need 978,500.00; available at the settle date 0 → SWEEP_IN **978,500.00** from
  `I1:TREASURY` linked to the fill; entry `Dr 1120 PRV-TESTCO 950,000.00 / Dr 5000 28,500.00 / Cr 2100 978,500.00`;
  settlement due **2026-11-19** (calendar T+30; SETTLE_DUE pays it on Thu 11-19); LONG lot 10,000 @ 95.00, `lock_until`
  **2027-04-20**; fill origin NEGOTIATED, basis `negotiated`. Mark 100.00 (quality `private`, "last round 2026-09-01
  (Series X)") → MV 1,000,000.00; unrealised **+50,000.00**; NAV(I1:ALL) = 1,000,000,000 − 28,500 + 50,000 =
  **1,000,021,500.00**; residual 0. Wed 11-19 settlement `Dr 2100 / Cr 1010 978,500.00`.
- `private.record_round {PRV-TESTCO, 2026-11-01, "Series Y", price_per_share 120.00, source_url "https://…"}` → mark
  120.00; unrealised `1,200,000 − 950,000` = **250,000.00**; without `source_url` → 400 `SOURCE_REQUIRED`.
- `private.trade SELL 2,000 @ 118.00` on 2026-11-20 (inside the lock-up): preview `warnings: [LOCKUP "lock-up until
  2027-04-20"]`, not a blocker; gross 236,000.00; fee `to_c(236,000 × 0.03)` = 7,080.00; FIFO cost `2,000 × 95` =
  190,000.00; realised **+46,000.00**; entry `Dr 1200 228,920.00 / Dr 5000 7,080.00 / Cr 1120 190,000.00 / Cr 4000
  46,000.00`; remaining 8,000 @ 95 cost 760,000.00, MV 960,000.00, unrealised 200,000.00; `risk.report.private_illiquid`
  MV 960,000.00 (0.096 % of NAV), lock-up calendar lists 2027-04-20. `SELL_SHORT` → 400 NOT_TRADEABLE_HERE; a trade in
  `P1` (no PRIVATE account) → 400 NOT_ELIGIBLE_QP; no `price` → 400 PRICE_REQUIRED. A company set PUBLIC with ticker
  `TESTCO` and a FixedMarks 125.00 → the position's mark 125.00 (quality of that record), note "listed 2026-12-01".

### 19.18 Private credit (`test_terminal_private_credit.py` WP-21)

`F1:CREDIT`, SOFR 3.87 (obs 10-19), HY OAS `BAMLH0A0HYM2` 3.00 % at issue. `pc.commit {borrower "Testco Holdings",
USD, principal 5,000,000.00, issue_price 98.00, SOFR + 6.00 %, 36 months, quarterly}` on Tue 2026-10-20: cash out
`5,000,000 × 98/100` = **4,900,000.00** (`Dr 1130 PC-000001 / Cr 1010`, swept from F1:TREASURY); maturity 2029-10-20
(1,096 nights: 365 + 366 + 365); next payment 2027-01-20.
- Nightly: coupon `5,000,000 × 0.0987 / 360 = 1,370.8333` → **1,370.83**, night 2 cumulative `to_c(2,741.6667) =
  2,741.67` (+**1,370.84**) `Dr 1220 / Cr 4300`; OID `100,000 × n / 1,096`: night 1 `to_c(91.2409)` = **91.24**, night 2
  `to_c(182.4818) = 182.48` (+91.24), night 3 `to_c(273.7226) = 273.72` (+91.24) `Dr 1130 / Cr 4300`. Mark with the OAS
  unchanged = amortised cost (`pc_model`, note "spread unchanged").
- PC_PAY 2027-01-20 (92 nights): coupon cumulative `to_c(5,000,000 × 0.0987 × 92/360) = to_c(126,116.6667)` =
  **126,116.67** → `Dr 1010 / Cr 1220 126,116.67`; 1220 = 0; next payment 2027-04-20. OID accreted by then `to_c(100,000
  × 92/1,096) = to_c(8,394.1606)` = **8,394.16** → 1130 = **4,908,394.16** (L24).
- `pc.amend_fee 25,000.00` → `Dr 1010 / Cr 4320 25,000.00` (income). OAS now 4.00 % (ΔOAS +1.00 %), 1,004 days remaining
  (`1,096 − 92`): `T = 1,004/365 = 2.7506849`; `pc_mark = to_c(4,908,394.16 × (1 − 0.01 × 2.7506849)) = to_c(4,908,394.16
  − 135,014.46)` = **4,773,379.70** (`4,908,394.16 × 0.0275 = 134,980.8394; × 0.000006849 = 33.6176; sum 135,014.457`);
  unrealised = 4,773,379.70 − 4,908,394.16 = **−135,014.46**. P&L ITD after the fee = income (126,116.67 + 8,394.16 +
  25,000.00) + unrealised −135,014.46; residual 0.
- `pc.repay 5,000,000.00` at par after 92 nights (OAS back to 3.00 %): `Dr 1010 5,000,000.00 / Cr 1130 4,908,394.16 /
  Cr 4000 91,605.84` (the unaccreted OID realised); loan REPAID; L24 zero. A missing SOFR for 10 days → the night is
  SKIPPED with WARN (never defaulted); `EUR` with no policy series → 400 CURRENCY_UNSUPPORTED.

### 19.19 Dividends and payments in lieu (`test_terminal_dividends.py` WP-18)

- `T1:US_EXCHANGE` long 1,000 AAPL since Mon 10-19; `FakeProvider.set_events("AAPL", dividends=[("2026-11-09", "0.26")])`
  → the refresher writes `dividends(AAPL, 2026-11-09, 0.26, pay_date NULL)`. DIVIDEND job on Mon 2026-11-09 00:40:
  `Dr 1210 AAPL 260.00 / Cr 4200 AAPL 260.00` (`div:T1:US_EXCHANGE:AAPL:2026-11-09`); position row `income_pending_c
  26000`, note "pays 2026-12-01 (convention: ex + 15 business days: 11-10…11-13, 11-16…11-20, 11-23…11-25, 11-27,
  11-30, 12-01; 11-26 is Thanksgiving)". DIVPAY on 2026-12-01: `Dr 1010 / Cr 1210 260.00`. A buy of 500 more on 11-09
  10:00 earns nothing for this ex-date (position at the prior close = 1,000). Rerun → nothing.
- Short 1,000 ABC (§19.5 setup) with `("2026-11-09", "0.10")`: `Dr 5400 ABC 100.00 / Cr 2320 ABC 100.00`; pay
  `Dr 2320 / Cr 1010 100.00`. P&L explain: Δincome +260.00, Δfinancing −100.00; residual 0.
- `set_events("AAPL", splits=[("2026-11-12", "4:1")])` → Attention WARN `SPLIT_MANUAL_REVIEW` for every account
  holding AAPL; no posting, lots unchanged (disclosed).

### 19.20 Performance statistics (`test_terminal_performance.py` WP-20; pure function)

Snapshots `T1:ALL` Mon 2026-10-12 … Fri 10-16: NAV **1,000,000.00, 1,010,000.00, 999,900.00, 1,029,898.00 (flows_day
10,000.00), 1,029,898.00**; SPY CLOSE marks **100.00, 102.00, 100.98, 100.98, 101.9898**; SOFR 3.87 %.
- Returns `r = [1,010,000/1,000,000 − 1, 999,900/1,010,000 − 1, (1,029,898 − 10,000)/999,900 − 1, 1,029,898/1,029,898 −
  1]` = **[0.0100, −0.0100, 0.0200, 0.0000]**; `m = [0.0200, −0.0100, 0.0000, 0.0100]` (100.98 × 1.01 = 101.9898).
- `mean(r)` 0.005; `ann_ret` = 0.005 × 252 = **1.2600**; `var(r, ddof=1) = (0.005² + 0.015² + 0.015² + 0.005²)/3 =
  0.0005/3 = 0.000166667`; `stdev` 0.01290994; `vol` = 0.01290994 × 15.874508 = **0.2049**; `rf` = 0.0387/360 =
  0.0001075; `sharpe` = (0.005 − 0.0001075)/0.01290994 × 15.874508 = 0.378971 × 15.874508 = **6.0160**; downside:
  `r − rf = [0.0098925, −0.0101075, 0.0198925, −0.0001075]` → `min(·,0)² = [0, 0.000102161, 0, 0.0000000116]`, mean
  0.0000255433, sqrt 0.00505403 → `sortino` = 0.0048925/0.00505403 × 15.874508 = 0.968040 × 15.874508 = **15.3672**;
  `mean(m)` 0.005; `cov(r, m) = (0.005×0.015 + (−0.015)(−0.015) + 0.015×(−0.005) + (−0.005)(0.005))/3 = (0.000075 +
  0.000225 − 0.000075 − 0.000025)/3 = 0.0002/3 = 0.0000666667`; `var(m) = (0.015² + 0.015² + 0.005² + 0.005²)/3 =
  0.0005/3 = 0.000166667`; `beta` = **0.4000**; `alpha` = (0.0048925 − 0.4 × 0.0048925) × 252 = 0.0029355 × 252 =
  **0.7397**; flow-adjusted index 1.0000, 1.0100, 0.9999, 1.019898, 1.019898 → `max_dd` = (1.01 − 0.9999)/1.01 =
  **0.0100**; `hit_rate` = 2/4 = **0.5000**; monthly October = (1,029,898 − 1,000,000 − 10,000)/1,000,000 =
  **0.019898**. One snapshot → `{status: "insufficient history"}`; a missing SPY close on one date → that pair is
  skipped and `pairs_skipped 1` reported; ratios compared to 4 dp.

### 19.21 Daily News (`test_terminal_news.py` WP-22)

Fixtures `yahoo_news.json` (4 items, one without a title → skipped, one with `relatedTickers`), `fed_rss.xml` (2
items), `newspaper_index.json` (3 entries: today `am` and `pm`, yesterday `pm`, plus a `learn` slot that is skipped).
With a stubbed `http_get` capturing the UA: the Yahoo call carries `BROWSER_UA`, the Fed call `GOV_UA`; the newspaper
is read from the fixture path (no network). `NewsStore.items()` returns 3 + 2 + (2 editions × (1 + 3 headlines) = 8) =
**13** items newest first, deduplicated by `(source, link)` on a second refresh (still 13); an item whose title contains
"ignore previous instructions" is returned verbatim and `injection_flags == 1`. `attention_summary` on the §19.5 book
after the Mon collateral call: `collateral_calls_today 1`, `working_orders` = the open count, `integrity.ok True`; with a
corrupted `gl_balances` row (§19.12) → `integrity.ok False`, `failing: ["L3"]` (D12: the failure shows under
"Requires your attention").

### 19.22 Trailing stops and all-or-none packages (`test_terminal_orders.py` WP-02, `test_terminal_strategies.py` WP-24)

- TRAILING_STOP SELL 1,000 AAPL `trail_amt 2.00`, last 339.50 at submit → `stop_px 337.50`, `trail_ref_px 339.50`;
  last 341.00 → RATCHET: ref 341.00, stop **339.00** (one order_event); last 340.00 → unchanged; last 338.90 →
  TRIGGER → fill 1,000 @ **338.90** (basis `stop_print`). `trail_pct 1.00` at last 341.00 → stop `341.00 × 0.99` =
  **337.59**. BUY `trail_amt 2.00` at 339.50 → stop 341.50; last 338.00 → stop **340.00**; last 340.10 → TRIGGER, fill
  @ 340.10. Both trail fields → 400 TRAIL_INVALID; `trail_pct 100` → TRAIL_INVALID. The ratchet survives a restart
  (`orders.stop_px` persisted).
- Strategy resolution: spot 769.32, listed strikes 700…840 step 5: `resolve_strike("pct:-7")` → target 715.4676 →
  **715**; `"ATM"` → 770; `"pct:+10"` → 846.252 → **840** (nearest listed); an override "-5" → 730.854 → 730; override
  "800" → 800. `expiry_for_tenor(today 2026-10-20, 3)` = the nearest listed expiry on or after 2026-10-20 + 3 months
  = 2027-01-20: over listed 2026-11-20, 2027-01-15, 2027-02-19 → **2027-02-19** (2027-01-15 is before the target); with
  2027-01-22 also listed → 2027-01-22. Sizing `units 1000`:
  PROTECTIVE_PUT → stock 1,000 + 10 puts; PARTIAL_COVERED_CALL (size 0.5) → 5 calls; CALL_RATIO_SPREAD (size 2.0) → 10
  + 20; BETA_HEDGED_LONG futures `n = max(1, round(1,000 × 769.32 / (7,754.25 × 50)))` = round(1.984) = **2** ES short.
  IRON_CONDOR → `fill_mode AON`; PROTECTIVE_PUT → SEQUENTIAL (primary stock); a TRS template in `T1:US_EXCHANGE` →
  `available False, reason "NOT ELIGIBLE / not a PB account"`.
- AON iron condor SPY 2026-11-20 ×10 (strikes from `pct` rules pinned to P700 / P730 / C800 / C830 by the fixture
  chain): legs BUY P700 ask 2.10 (ask_size 50), SELL P730 bid 4.00 (bid_size 50), SELL C800 bid 3.50 (**bid_size 5**),
  BUY C830 ask 1.20 (ask_size 50) → `evaluate_package` → NONE `AON_SIZE` (C800), nothing fills, every leg WORKING;
  next observation C800 bid_size 20 → one `order.match_package` (`matchpkg:{pkg}:{quote_time}`): premiums −2,100.00 +
  4,000.00 + 3,500.00 − 1,200.00 = **+4,200.00** credit; fees 4 × 10 × 0.65 = **26.00**; entries per §5.4 per leg
  (`Dr 1700 2,100 / Dr 5000 6.50 / Cr 2100 2,106.50`; `Dr 1700 1,200 / Dr 5000 6.50 / Cr 2100 1,206.50`; `Dr 1200
  3,993.50 / Dr 5000 6.50 / Cr 2900 4,000`; `Dr 1200 3,493.50 / Dr 5000 6.50 / Cr 2900 3,500`); margin once for the
  package: put spread `30 × 100 × 10 − (4,000 − 2,100)` = 28,100.00, call spread `30,000 − (3,500 − 1,200)` = 27,700.00,
  requirement = max = **28,100.00** → `Dr 1310 / Cr 1010 28,100.00`; L16. A stale chain → `AON_STALE`, nothing; a
  cancel of the package cancels all four legs; the same observation again → no second fill.

### 19.23 Prime-broker schedules, PB credit interest, monthly minimum, cash interest, loan reset (`test_terminal_prime.py` WP-07, `test_terminal_loans.py` WP-05, `test_terminal_lifecycle.py` WP-08)

- `F1:PB_GS` after onboarding: `account.update_schedule {equity_per_share "0.0025"}` → a 1,000 AAPL buy fee
  `1,000 × 0.0025` = **2.50**; `GET /api/accounts/F1:PB_GS/schedule` shows the edited field and the SETTINGS_CHANGED
  event with before 0.0030 / after 0.0025; the D10 buy at the seed schedule: 1,000 × 0.0030 = **3.00**.
- PB credit interest: settled USD 1,000,000.00 in `F1:PB_GS`, `credit_interest_spread 0.75 %` → rate `3.87 − 0.75 =
  3.12 %` → night `to_c(1,000,000 × 0.0312 / 360) = to_c(86.6667)` = **86.67** `Dr 1230 / Cr 4300` (`accr:pbcredit:…`);
  paid monthly with PAY_MONTHLY. Monthly minimum 500.00 with 120.00 of fees charged in the month → PB_MONTHLY_MIN posts
  **380.00** `Dr 5000 / Cr 1010`; with 600.00 of fees → nothing (SKIPPED).
- Treasury cash interest (directive §11): T settled USD 6,153,375.00 (after §19.2) at `3.87 − 0.50 = 3.37 %`: night
  `to_c(6,153,375 × 0.0337 / 360) = to_c(207,368.7375 / 360) = to_c(576.0243)` = **576.02** `Dr 1230 / Cr 4300`
  (`accr:cashint:USD:{d}`); paid on the 1st.
- Loan floating reset and early repayment (directive §9): the §19.6 EUR loan at 2.75 % posts 76.39 / 76.39 (cum
  152.78); a new ECBDFR observation 2.25 % (obs date D3) → rate 3.00 %, segment restart: night 3 `to_c(1,000,000 ×
  0.03 / 360)` = **83.33**, night 4 cum `to_c(166.6667) = 166.67` (+**83.34**); early repayment after night 4 with
  EUR cash: accrued 152.78 + 166.67 = **319.45** → `Dr 2755 319.45 / Dr 2750 1,000,000.00 / Cr 1010:EUR 1,000,319.45`.

### 19.24 Acceptance tests D1–D13 of `BUILD_PROMPT_2026-10-20.md` — where each lives

| D | test file :: method (hand-worked numbers) |
|---|---|
| D1 Treasury 10,000,000 → add 2,500,000 → 12,500,000, P&L 0 | `test_terminal_treasury.py::test_d1_initial_capital_and_deposit` (§19.1 on `T1`); the three real books' capitals in `test_terminal_books.py::test_first_run_three_books` |
| D2 Buy 100 AAPL with 0 cash: sweep = 100 × price + commission, linked, T+1, NAV −commission | `test_terminal_sweeps.py::test_d2_buy_100_aapl_zero_cash`: last 339.50 (Yahoo last is the executable price; no ask in the free feed, disclosed), gross 33,950.00, fee `max(100 × 0.005, 1.00)` = **1.00**, sweep **33,951.00** linked to the fill (`shortfall_c 3395100`), settles 2026-10-21, NAV(T1:ALL) **9,999,999.00** |
| D3 Restart between steps; a second server refuses | `test_terminal_persistence.py::test_restart_between_every_step`, `test_terminal_server.py::test_duplicate_server_exit_3` |
| D4 Short 1,000 with borrow; +10 % → call 1.02 × 1,000 × ΔP; −5 % → excess back; 3 nights over a weekend | `test_terminal_shorts.py::test_d4_collateral_marks_both_ways`: from 50.00 to **55.00** → required `to_c(1.02 × 1,000 × 55)` = 56,100.00, call **+5,100.00** = 1.02 × 1,000 × 5.00, paid by sweep (the account's cash swept out first); then **52.25** (−5 %) → required 53,295.00, **−2,805.00** returned; fee nights Fri/Sat/Sun per §19.5 |
| D5 No borrow → rejected; partial → scaled | `test_terminal_shorts.py::test_d5_no_borrow_and_partial` (§19.5: NO_BORROW; T3 → 800) |
| D6 Outside hours queues; buy limit fills only when price ≤ limit; DAY expires; cancelled/expired stay | `test_terminal_orders.py::test_d6_session_limit_day_expiry` (§19.9) + `test_terminal_api.py::test_orders_history_keeps_cancelled_expired` (`?status=all` and `/api/activity`) |
| D7 Stock + protective put together; partial fill resizes | `test_terminal_orders.py::test_d7_hedge_package_resize` (§19.9: 12,000 → 120, 21,000 → 210) + `test_terminal_hedges.py` |
| D8 Expiry on the real date: ITM call exercises, OTM lapses, short ITM assigned | `test_terminal_options.py::test_d8_expiry_exercise_lapse_assignment` (§19.7) |
| D9 Borrow 1,000,000 EUR, convert same day, T+2, repay needs EUR | `test_terminal_fx.py::test_d9_eur_loan_and_spot` (§19.6, every balance) |
| D10 Goldman account: its commission, DVP, margin per PB, exposure per PB | `test_terminal_prime.py::test_d10_goldman_buy` (fee 3.00, settlement rows in `F1:PB_GS`, headroom per PB) + `test_terminal_risk.py::test_exposure_per_pb` |
| D11 Rule 201 / 13D-G / self-trading notices, none block | `test_terminal_risk.py::test_d11_compliance_notices` |
| D12 integrity passes after every step; a corrupted entry fails and shows under "Requires your attention" | `assert_integrity` in every scenario; `test_terminal_bus.py::test_d12_corrupted_balance_rolls_back`; `test_terminal_news.py::test_attention_shows_integrity_failure`; `test_terminal_reconcile.py` |
| D13 Desk tests pass exactly as before | WP-16 baseline diff (§19.12 compatibility) |

## 20. Unsupported / limited (shown as such in the UI; never fabricated)

| item | state in Build 1 | how it is shown |
|---|---|---|
| Private equity DEAL FLOW (buyouts), investment banking, clients / institutions / investors, careers / missions, MBS / structured, exotic OTC (variance, vol, dividend, inflation swaps, barriers), commodity physicals, crypto | UNSUPPORTED (TERMINAL_DECISIONS §5) | grey "NOT IN THIS BUILD" rows wherever a list would show them; search says "not in this build"; the full list with this wording in Settings → Unsupported and `GET /api/unsupported` |
| Private credit (direct loans, OID, amendment fees, quarterly payments) | SUPPORTED, computed marks (§13.14, WP-21) — the earlier "unsupported" classification was wrong (B10 and the 17:48 amendment) | Credit page section; mark labelled computed with its inputs |
| Private markets (secondaries at negotiated prices, last-round marks) | SUPPORTED in eligible lifetimes (§13.13, WP-19); companies without a sourced price per share show "valuation only" / unavailable | Private Markets account page; QP sentence when locked |
| PB OTC swaps: IRS, TRS, cross-currency | SUPPORTED, computed marks, daily VM and resets (§13.11–13.12, WP-17/WP-26) in eligible lifetimes; dealt at the model par rate ± the PB's disclosed half-width (no observable OTC market) | PB page OTC tab; sentences SWAP_MARK / SWAP_VM / SWAP_RESET |
| Trailing stops; all-or-none option packages; the 60 strategy templates | SUPPORTED (§10.5 8a, §10.9, §13.16; WP-02 / WP-24) | ticket type TRAIL; hedge panel "Strategies" tab |
| Bonds (Treasury benchmarks, curated corporates), Debt Instruments execution | LIMITED — computed marks and the curve are shown; no execution | DEBT page shows the curve and the list with "model price, not executable in this build" |
| Foreign listings, foreign bonds | NOT IN THIS BUILD (resolve refuses non-US exchanges) | Foreign Markets & FX page shows FX spot only |
| FX forwards, FX options | NOT IN THIS BUILD (`fx.forward` → 400) | hedge panel shows the label for FX primaries |
| Repo / reverse repo, securities lending out of inventory | NOT IN THIS BUILD | Credit page sections present with the label |
| PB OTC options and forwards (FX / equity options, FRAs, caps) | NOT IN THIS BUILD (`/api/otc/preview` with those kinds → 400 `NOT_IN_THIS_BUILD`) | PB page OTC tab lists them with the label; swaps are live |
| Splits applied to lots | NOT IN THIS BUILD — a Yahoo split event raises an Attention WARN "manual review" (dividends and payments in lieu ARE in this build, §13.15) | positions row note on the split |
| Recall notices from lender data | LIMITED — INFO notice from the FINRA ratio heuristic only; no buy-ins ever | Attention INFO |
| Early assignment of short options | not simulated | glossary OCC sentence |
| Options on futures | NOT IN THIS BUILD | hedge panel label for futures primaries |
| Borrow availability / tiers | heuristic (FINRA + ADV), disclosed | ticket borrow block, sentence BORROW_AVAIL |
| Futures initial margins, CME holiday calendar, settlement price | static table / NYSE approximation / observed print | sentences FUT_MARGIN, CME_CAL, FUT_SETTLE |
| Live quotes | none — everything is delayed, close or computed | freshness on every price; `live` reserved |
| Cboe NBBO for equity fills | opportunistic (only when a fresh chain is already cached); otherwise Yahoo last | preview `price_basis` |
| Executions when Yahoo / Cboe are down | paused (stale) — no second equity source | Attention WARN, ticket blocker |
| Treasury interest on non-USD cash | 0 % | sentence CASH_INT |
| Policy rates for JPY/CHF/CAD/AUD loans | monthly OECD series (as-of month shown) | loan form note |
| Risk and compliance | warnings and notices only; not a compliance system | "warnings only" header on the Risk page |
| Phone / LAN access, access keys | not in this build (loopback only) | — |
| Performance statistics tab (Sharpe, Sortino, beta/alpha, drawdown, hit rate, monthly table) | SUPPORTED per lifetime (§6.9, WP-20); needs ≥ 2 daily snapshots | Portfolio → Performance shows "insufficient history (1 of 2 snapshots)" until then |
| Private marks under scenario shocks; VaR | not applied (private marks are not shocked; no VaR in this build) | Risk table note |
| OTC dealt rates | model par rate ± the PB's half-width (no observable quotes); IM = DV01 × 50 bp / 10 % TRS notional / 2 % XCCY notional, static | preview "cost of dealing"; sentence SWAP_MARK |

---

## 21. Judge risks and how this design resolves them

| risk (from the verdicts) | resolution |
|---|---|
| Cboe never fetched with an honest UA; options could be dead on arrival | §8.3: honest `TERMINAL_UA`; the WP-01 smoke probe is the first task; on 403 options become computed-only with `refused_ua` in health; never a bypass |
| Base-currency rounding unspecified; 3-line foreign entries fail by 0.01 | §5.3 plug rule in `units.base_with_plug`, applied per (currency, rate) group in `ledger.post`; §19.10 pins it |
| Shorts double-restricted (102 % collateral + frozen proceeds) | §13.1: 102 % collateral from own cash, proceeds settle as ordinary cash, no 1030; available = settled + receivables − payables − reserved; §19.5 numbers |
| Stale DAY orders after a laptop sleep could fill days later | §10.5 steps 2 and 4: `now > expires_ts` → no fill; `quote_time ≥ session.opened_at` required; §19.9 sleep test |
| Laptop off over an expiry / settlement day: closes missing, expiries wedge | §11.4: `YahooChart.daily()` closes labelled "recorded late"; OPTION_EXPIRY retries then uses the daily close; FUTURES_VM likewise; MARKS_RECORD at 16:20 |
| Yahoo is a delayed last-trade feed with no bid/ask and no guarantee | §1.3 FILL_PRICE, §8.2 (never `live`), preview `assumption` line, Cboe NBBO only opportunistically (§10.5 step 5); outages pause executions visibly |
| CME approximations (calendar, settlement print, margins) | §9 and §12.3 labelled; `session_overrides` setting; sentences CME_CAL / FUT_SETTLE / FUT_MARGIN on screen |
| Scope vs one session; integration drift | §1.1 Build 1 / Build 2 split; the workplan builds the vertical slice (core → Treasury → equities → lifecycle → server/UI → launcher) first and marks the rest LIMITED; file-exclusive packages with signatures fixed here |
| Fat-finger sweeps fund anything up to 10 M silently | §10.2 `CONFIRM_REQUIRED` above 2,000,000.00 sweep or 25 % of NAV; the UI asks once; lifecycle sweeps are shown |
| Multi-currency FX relief unspecified (the 4650 line) / unconventional fx_result | §6.4: no 4650; FX result is computed from the lines' base carrying, stated on screen (FX_RESULT); §19.6 worked to the cent incl. repayment |
| Income conventions diverge from reality (dividends, pay dates, borrow tiers, SOFR lag) | dividends from Yahoo chart events with the pay-date convention labelled (§13.15); tiers and RATE_LAG disclosed; every accrual memo carries the observation date |
| Owner amendments after the draft (swap VM + resets, lifetimes, three books, private markets) silently dropped | §13.10–§13.16 with hand-worked §19.14–§19.23, packages WP-17…WP-26; the UI shows NOT YET BUILT (never a fake number) for any of them that has not landed |
| OTC swaps need a "quote" that no free source gives | dealt at the model par rate ± the PB's disclosed half-width; mark0 is the visible cost of dealing; VM/resets keyed and idempotent; STM/CTM per schedule (§13.11) |
| Private valuations invented | the master is web-sourced at build time with a URL and as-of per number, nulls otherwise (test-guarded); owner-recorded rounds require a source URL; marks are `private`, never aged, never executable (§13.13) |
| A reset deletes history | reset closes accounts (ledger refuses CLOSED), stores the final figures and integrity report, keeps every row; History pages read the archived scope (§13.10, L23) |
| Integrity inside the transaction could stall the writer | §6.8: the scoped set is indexed on touched keys (O(touched)); the whole-DB set runs on a reader; §19.12 health < 50 ms while the lock is held 2 s |
| Operational violations: `legacy` on the live finsim.db, `wmic`, VBS deferring to any 200 | §18: `legacy` serves a backup copy; `stop` uses `OpenProcess` + image name (no wmic, no `os.kill`); the VBS checks the `finsim-terminal` marker and shuts the old engine down first; `install` records the finsim.db sha256 before and after |
| Memory: two SPX parses at once | §8.5 single-flight `ChainCache.request` shared by the chain page and the hedge panel; fetches serialised |
| Option freshness has no provider timestamp | §8.1: chain records take the underlying's `last_trade_time`; per-contract executable only when `bid > 0 and ask ≥ bid` |
| Observed-close capture timing (16:02 may miss the closing print) | §11.4: capture at 16:20 (16:35 index options); `CLOSE_REVISED` INFO when the next day's prev_close differs |
| Participation cap double counting / same-bar refill | §10.6: cap 0 on an unchanged `last_bar_ts`; `fills.idem_key` carries `bar_ts`; §19.9 same-bar test |
| Per-contract futures symbols unverified | §7.1 verify at the smoke test; front-month continuous fallback only for the front month, LIMITED; deferred months UNSUPPORTED |
| Fail-closed integrity halts trading on a provider outage / a check bug | §6.8: only the ledger-level L set can hold; mark-dependent checks WARN; the hold blocks only new submissions |
| Matcher isolation (one order's funding failure rolls back others) | §10.5: one `order.match` command per order per decision; funding failure → SUSPENDED, never a shared rollback |
| Windows specifics (pythonw stdout None, `os.kill`, old task/VBS names, SO_EXCLUSIVEADDRUSE) | §16 steps 1–2 and 5; §18 install removes `FinSim Server` task and the old VBS files and replaces `Startup\FinSim Server.vbs` |
| Silent death of a pythonw server on a stray `ZoneInfo` import | §16 step 2: `app.serve` wraps startup, logs the traceback, exits 5; `status` prints the log tail; every zone goes through `finsim.tzfallback.get_zone` (the terminal never imports `zoneinfo` directly) |
| Request-id reuse silently replays (execution) vs. rejections final (accounting) | §4: COMMITTED + same sha → replay; different sha → 409; a rejection leaves no row so a corrected retry with the same id works; the FK problem (requests row after entries) is avoided by inserting the commands row at step 4 |
| `positions` projection can drift | §3: positions are the VIEW `v_positions` over lots; the only projection is `gl_balances`, verified by L3 |
| Synthesised trades on lifecycle events (buy-ins, auto-borrow, cash-settle) | §5.4 / §13.3: needs-a-borrow cases SKIP with Attention and retry; no buy-ins; no cash-settling of physical options; 5700 never posted automatically |
| Option margin as an unposted reserve leaves available negative indefinitely | §13.3: margin moved to 1310 (cash leaves 1010), tied by L16, recomputed after every option fill |
| I13 cash floor vs mandatory VM with an empty Treasury | §11.6 overdraft rule: the loss is posted, `FUNDING_SHORTFALL` is opened in the same transaction, L18 accepts it while open, new orders are refused |
| HOUSEKEEPING purging order history | §11.2: never purges `order_events`; only unreferenced `quote_cache` rows |
| Sweep-out churn before VM / marks | §11.2: SWEEP_OUT at 17:30 once per calendar day, after VM (≤ 17:05) and collateral marks (17:10) |
| Fake data reaching the owner (`--offline`) | §1.2 rule 10 and §16: refused on the real home; permanent red banner; `/api/health.offline` |
| Thin names stale for 20 minutes look like a broken feed | §17.6: the ticket says "no observed print since hh:mm" and offers a resting limit |
| Tests must run on 3.12 (unittest) and 3.14 (pytest) | unittest style only, no pytest idioms; FakeClock injected everywhere; `skipUnless(BUILD2)` for Build 2 pins |
| `init:capital` payload change turning every start into a 409 | §3.1: the init runs only when its request_id is absent; L6 ties capital to the stored commands |
| Treasury sweep confirmation only as an open question | §10.2 CONFIRM_REQUIRED (settled here) |
| MARKET orders outside the session refused unless MOO | §10.2: not a block — orders rest (TERMINAL_DECISIONS §4); `expires_ts` from `day_order_expiry` |
| Reader connections left to GC in a long-lived process | §2.6: a pool of 4 connections, handed out and returned per request, closed at shutdown |
