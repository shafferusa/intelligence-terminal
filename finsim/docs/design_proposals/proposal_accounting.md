# FinSim Live Terminal — architecture proposal (accounting-first lens)

Author: Claude (Fable 5.1), 2026-10-20 16:20 ET. Status: PROPOSAL for the build team. Builds on
`docs/TERMINAL_DECISIONS.md` (not re-litigated), `docs/HANDOFF_2026-10-20.md` (newest wins), `docs/DIRECTIVE_2026-10-20.md`
and `docs/PROBE_2026-10-20.txt`. Everything here is concrete enough to build from; where a choice had to be made it is
made and marked **Decision**; where a fact was assumed it is marked **Assumption**.

Reading order for a coding agent: §1 (principles) → §2 (package layout, your module's signatures) → §3 (schema) →
§4 (CommandBus) → §5 (accounting definitions) → the section for your work package → §15 (tests) → §16 (build order).

---

## 0. One-paragraph summary

One SQLite file (`%USERPROFILE%\.finsim\terminal.db`), one process, one writer. Every change to money or positions is a
**command** executed by the `CommandBus` inside `BEGIN IMMEDIATE … COMMIT`, with a client-supplied `request_id` that
is unique in the database, so retries are free and nothing is ever applied twice. The command posts **journal entries**
(double-entry, one line per account × GL code × currency, amounts in exact integer micro-units), appends **events**,
updates the **projections** (`gl_balances`, `positions`, `lots`, `orders`, `obligations`, …) and runs `integrity()`
before committing; if any check fails the whole command rolls back and the caller gets the reason. The ledger is kept at
**cost** (trade-date accounting, settled vs pending cash as separate GL accounts); market value, unrealised P&L, FX
result and NAV are **computed, never posted**, by one module (`accounting.py`) from the ledger plus the current marks,
so there is no second cash or NAV that can diverge. Prices come only from a background `QuoteService` (Yahoo v8 chart,
Cboe delayed quotes, Treasury par curve, FRED), every mark carries source/time/quality, and stale or unavailable quotes
block executions. Lifecycle (settlement T+n, accruals, expiries, variation margin, 102 % collateral marks, loan
interest, sweeps) runs from the real clock as idempotent jobs keyed `(job, reference, date)`. The HTTP server never
holds the writer lock on a read; the UI polls small JSON snapshots and shows freshness on every price.

---

## 1. Principles the code must keep (checked by tests, not by goodwill)

1. **No money creation.** Capital enters only through `treasury.init_capital` (once, request_id `init:capital:1`) and
   explicit `treasury.deposit`. Accounts open empty. Internal transfers post Dr/Cr `3100 Treasury advances` on both
   sides and net to zero across accounts. There is no default amount anywhere in the code base (`capital=` defaults are
   forbidden; a missing amount is a 400).
2. **One ledger, cost basis, no MTM postings.** Unrealised P&L, market values, FX translation and NAV are read-model
   calculations (§5). The only P&L that is posted is realised: fills, expiries, exercises, variation margin, accruals,
   fees, dividends, coupons, interest.
3. **Atomic or nothing.** A command is a single SQLite transaction. Projections are updated in the same transaction and
   verified by `integrity()` before `COMMIT`.
4. **Idempotent by construction.** `commands.request_id`, `journal_entries.posting_key`, `fills.fill_key`,
   `obligations.obligation_key`, `job_runs.job_key` are UNIQUE. Background jobs derive their request ids from
   `(job, reference, date)`, so a job that runs twice (restart, catch-up) cannot post twice.
5. **Marks are observed or labelled.** Every price carries `source`, `quote_time`, `fetched_at`, `quality`
   (`live | delayed | close | computed | stale | unavailable`) and a delay label. Nothing is invented; a missing mark is
   `unavailable` and the figures that depend on it are shown as partial with the reason.
6. **Executions only against observed conditions.** A fill needs an open session, a fresh quote, and a quantity cap
   from observed volume (10 % participation of the last completed 1-minute bar, disclosed). No random prices, no
   invented recalls, no invented assignments.
7. **Decimal everywhere in accounting.** Money in integer micro-units in SQLite, `decimal.Decimal` in Python
   (`finsim.money.D/money/ZERO` reused). Floats are allowed only in pricing models (BSM, curve interpolation) and are
   converted with `D(repr(x))` before posting.
8. **Reads never wait for writes.** Request threads use their own read-only connections (WAL). Only `CommandBus.execute`
   takes the writer lock.
9. **Errors are loud.** Every exception in a worker is logged, counted in `/api/health`, and shown in the UI Attention
   strip. No `except: pass`.

---

## 2. Package layout: `finsim/finsim/terminal/`

Standard library only. Runs on Python 3.12 (pythonw) and 3.14. Time zones only via `finsim.tzfallback.get_zone`.
Reused from the old package (import, never edit): `finsim.money` (`D`, `money`, `price`, `qty`, `ZERO`),
`finsim.tzfallback.get_zone`, `finsim.calendar.us_equity_holidays` (rule fallback), `finsim.engines.options_pricing`
(`bsm`, `greeks`, `implied_vol`, `crr_tree`), `finsim.app` (`_browser_candidates`, `open_window`, `_win_special`,
`_known_desktop` for the launcher). The repo file `data/nyse-holidays.json` (repo root, two levels above `finsim/`) is
read at startup.

```
finsim/finsim/terminal/
  __init__.py        VERSION = "1.0.0"; APP_NAME = "FinSim Terminal"
  money.py           micro-unit helpers (to_u / from_u), rounding conventions
  clock.py           Clock protocol, real clock, ET helpers, trade-date rules
  calendar.py        holidays + early closes (nyse-holidays.json, rule fallback), business-day math, settlement dates
  sessions.py        session table per product group; open/closed/break; next open/close; DAY-order expiry time
  db.py              connection factory, PRAGMAs, schema DDL, migrations, backup
  ledger.py          chart of accounts, Line/Entry dataclasses, post(), balance rules, gl_balances upkeep
  treasury.py        handlers: treasury.init_capital / deposit / withdraw / transfer; transfer_inline() used by sweeps
  commands.py        Command, CommandBus, handler registry, errors, request-id idempotency
  accounting.py      ALL read models: cash, buying power, positions, lots, P&L, NAV, balance sheet, treasury, integrity()
  instruments.py     instrument master, symbol mapping (Yahoo/Cboe), futures specs, OCC option ids, universe seeding
  http.py            http_get with UA policy, retries, circuit breaker, provider health
  providers/yahoo.py       v8 chart: quote + 1m bars + daily bars + dividend events
  providers/cboe.py        delayed quotes: underlying NBBO + option chain (reduced)
  providers/treasury.py    daily par yield curve XML
  providers/fred.py        SOFR, DGS*, policy rates, OAS (CSV)
  providers/frankfurter.py ECB reference FX (fallback only)
  providers/finra.py       daily short volume (hard-to-borrow heuristic)
  quotes.py          QuoteCache + QuoteService (workers, subscriptions, freshness rules, MarkSet)
  pricing.py         computed marks: bonds from curve, FX forwards (CIP), option model fallback, futures fair value
  fees.py            fee schedules and rate tables (SOFR-based), day counts
  orders.py          validation, preview, submit/cancel, matching rules, partial fills, sweeps-in, hedge legs
  booking.py         fill → journal entry + lots (FIFO) + obligations; expiry/exercise/assignment bookings
  products/equities.py  product rules (shortable, settle days, fee), dividends
  products/options.py   OCC parsing, chain views, Reg-T margin, expiry/exercise/assignment rules
  products/futures.py   contract table, expiry rules, VM, initial/maintenance margin
  products/bonds.py     Treasury/corporate curated set, ACT/ACT accrual, coupons, curve pricing
  products/fx.py        spot/forward, value dates, spread cost
  products/credit.py    stock borrow (locate/borrow/return/collateral mark), currency loans, repo, PB financing
  lifecycle.py       job scheduler: due-job discovery (idempotent keys) + job handlers; catch-up after downtime
  hedges.py          hedge candidate generation from the live chain / futures table
  risk.py            warnings (exposure, concentration, Greeks, margin headroom) + compliance notices (inform only)
  api.py             routes → service functions; JSON encoding; error mapping
  server.py          ThreadingHTTPServer, single-instance guard, worker threads, health, shutdown
  app.py             CLI: serve/stop/status/install/open/uninstall/legacy; Windows VBS + shortcuts; log rotation
  static/            index.html, app.css, core.js, nav.js, ticket.js, chain.js, charts.js, page-*.js (§13)
```

### 2.1 Public signatures per module

`money.py`
```python
U = 1_000_000                                   # micro-units per currency unit / per share
def to_u(x: Decimal | int | str) -> int         # exact: Decimal(x) * U must be integral, else ValueError
def from_u(u: int) -> Decimal
def cents(x) -> Decimal                          # finsim.money.money (HALF_EVEN to 0.01)
def round_px(x: Decimal, tick: Decimal) -> Decimal   # nearest tick, HALF_EVEN
def mul_money(qty: Decimal, px: Decimal, multiplier: Decimal) -> Decimal   # cents(qty*px*multiplier)
```

`clock.py`
```python
ET = get_zone("America/New_York")
class Clock(Protocol):
    def now(self) -> datetime            # aware UTC
class RealClock(Clock): ...
class FakeClock(Clock):                  # tests: set(dt), advance(seconds)
def ny(now: datetime) -> datetime        # astimezone(ET)
def et_date(now: datetime) -> date
def trade_date(now: datetime, group: str) -> date   # CME groups: after 18:00 ET → next business day
def accounting_day(now: datetime) -> date           # accounting day D = (D-1 17:00 ET, D 17:00 ET]; before 17:00 ET → et_date(now), else et_date(now)+1 (§5.7)
def iso(dt: datetime) -> str                        # 'YYYY-MM-DDTHH:MM:SS.mmm+00:00'
```

`calendar.py`
```python
class TradingCalendar:
    def __init__(self, holidays_path: str | None, fallback=us_equity_holidays): ...
    def is_business_day(self, d: date) -> bool          # US (NYSE list; rule fallback when year missing)
    def is_holiday(self, d: date) -> bool
    def early_close(self, d: date) -> time | None       # 13:00 ET on listed days
    def next_business_day(self, d: date) -> date
    def prev_business_day(self, d: date) -> date
    def add_business_days(self, d: date, n: int) -> date
    def settlement_date(self, trade_date: date, settle_days: int) -> date
    def month_end(self, d: date) -> date
    def source(self) -> dict                            # {"file": path, "years": [...], "fallback_years": [...]}
```

`sessions.py`
```python
GROUPS = {"US_EQUITY","US_EQUITY_OPTIONS","US_INDEX_OPTIONS","CME_EQUITY_INDEX","CME_RATES","CME_ENERGY_METALS",
          "CME_AGS","CME_FX","FX_SPOT","US_BONDS","FOREIGN"}
@dataclass(frozen=True)
class SessionState:
    group: str; state: str            # OPEN | CLOSED | BREAK | HOLIDAY
    opened_at: datetime | None; closes_at: datetime | None; next_open: datetime | None
    trade_date: date; early_close: bool; note: str
def session_state(group: str, now: datetime, cal: TradingCalendar, foreign: dict | None = None) -> SessionState
def day_order_expiry(group: str, now: datetime, cal) -> datetime      # the close of the session the order can trade in
def is_open(group, now, cal) -> bool
```

`db.py`
```python
SCHEMA_VERSION = 1
def open_writer(path: str) -> sqlite3.Connection      # WAL, synchronous=FULL, foreign_keys=ON, busy_timeout=5000, isolation_level=None
def open_reader(path: str) -> sqlite3.Connection      # query_only=1, same PRAGMAs
def init_schema(conn) -> None                         # CREATE IF NOT EXISTS …; seeds chart, accounts, settings; records schema_meta
def migrate(conn) -> list[str]                        # forward-only steps keyed by schema_meta.schema_version
def backup(path: str, dest_dir: str, keep: int = 10) -> str | None   # sqlite3 backup API, before opening the writer
```

`ledger.py`
```python
CHART: dict[str, GlSpec]          # §3.2; GlSpec(code, name, kind, per_instrument, bucket)
@dataclass(frozen=True)
class Line:
    account_id: str; gl: str; currency: str; amount: Decimal      # signed, debit positive, quantised to cents
    instrument_id: str | None = None; lot_id: int | None = None; memo: str = ""; counterparty: str | None = None
    fx_rate: Decimal | None = None       # None → the command's FxTable rate for `currency`; FX-deal handlers set the deal rate explicitly
@dataclass(frozen=True)
class Entry:
    posting_key: str; kind: str; memo: str; lines: list[Line]; ts: datetime; trade_date: date
    ref_table: str | None = None; ref_id: str | None = None; event_id: int | None = None
class LedgerError(Exception)
def post(ctx: "TxContext", entry: Entry, fx: "FxTable") -> int        # validates, inserts entry+lines, updates gl_balances; returns entry id
def validate(entry: Entry, fx: "FxTable") -> None     # non-empty, each line non-zero, known GL/account/currency, Σ amount per currency == 0,
                                                     # Σ base_amount over the whole entry == 0, per_instrument codes carry instrument_id
def dr(account_id, gl, currency, amount, **kw) -> Line   # amount>0 → debit; negative flips (like the old helper)
def cr(account_id, gl, currency, amount, **kw) -> Line
```
`FxTable` = `{ccy: (rate_to_usd: Decimal, source: str)}` captured by the command at start (from `MarkSet`), used to fill
`base_amount_u` and `fx_rate` on every line (USD → `1`).

`commands.py`
```python
@dataclass
class Command:
    type: str; payload: dict; request_id: str; actor: str = "ui"   # actor: ui | job | worker | cli
class RejectError(Exception):   code: str; message: str; details: dict     # → HTTP 400, stored as REJECTED
class ConflictError(Exception): ...                                       # → HTTP 409 (stale quote, session closed, duplicate id with other payload)
class IntegrityFailure(Exception): ...                                     # → HTTP 500, rolled back, Attention
class TxContext:                     # what a handler sees
    conn: sqlite3.Connection; now: datetime; clock: Clock; cal: TradingCalendar; marks: "MarkSet"; fx: "FxTable"
    command_id: int; request_id: str; touched_accounts: set[str]
    def event(self, type: str, account_id=None, instrument_id=None, ref_table=None, ref_id=None, **payload) -> int
    def post(self, entry: Entry) -> int
class CommandBus:
    def __init__(self, db_path: str, clock: Clock, cal: TradingCalendar, quotes: "QuoteService", log): ...
    def register(self, type: str, handler: Callable[[TxContext, dict], dict], light: bool = False) -> None
    def execute(self, cmd: Command) -> dict      # returns the handler's result dict + {"request_id","command_id","replayed":bool}
    def close(self) -> None
```

`accounting.py` — see §5 for the definitions; signatures:
```python
class MarkSet(Protocol):          # provided by quotes.QuoteService.marks()
    def mark(self, instrument_id: str) -> Mark | None     # Mark(price: Decimal, currency, quality, source, quote_time, fetched_at, delay_label, kind)
    def fx(self, ccy: str) -> Mark | None                 # USD per 1 unit of ccy; USD → Mark(1, 'USD', 'live', 'const', …)
    def as_of(self) -> datetime
def cash(conn, account_id: str, cal, now) -> dict[str, CashView]
def buying_power(conn, account_id: str, currency: str, settle_date: date, marks) -> Decimal
def positions(conn, account_id: str | None, marks) -> list[PositionView]
def lots(conn, account_id: str, instrument_id: str) -> list[LotView]
def nav(conn, account_id: str | None, marks) -> NavView
def balance_sheet(conn, account_id: str | None, marks) -> list[BsRow]
def pnl(conn, account_id: str | None, marks, period: str, now) -> PnlView       # today | mtd | ytd | inception
def treasury(conn, marks) -> TreasuryView
def transactions(conn, account_id: str | None, filters: dict) -> list[dict]
def integrity(conn, scope: set[str] | None = None, marks=None) -> IntegrityReport   # ok: bool, problems: [{code, detail, account_id, …}], checked: [...], ms
```

`instruments.py`
```python
@dataclass
class Instrument: ...                 # mirrors the instruments table (§3)
def seed_from_universe(conn, universe_path: str) -> int
def get(conn, instrument_id) -> Instrument | None
def search(conn, q: str, limit=25) -> list[Instrument]
def resolve(conn, symbol: str, quotes: "QuoteService") -> Instrument    # unknown ticker → Yahoo chart meta → upsert (worker/light command)
def yahoo_symbol(inst) -> str | None;   def cboe_symbol(inst) -> str | None
def option_id(underlying: str, expiry: date, right: str, strike: Decimal) -> str     # 'O:SPY261120C00760000' (OCC)
def parse_option_id(id) -> tuple
def future_id(root: str, year: int, month: int) -> str                                # 'F:ESZ26'
FUTURES_SPECS: dict[str, FutSpec]      # §10.3
def futures_contract(conn, root: str, expiry_month: date) -> Instrument              # creates if missing
```

`quotes.py`
```python
@dataclass
class Quote: instrument_id, source, quote_time, fetched_at, last, bid, ask, bid_size, ask_size, prev_close, day_volume, bar_time, bar_volume, currency, delay_minutes, iv, greeks, error
class QuoteCache:                    # in-memory, RLock; persisted through the light command quotes.upsert_batch every 15 s
    def get(self, instrument_id) -> Quote | None
    def put(self, q: Quote) -> None
    def quality(self, q: Quote, session: SessionState, now) -> str         # §8.3 rules
class QuoteService:
    def __init__(self, bus, cache, providers, clock, cal, log, workers=2): ...
    def start(self); def stop(self, timeout=10)
    def subscribe(self, instrument_ids: Iterable[str], priority: str)     # 'orders' (15 s) | 'held' (60 s) | 'watch' (60 s) | 'page' (30 s, TTL 5 min)
    def refresh_now(self, instrument_ids, wait: float = 3.0) -> dict[str, Quote]   # synchronous bounded fetch for previews (never inside the writer lock)
    def chain(self, underlying_id: str, max_age_s=90) -> Chain | None       # Cboe, one underlying at a time
    def marks(self) -> MarkSet                                              # snapshot for a command / a read
    def health(self) -> dict
    def curve(self) -> Curve | None; def rate(self, name: str) -> RateObs | None
```

`orders.py`
```python
def preview(conn, quotes, cal, clock, req: dict) -> dict          # §9.2 (no writes; uses refresh_now)
def validate(ctx, req: dict) -> ValidatedOrder                     # raises RejectError/ConflictError
# command handlers
def h_submit(ctx, payload) -> dict        # 'order.submit'
def h_cancel(ctx, payload) -> dict        # 'order.cancel'
def h_match(ctx, payload) -> dict         # 'order.match' {order_id, quote: Quote snapshot}  (job/worker)
def h_expire(ctx, payload) -> dict        # 'order.expire' {order_id, reason}
def executable_price(order, q: Quote) -> tuple[Decimal | None, str]   # (price, basis) basis in {'ask','bid','last','limit'}
def fill_cap(order, q: Quote, inst) -> tuple[Decimal, str]             # (max qty this pass, disclosure)
def shortfall(ctx, account_id, currency, needed: Decimal, settle_date) -> Decimal
def sweep_in(ctx, account_id, currency, amount: Decimal, link: str) -> int | None   # posts the Treasury transfer; raises RejectError if Treasury cannot
```

`booking.py`
```python
def book_fill(ctx, order, fill: FillSpec) -> FillResult        # inserts fill, lots/lot_closes, position, entry, obligations
def book_expiry(ctx, position, mark: Mark, expiry: date) -> FillResult
def book_exercise(ctx, position, qty, strike, reason: str) -> FillResult   # long exercise / short assignment
def book_vm(ctx, account_id, instrument, settle_px: Decimal, prev_px: Decimal, qty: Decimal, date) -> int
def relieve_fifo(ctx, account_id, instrument_id, qty: Decimal, close_fill_id, price) -> tuple[Decimal, Decimal]  # (cost_relieved, realised)
```

`lifecycle.py`
```python
@dataclass
class Job: key: str; type: str; payload: dict; due: datetime; ref: str
def due_jobs(conn, now, cal, quotes) -> list[Job]       # discovery is pure: reads tables + job_runs, returns what is due and not yet done
class LifecycleWorker(threading.Thread):                 # every 60 s: due_jobs → bus.execute(Command(type, payload, request_id=key, actor='job')) one by one
def catch_up(conn, bus, now, cal, quotes, log) -> dict   # same thing, run at startup; returns counts
```

`api.py` / `server.py` / `app.py` — §12 and §14.

---

## 3. SQLite schema (DDL)

All timestamps ISO-8601 UTC text (`2026-10-20T20:15:03.120+00:00`), dates ISO (`2026-10-20`). Money and quantities are
INTEGER micro-units (`*_u`, 1e-6; 1,000 shares = 1_000_000_000). Prices and rates are TEXT decimal strings (never
summed in SQL). JSON blobs are TEXT.

```sql
PRAGMA journal_mode=WAL;  PRAGMA synchronous=FULL;  PRAGMA foreign_keys=ON;  PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- keys: schema_version, created_ts, instance_id (uuid4 at creation), app_version_last_opened

CREATE TABLE IF NOT EXISTS accounts (
  id            TEXT PRIMARY KEY,                       -- TREASURY | US_EXCHANGE | DEBT | CREDIT | FOREIGN_FX | PB_GS | PB_JPM | PB_MS | PB_DB | PB_BARC | PB_CITI | PB_UBS | PB_BNP
  name          TEXT NOT NULL,
  kind          TEXT NOT NULL CHECK (kind IN ('TREASURY','TRADING','PB')),
  base_ccy      TEXT NOT NULL DEFAULT 'USD',
  broker        TEXT,                                   -- PB only (display name)
  fee_schedule  TEXT NOT NULL DEFAULT 'DEFAULT',        -- fees.SCHEDULES key
  sweep_out     INTEGER NOT NULL DEFAULT 1,
  sweep_buffer_u INTEGER NOT NULL DEFAULT 10000000000,  -- USD 10,000.00
  pb_financing  INTEGER NOT NULL DEFAULT 0,
  status        TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED')),
  sort_order    INTEGER NOT NULL DEFAULT 100,
  opened_ts     TEXT NOT NULL, closed_ts TEXT
);

CREATE TABLE IF NOT EXISTS chart (
  gl_code TEXT PRIMARY KEY, name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('ASSET','LIABILITY','EQUITY','INCOME','EXPENSE')),
  per_instrument INTEGER NOT NULL DEFAULT 0,            -- 1: gl_balances keyed by instrument too
  bucket TEXT NOT NULL                                  -- read-model bucket (§5.6)
);

CREATE TABLE IF NOT EXISTS commands (
  id            INTEGER PRIMARY KEY,
  request_id    TEXT NOT NULL UNIQUE,                   -- idempotency
  type          TEXT NOT NULL, actor TEXT NOT NULL,
  payload       TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
  status        TEXT NOT NULL CHECK (status IN ('COMMITTED','REJECTED')),
  result        TEXT, error_code TEXT, error_message TEXT,
  started_ts    TEXT NOT NULL, finished_ts TEXT NOT NULL, duration_ms INTEGER NOT NULL,
  app_version   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  command_id INTEGER NOT NULL REFERENCES commands(id),
  ts TEXT NOT NULL, type TEXT NOT NULL,                 -- ORDER_SUBMITTED, ORDER_FILLED, ORDER_PARTIAL, ORDER_CANCELLED, ORDER_EXPIRED, ORDER_REJECTED, ORDER_SUSPENDED,
                                                        -- SETTLED, ACCRUED, CAPITAL_DEPOSITED, TRANSFER, SWEEP_IN, SWEEP_OUT, BORROW_OPENED, BORROW_RETURNED, COLLATERAL_MARKED,
                                                        -- LOAN_DRAWN, LOAN_REPAID, FX_DEALT, FX_SETTLED, OPTION_EXPIRED, OPTION_EXERCISED, OPTION_ASSIGNED, FUTURES_VM, FUTURES_EXPIRED,
                                                        -- MARGIN_MOVED, DIVIDEND, COUPON, INTEREST_PAID, NAV_SNAPSHOT, ATTENTION
  account_id TEXT REFERENCES accounts(id), instrument_id TEXT,
  ref_table TEXT, ref_id TEXT, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_acct_ts ON events(account_id, ts);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS journal_entries (
  id INTEGER PRIMARY KEY,
  command_id INTEGER NOT NULL REFERENCES commands(id),
  event_id INTEGER REFERENCES events(id),
  posting_key TEXT NOT NULL UNIQUE,                     -- 'fill:{fill_id}', 'settle:{obligation_id}', 'accr:{kind}:{ref}:{date}', 'vm:{acct}:{inst}:{date}', 'xfer:{request_id}', ...
  ts TEXT NOT NULL, trade_date TEXT NOT NULL,
  kind TEXT NOT NULL,                                   -- CAPITAL | TRANSFER | SWEEP | FILL | SETTLEMENT | ACCRUAL | INTEREST | FEE | VM | MARGIN | COLLATERAL | EXPIRY | EXERCISE | ASSIGNMENT | LOAN | REPO | FX | DIVIDEND | COUPON | RELEASE
  memo TEXT NOT NULL, ref_table TEXT, ref_id TEXT
);

CREATE TABLE IF NOT EXISTS journal_lines (
  id INTEGER PRIMARY KEY,
  entry_id INTEGER NOT NULL REFERENCES journal_entries(id),
  line_no INTEGER NOT NULL,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  gl_code TEXT NOT NULL REFERENCES chart(gl_code),
  currency TEXT NOT NULL,
  amount_u INTEGER NOT NULL CHECK (amount_u <> 0),      -- signed: debit +, credit −; native currency micro-units
  base_amount_u INTEGER NOT NULL,                       -- USD micro-units at fx_rate (debit +)
  fx_rate TEXT NOT NULL, fx_source TEXT NOT NULL,       -- '1' / 'const' for USD
  instrument_id TEXT, lot_id INTEGER, counterparty TEXT, memo TEXT,
  UNIQUE (entry_id, line_no)
);
CREATE INDEX IF NOT EXISTS ix_lines_acct_gl ON journal_lines(account_id, gl_code, currency);
CREATE INDEX IF NOT EXISTS ix_lines_inst ON journal_lines(account_id, instrument_id);
CREATE INDEX IF NOT EXISTS ix_lines_entry ON journal_lines(entry_id);

CREATE TABLE IF NOT EXISTS gl_balances (                 -- projection; verified by integrity() against journal_lines
  account_id TEXT NOT NULL, gl_code TEXT NOT NULL, currency TEXT NOT NULL,
  instrument_id TEXT NOT NULL DEFAULT '',               -- '' unless chart.per_instrument = 1
  balance_u INTEGER NOT NULL, base_u INTEGER NOT NULL,  -- Σ amount_u, Σ base_amount_u (debit positive)
  PRIMARY KEY (account_id, gl_code, currency, instrument_id)
);

CREATE TABLE IF NOT EXISTS instruments (
  id TEXT PRIMARY KEY,                                  -- 'AAPL' | 'O:SPY261120C00760000' | 'F:ESZ26' | 'FX:EURUSD' | 'B:UST-10Y-2036-08-15' | 'IDX:^VIX' | 'RATE:^TNX'
  kind TEXT NOT NULL CHECK (kind IN ('EQUITY','ETF','ADR','REIT','PREFERRED','OPTION','FUTURE','BOND','FX','INDEX','RATE')),
  name TEXT NOT NULL, currency TEXT NOT NULL,
  product_group TEXT NOT NULL,                          -- sessions.GROUPS
  venue TEXT, country TEXT, sector TEXT,
  multiplier TEXT NOT NULL DEFAULT '1', tick TEXT NOT NULL DEFAULT '0.01', lot_size TEXT NOT NULL DEFAULT '1',
  settle_days INTEGER NOT NULL DEFAULT 1,
  shortable INTEGER NOT NULL DEFAULT 1,
  support TEXT NOT NULL DEFAULT 'LIVE' CHECK (support IN ('LIVE','LIMITED','COMPUTED','UNSUPPORTED')),
  yahoo_symbol TEXT, yahoo_scale TEXT NOT NULL DEFAULT '1', cboe_symbol TEXT,
  underlying_id TEXT, expiry TEXT, strike TEXT, right TEXT CHECK (right IN ('C','P')), style TEXT, settlement TEXT,
  spec TEXT NOT NULL DEFAULT '{}',                      -- JSON: futures {root, margin_initial, margin_maint, expiry_rule}; bonds {coupon, freq, maturity, issue, daycount, kind}; equity {shares_outstanding, beta, cik, adv}
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','EXPIRED','DELISTED')),
  created_ts TEXT NOT NULL, updated_ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_inst_under ON instruments(underlying_id, expiry);

CREATE TABLE IF NOT EXISTS quotes (                     -- latest observation per instrument (persisted copy of the cache)
  instrument_id TEXT PRIMARY KEY REFERENCES instruments(id),
  source TEXT NOT NULL, quote_time TEXT, fetched_at TEXT NOT NULL,
  last TEXT, bid TEXT, ask TEXT, bid_size INTEGER, ask_size INTEGER, prev_close TEXT, day_volume INTEGER,
  bar_time TEXT, bar_volume INTEGER, bar_close TEXT,
  currency TEXT NOT NULL, delay_minutes INTEGER NOT NULL, iv TEXT, greeks TEXT, error TEXT
);

CREATE TABLE IF NOT EXISTS marks (                      -- end-of-day marks used by VM, collateral marks, expiries, NAV snapshots (append-only)
  instrument_id TEXT NOT NULL, mark_date TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('CLOSE','SETTLE','CURVE')),
  price TEXT NOT NULL, currency TEXT NOT NULL, source TEXT NOT NULL, quote_time TEXT, quality TEXT NOT NULL, note TEXT,
  PRIMARY KEY (instrument_id, mark_date, kind)
);

CREATE TABLE IF NOT EXISTS rates (                      -- daily reference rates (FRED/Treasury), append-only
  name TEXT NOT NULL, obs_date TEXT NOT NULL, value TEXT NOT NULL, source TEXT NOT NULL, fetched_at TEXT NOT NULL,
  PRIMARY KEY (name, obs_date)
);                                                      -- names: SOFR, DGS1MO.., ECBDFR, SONIA, BOJ, PAR_1M..PAR_30Y, BAMLC0A0CM, BAMLH0A0HYM2

CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY,                                  -- 'O-' + zero-padded sequence
  request_id TEXT NOT NULL UNIQUE,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  instrument_id TEXT NOT NULL REFERENCES instruments(id),
  side TEXT NOT NULL CHECK (side IN ('BUY','SELL','SELL_SHORT','BUY_TO_COVER','BUY_TO_OPEN','SELL_TO_OPEN','BUY_TO_CLOSE','SELL_TO_CLOSE')),
  qty_u INTEGER NOT NULL CHECK (qty_u > 0), filled_u INTEGER NOT NULL DEFAULT 0,
  order_type TEXT NOT NULL CHECK (order_type IN ('MARKET','LIMIT','STOP','STOP_LIMIT')),
  limit_px TEXT, stop_px TEXT,
  tif TEXT NOT NULL CHECK (tif IN ('DAY','GTC')),
  status TEXT NOT NULL CHECK (status IN ('WORKING','PARTIAL','FILLED','CANCELLED','EXPIRED','REJECTED','SUSPENDED')),
  triggered INTEGER NOT NULL DEFAULT 0,
  participation TEXT NOT NULL DEFAULT '0.10',
  submitted_ts TEXT NOT NULL, expires_ts TEXT NOT NULL,
  last_match_quote_time TEXT, closed_ts TEXT, reason TEXT,
  avg_px TEXT, fees_u INTEGER NOT NULL DEFAULT 0,
  group_id TEXT, parent_order_id TEXT REFERENCES orders(id), hedge_spec TEXT,   -- hedge legs: parent = primary; hedge_spec JSON {kind, ratio, …}
  preview TEXT NOT NULL                                 -- JSON snapshot shown to the user at submission (quote, fees, sweep, settle date)
);
CREATE INDEX IF NOT EXISTS ix_orders_open ON orders(status, instrument_id);

CREATE TABLE IF NOT EXISTS fills (
  id INTEGER PRIMARY KEY,
  fill_key TEXT NOT NULL UNIQUE,                        -- 'fill:{order_id}:{quote_time_epoch}' | 'expiry:{acct}:{inst}:{date}' | 'exercise:{acct}:{inst}:{date}:{n}' | 'assign:…' | 'buyin:{borrow_id}' | 'maturity:{acct}:{inst}:{date}'
  order_id TEXT REFERENCES orders(id),
  account_id TEXT NOT NULL REFERENCES accounts(id),
  instrument_id TEXT NOT NULL REFERENCES instruments(id),
  kind TEXT NOT NULL CHECK (kind IN ('TRADE','EXPIRY','EXERCISE','ASSIGNMENT','BUY_IN','MATURITY')),
  qty_u INTEGER NOT NULL CHECK (qty_u <> 0),            -- signed position change (+ long / − short)
  price TEXT NOT NULL, currency TEXT NOT NULL,
  gross_u INTEGER NOT NULL, fee_u INTEGER NOT NULL DEFAULT 0, accrued_u INTEGER NOT NULL DEFAULT 0,
  ts TEXT NOT NULL, trade_date TEXT NOT NULL, settle_date TEXT,
  quote_source TEXT, quote_time TEXT, quote_quality TEXT, price_basis TEXT,   -- basis: ask|bid|last|limit|strike|zero|settle
  bar_volume INTEGER, cap_u INTEGER, note TEXT
);
CREATE INDEX IF NOT EXISTS ix_fills_pos ON fills(account_id, instrument_id);

CREATE TABLE IF NOT EXISTS positions (                  -- projection; verified against fills and lots
  account_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
  qty_u INTEGER NOT NULL,                               -- signed
  basis_u INTEGER NOT NULL,                             -- long: Σ lot cost; short: Σ lot proceeds (both positive); native currency
  currency TEXT NOT NULL,
  settle_px TEXT,                                       -- futures: last settlement price VM was posted against (initial = fill price)
  opened_ts TEXT NOT NULL, updated_ts TEXT NOT NULL,
  PRIMARY KEY (account_id, instrument_id)
);

CREATE TABLE IF NOT EXISTS lots (
  id INTEGER PRIMARY KEY,
  account_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
  open_fill_id INTEGER NOT NULL REFERENCES fills(id),
  side TEXT NOT NULL CHECK (side IN ('LONG','SHORT')),
  open_ts TEXT NOT NULL, open_qty_u INTEGER NOT NULL, remaining_u INTEGER NOT NULL CHECK (remaining_u >= 0),
  price TEXT NOT NULL, basis_u INTEGER NOT NULL, currency TEXT NOT NULL      -- basis_u = gross cost (long) or gross proceeds (short) of the open qty
);
CREATE INDEX IF NOT EXISTS ix_lots_open ON lots(account_id, instrument_id, remaining_u);

CREATE TABLE IF NOT EXISTS lot_closes (
  id INTEGER PRIMARY KEY,
  lot_id INTEGER NOT NULL REFERENCES lots(id), close_fill_id INTEGER NOT NULL REFERENCES fills(id),
  qty_u INTEGER NOT NULL, close_price TEXT NOT NULL,
  basis_relieved_u INTEGER NOT NULL, proceeds_u INTEGER NOT NULL, realised_u INTEGER NOT NULL, ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS obligations (                -- settlement obligations (cash legs due on a date)
  id INTEGER PRIMARY KEY,
  obligation_key TEXT NOT NULL UNIQUE,                  -- 'obl:fill:{fill_id}' | 'obl:fx:{deal_id}:{BUY|SELL}' | 'obl:exercise:{fill_id}'
  account_id TEXT NOT NULL REFERENCES accounts(id),
  direction TEXT NOT NULL CHECK (direction IN ('IN','OUT')),
  currency TEXT NOT NULL, amount_u INTEGER NOT NULL CHECK (amount_u > 0),
  gl_pending TEXT NOT NULL,                             -- 1200 | 2100 | 1250 | 2150 (the pending account carrying it)
  gl_settled TEXT NOT NULL,                             -- 1010 | 1030
  instrument_id TEXT, fill_id INTEGER, deal_id INTEGER,
  trade_date TEXT NOT NULL, due_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PENDING','SETTLED')), settled_ts TEXT, entry_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_obl_due ON obligations(status, due_date);

CREATE TABLE IF NOT EXISTS borrows (
  id INTEGER PRIMARY KEY, borrow_key TEXT NOT NULL UNIQUE,     -- 'borrow:{request_id}'
  account_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
  qty_u INTEGER NOT NULL, collateral_u INTEGER NOT NULL, collateral_ccy TEXT NOT NULL,
  fee_rate TEXT NOT NULL, rebate_rate TEXT NOT NULL, tier TEXT NOT NULL,          -- tier: GC | WARM | HTB
  lender TEXT NOT NULL DEFAULT 'Street',
  opened_ts TEXT NOT NULL, closed_ts TEXT,
  status TEXT NOT NULL CHECK (status IN ('OPEN','CLOSED','RECALLED')),
  accrued_fee_u INTEGER NOT NULL DEFAULT 0, accrued_rebate_u INTEGER NOT NULL DEFAULT 0,
  last_accrual_date TEXT, last_mark_date TEXT, reason TEXT
);

CREATE TABLE IF NOT EXISTS loans (
  id INTEGER PRIMARY KEY, loan_key TEXT NOT NULL UNIQUE,
  account_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('CASH_LOAN','PB_MARGIN','REPO','REVERSE_REPO')),
  currency TEXT NOT NULL, principal_u INTEGER NOT NULL,
  rate_kind TEXT NOT NULL CHECK (rate_kind IN ('FLOAT','FIXED')), index_name TEXT, spread TEXT NOT NULL DEFAULT '0', fixed_rate TEXT,
  day_count TEXT NOT NULL,                              -- ACT/360 | ACT/365
  start_date TEXT NOT NULL, maturity_date TEXT,         -- NULL = open / on demand
  accrued_u INTEGER NOT NULL DEFAULT 0, last_accrual_date TEXT,
  collateral_instrument_id TEXT, collateral_qty_u INTEGER, haircut TEXT,
  counterparty TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('OPEN','CLOSED')), closed_ts TEXT
);

CREATE TABLE IF NOT EXISTS fx_deals (
  id INTEGER PRIMARY KEY, deal_key TEXT NOT NULL UNIQUE,
  account_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('SPOT','FORWARD')),
  buy_ccy TEXT NOT NULL, sell_ccy TEXT NOT NULL, buy_u INTEGER NOT NULL, sell_u INTEGER NOT NULL,
  rate TEXT NOT NULL, mid TEXT NOT NULL, spread TEXT NOT NULL, quote_source TEXT NOT NULL, quote_time TEXT,
  trade_ts TEXT NOT NULL, value_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PENDING','SETTLED')), settled_ts TEXT
);

CREATE TABLE IF NOT EXISTS job_runs (                   -- idempotency + audit of lifecycle jobs
  job_key TEXT PRIMARY KEY,                             -- '{job}:{ref}:{date}' (same string is the command request_id)
  job TEXT NOT NULL, ref TEXT NOT NULL, run_date TEXT NOT NULL, ts TEXT NOT NULL,
  command_id INTEGER, status TEXT NOT NULL CHECK (status IN ('DONE','SKIPPED','FAILED')), note TEXT
);

CREATE TABLE IF NOT EXISTS nav_snapshots (
  account_id TEXT NOT NULL, snap_date TEXT NOT NULL, ts TEXT NOT NULL,
  nav_u INTEGER NOT NULL, cash_u INTEGER NOT NULL, mv_u INTEGER NOT NULL, unrealised_u INTEGER NOT NULL,
  realised_cum_u INTEGER NOT NULL, income_cum_u INTEGER NOT NULL, financing_cum_u INTEGER NOT NULL, fees_cum_u INTEGER NOT NULL,
  fx_result_u INTEGER NOT NULL, capital_cum_u INTEGER NOT NULL, transfers_cum_u INTEGER NOT NULL,
  quality TEXT NOT NULL, detail TEXT NOT NULL,          -- worst mark quality; JSON per-position marks used
  PRIMARY KEY (account_id, snap_date)
);

CREATE TABLE IF NOT EXISTS attention (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('INFO','WARN','ERROR')),
  source TEXT NOT NULL, code TEXT NOT NULL, message TEXT NOT NULL, details TEXT, resolved_ts TEXT
);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS watchlist (account_id TEXT NOT NULL, instrument_id TEXT NOT NULL, added_ts TEXT NOT NULL, PRIMARY KEY (account_id, instrument_id));
CREATE TABLE IF NOT EXISTS sequences (name TEXT PRIMARY KEY, next INTEGER NOT NULL);   -- order ids
```

Uniqueness that enforces idempotency and "no duplicate posting": `commands.request_id`, `journal_entries.posting_key`,
`fills.fill_key`, `obligations.obligation_key`, `job_runs.job_key`, `borrows.borrow_key`, `loans.loan_key`,
`fx_deals.deal_key`, `orders.request_id`, `nav_snapshots` PK, `marks` PK. A duplicate insert raises
`sqlite3.IntegrityError`, which the bus maps to a `ConflictError` ("already posted") **after** confirming the stored
command has the same `payload_sha256` (same id, different payload → 409 `REQUEST_ID_REUSED`).

### 3.1 Seed rows (`db.init_schema`)

- `accounts`: TREASURY (kind TREASURY, sort 0), US_EXCHANGE (TRADING, 10), DEBT (TRADING, 20), CREDIT (TRADING, 30),
  FOREIGN_FX (TRADING, 40). PB accounts are created by `account.open_pb` (ids `PB_GS`, `PB_JPM`, `PB_MS`, `PB_DB`,
  `PB_BARC`, `PB_CITI`, `PB_UBS`, `PB_BNP`).
- `settings`: `participation_rate=0.10`, `sweep_out_time_et=16:30`, `eod_snapshot_time_et=17:05`,
  `treasury_cash_rate_spread=-0.0050`, `base_ccy=USD`, `quote_stale_grace_min=5`, `cboe_chain_ttl_s=90`.
- `chart`: §3.2.
- The initial capital is NOT seeded by the schema: `server.start()` executes
  `Command("treasury.init_capital", {"amount": "10000000.00", "currency": "USD", "source": "initial capital"}, request_id="init:capital:1", actor="cli")`
  on every start; the second and later starts are replays (returns the stored result, posts nothing).

### 3.2 Chart of accounts (`ledger.CHART`)

Balances are kept per `(account_id, gl_code, currency[, instrument_id])`. `bucket` is the read-model category.

| code | name | kind | per_instr | bucket | notes |
|---|---|---|---|---|---|
| 1010 | Cash — settled | ASSET | 0 | cash_settled | spendable |
| 1030 | Cash — restricted (short-sale proceeds) | ASSET | 1 | cash_restricted | released on cover, pro rata |
| 1100 | Investments — long, at cost | ASSET | 1 | positions_cost | equities, ETFs, bonds (clean), options bought |
| 1150 | Accrued interest receivable — bonds | ASSET | 1 | accrued_income | ACT/ACT |
| 1160 | Accrued interest receivable — cash & rebates | ASSET | 0 | accrued_income | Treasury idle cash, borrow rebates |
| 1200 | Receivable — unsettled sales | ASSET | 1 | cash_pending_in | securities |
| 1210 | Dividends receivable | ASSET | 1 | accrued_income | ex-date to pay date |
| 1250 | FX receivable — unsettled | ASSET | 0 | cash_pending_in | per currency |
| 1300 | Margin deposit — futures & short options | ASSET | 0 | margin | clearing-house style, cash moved |
| 1400 | Cash collateral posted — stock borrow | ASSET | 1 | collateral | 102 % marked daily |
| 1410 | Cash collateral posted — PB / CSA | ASSET | 0 | collateral | OTC (limited) |
| 1500 | Reverse repo receivable | ASSET | 0 | financing_asset | cash lent |
| 2100 | Payable — unsettled purchases | LIABILITY | 1 | cash_pending_out | |
| 2150 | FX payable — unsettled | LIABILITY | 0 | cash_pending_out | |
| 2200 | Securities sold short — at proceeds | LIABILITY | 1 | positions_short | |
| 2210 | Options written — at premium | LIABILITY | 1 | positions_short | |
| 2300 | Accrued interest payable — PB margin | LIABILITY | 0 | accrued_expense | |
| 2310 | Accrued borrow fees payable | LIABILITY | 1 | accrued_expense | per borrow |
| 2320 | Dividends payable in lieu | LIABILITY | 1 | accrued_expense | shorts over ex-date |
| 2330 | Accrued interest payable — repo | LIABILITY | 0 | accrued_expense | |
| 2340 | Accrued interest payable — currency loans | LIABILITY | 0 | accrued_expense | |
| 2400 | Cash collateral received | LIABILITY | 0 | collateral_received | reverse repo margin (limited) |
| 2500 | Repo borrowing | LIABILITY | 0 | financing | |
| 2600 | Currency loans — principal | LIABILITY | 0 | financing | |
| 2700 | PB margin loan | LIABILITY | 0 | financing | |
| 3000 | Capital contributed | EQUITY | 0 | capital | Treasury only |
| 3100 | Treasury advances (internal transfers) | EQUITY | 0 | transfers | nets to 0 across accounts |
| 3900 | FX conversion clearing | EQUITY | 0 | fx_clearing | nets to 0 in base |
| 4000 | Realised P&L — trading | INCOME | 1 | realised | fills, expiries, exercises |
| 4100 | Futures variation margin | INCOME | 1 | realised | daily cash settlement |
| 4200 | Dividend income | INCOME | 1 | income | |
| 4300 | Interest income — bonds | INCOME | 1 | income | coupons + accrual |
| 4310 | Interest income — cash | INCOME | 0 | income | Treasury idle USD |
| 4320 | Rebate income — cash collateral | INCOME | 1 | income | |
| 4330 | Interest income — reverse repo | INCOME | 0 | income | |
| 5000 | Commissions & fees | EXPENSE | 1 | fees | |
| 5100 | Interest expense — currency loans | EXPENSE | 0 | financing_cost | |
| 5110 | Interest expense — PB margin | EXPENSE | 0 | financing_cost | |
| 5120 | Interest expense — repo | EXPENSE | 0 | financing_cost | |
| 5200 | Securities borrow fees | EXPENSE | 1 | financing_cost | |
| 5210 | Dividends paid in lieu | EXPENSE | 1 | financing_cost | |
| 5300 | Buy-in penalties | EXPENSE | 1 | financing_cost | only on an observed recall |

Sign convention in storage: `amount_u` debit-positive for every kind. Natural-sign balance for display:
`ASSET/EXPENSE = balance_u`, `LIABILITY/EQUITY/INCOME = −balance_u`.

**Decision — multi-currency rule.** Every entry balances **per currency** (Σ `amount_u` = 0 for each currency in the
entry) **and in base** (Σ `base_amount_u` = 0 over the entry). A cross-currency deal is two legs through `3900 FX
conversion` (one leg per currency), each leg balanced in its own currency; both legs carry the same deal rate
(`Line.fx_rate` set by the handler) so `3900` nets to zero in base. Every other line's `base_amount_u` is
`amount × fx_rate` at the rate captured at the start of the command (`TxContext.fx`, one rate per currency, so a
single-currency entry balances in base automatically). `base_amount_u` is used for the FX-result calculation (§5.5)
and for P&L buckets at posting-time rates; cash and positions are always translated at the current mark.

---

## 4. The CommandBus contract

```
execute(cmd):
  1. acquire writer RLock (process-wide); timeout 30 s → ConflictError WRITER_BUSY (503 on HTTP)
  2. conn.execute("BEGIN IMMEDIATE")
  3. existing = SELECT … FROM commands WHERE request_id=?
     - if existing and status=COMMITTED and sha256(payload)==existing.sha → ROLLBACK; return stored result + replayed=True
     - if existing and status=REJECTED and same sha → ROLLBACK; raise RejectError(stored error)      (a rejection is final for that id)
     - if existing and different sha → ROLLBACK; raise ConflictError REQUEST_ID_REUSED
  4. INSERT commands(status='COMMITTED' placeholder …) → command_id       (row is updated at step 8; rolled back on failure)
  5. ctx = TxContext(conn, now=clock.now(), marks=quotes.marks(), fx=marks→FxTable, …)
  6. result = handler(ctx, payload)        # may raise RejectError / ConflictError / any Exception
  7. if not light: report = accounting.integrity(conn, scope=ctx.touched_accounts, marks=ctx.marks)
     - if not report.ok → raise IntegrityFailure(report)
  8. UPDATE commands SET result=json(result), finished_ts, duration_ms
  9. COMMIT; release lock; notify post-commit hooks (quote subscriptions for new instruments, UI attention, matcher wake)
  on RejectError: ROLLBACK the handler's work, then in a NEW short transaction INSERT commands(status='REJECTED', error_*) and COMMIT
       (so the rejection is remembered for the id), re-raise
  on ConflictError / IntegrityFailure / sqlite3.Error / any other: ROLLBACK everything (nothing is recorded for the id),
       log.exception, append attention(ERROR) in a separate transaction, re-raise
```

- `request_id`: the UI generates `ui:<uuid4>` per click; retries reuse it. Jobs use deterministic keys
  (`settle:obl:{id}`, `accr:borrow_fee:{borrow_id}:{date}`, `vm:{acct}:{inst}:{date}`, `expiry:{acct}:{inst}:{date}`,
  `sweep_out:{acct}:{date}`, `eod:{date}`), which are also written to `job_runs.job_key`.
- Handlers never open their own transactions, never sleep, never do network I/O. Previews that need a fresh quote call
  `quotes.refresh_now` **before** `bus.execute`; the quote snapshot used is passed in the payload so the command is
  reproducible.
- Background workers call `bus.execute` exactly like the API. Failures are recorded in `job_runs` (FAILED + note) and
  `attention`; the lifecycle worker retries a FAILED job on its next tick with the same key (bounded: after 3 failures
  the job is parked for 1 hour and shown in Attention with the exception text).
- `light=True` handlers (`quotes.upsert_batch`, `instruments.upsert`, `watchlist.*`, `settings.set`) skip `events`,
  `integrity()` and the sha replay check (they are naturally idempotent upserts) but still take the writer lock.
- Error → HTTP mapping: `RejectError` 400, `NotFound` 404, `ConflictError` 409, `WRITER_BUSY`/provider unavailable 503,
  anything else 500. Body: `{"error": {"code": "...", "message": "...", "details": {...}}, "request_id": "..."}`.

Handler registry (type → module):

| type | module | actor |
|---|---|---|
| treasury.init_capital, treasury.deposit, treasury.withdraw, treasury.transfer | `treasury.py` | ui/cli |
| account.open_pb, account.close, account.update_settings | `api.py` (thin handlers) | ui |
| order.submit, order.cancel, order.match, order.expire, order.suspend | `orders.py` | ui/worker/job |
| credit.borrow_stock, credit.return_stock, credit.collateral_mark, credit.loan_draw, credit.loan_repay, credit.repo_open, credit.repo_close, credit.pb_draw, credit.pb_repay | `products/credit.py` | ui/job |
| fx.spot, fx.forward | `products/fx.py` | ui |
| options.exercise, options.expire, options.margin_mark | `products/options.py` | ui/job |
| futures.vm, futures.expire, futures.margin_mark | `products/futures.py` | job |
| lifecycle.settle, lifecycle.accrue, lifecycle.interest_pay, lifecycle.dividend, lifecycle.coupon, lifecycle.sweep_out, lifecycle.eod_snapshot, lifecycle.day_order_expiry | `lifecycle.py` | job |
| quotes.upsert_batch, marks.record, rates.record, instruments.upsert, watchlist.add, watchlist.remove, settings.set | `quotes.py` / `instruments.py` / `api.py` | worker/ui (light) |

---

## 5. Accounting read models (`accounting.py`) — exact definitions

All functions read `gl_balances` (fast path) and `positions`/`lots`/`obligations`/`orders`; `integrity()` is what
guarantees the fast path equals the journal. Figures are `Decimal`; every figure that depends on a mark carries
`quality` and the API serialises `{"value": "123.45", "quality": "live", "as_of": "..."}` for marks-dependent numbers.

### 5.1 Cash (per account, per currency) — `cash()`

| name | definition |
|---|---|
| settled | balance(1010) |
| restricted | balance(1030) |
| pending_in | balance(1200) + balance(1250) (receivables not yet due or due today and not yet settled) |
| pending_out | balance(2100) + balance(2150) |
| projected | settled + pending_in − pending_out |
| margin_deposit | balance(1300) |
| collateral_posted | balance(1400) + balance(1410) |
| committed | Σ over WORKING/PARTIAL buy-side orders in this account/currency of `estimated_cost(remaining)` (limit price, else last ask/last, + fee) + Σ working SELL_SHORT orders' collateral need (1.02 × remaining × last) − already-posted collateral for them (0) |
| spendable (= "buying power", cash) | settled + Σ pending_in with due_date ≤ **new settle date** − pending_out − committed |
| available_from_treasury | Treasury settled cash in that currency − Treasury committed − Treasury buffer (0) |
| buying_power_total | spendable + available_from_treasury (+ PB financing headroom if `pb_financing`, §10.6) |

`buying_power(conn, account, ccy, settle_date, marks)` returns `spendable` as defined for an order settling on
`settle_date`. The UI shows **Buying power** = `buying_power_total` with a tooltip splitting own cash / Treasury sweep /
PB financing. Order of funding at fill time (§9.5): own spendable → Treasury sweep-in of exactly the shortfall → PB
financing → refuse.

### 5.2 Positions, lots, cost basis — `positions()`, `lots()`

- `qty` = `positions.qty_u` (signed). Integrity: equals Σ `fills.qty_u` for (account, instrument) and equals
  Σ `lots.remaining_u` × (+1 long / −1 short).
- `basis` (native) = `positions.basis_u`: long = Σ remaining lots' `price × qty × multiplier` (gross, **excluding
  commission**; commissions are expensed at trade); short = Σ remaining lots' gross proceeds. Convention shown on
  screen: "Cost basis is gross; commissions are in Fees."
- `avg_px` = basis / (|qty| × multiplier). Bonds: price per 100, multiplier `0.01` (quantity = face).
- Lot relief **FIFO** by `open_ts` then `id`, within the same side. A fill that crosses zero (long 300, sell 500)
  first closes the 300 long lots, then opens a 200 SHORT lot (allowed only if a borrow covers 200; otherwise the order
  is rejected at validation, §9.3).
- `market_value` (native) = qty × mark.price × multiplier (bonds: qty × mark/100; negative for shorts). `mv_usd` =
  market_value × fx(ccy).
- `unrealised` (native) = long: mv − basis; short: basis − |mv|. Options and futures: futures `unrealised` =
  (mark − settle_px) × qty × multiplier (only the part not yet settled as VM).
- `realised_to_date` (native, per position) = natural balance of 4000 + 4100 for that instrument.
- `income_to_date` = 4200 + 4300 + 4320 for the instrument; `financing_to_date` = −(5200 + 5210 + 5300) for the
  instrument (per-instrument codes only).
- `day_pnl` (per position) = mv_now − mv at last EOD snapshot (from `nav_snapshots.detail`) − net cash flows of the
  position since (fills' gross+fees, VM, dividends received) — shown as "today (observed)".
- `days_held` = calendar days since the oldest open lot.

### 5.3 NAV, equity, balance sheet — `nav()`, `balance_sheet()`

For an account A (or consolidated = Σ accounts; consolidated eliminates 3100 and 3900 which net to zero anyway):

```
assets_usd      = Σ_ccy fx(ccy) × [ 1010 + 1030 + 1150 + 1160 + 1200 + 1210 + 1250 + 1300 + 1400 + 1410 + 1500 ]_ccy
                + Σ_positions(long) mv_usd                                     (replaces 1100 at cost)
liabilities_usd = Σ_ccy fx(ccy) × [ 2100 + 2150 + 2300 + 2310 + 2320 + 2330 + 2340 + 2400 + 2500 + 2600 + 2700 ]_ccy
                + Σ_positions(short) |mv_usd|                                  (replaces 2200/2210 at proceeds)
NAV = assets_usd − liabilities_usd                     ("Account equity" in the UI = NAV of the account)
```

`balance_sheet()` rows (natural sign, USD and native): Cash settled by currency; Restricted cash; Receivables (unsettled
sales, FX, dividends); Accrued income; Securities at market (long) with "at cost" and "unrealised" sub-rows; Options
purchased at market; Futures margin deposit; Futures open P&L not yet settled (= futures `unrealised`, shown under
assets if positive else liabilities); Collateral posted; Reverse repo; — Payables (unsettled purchases, FX); Securities
sold short at market; Options written at market; Accrued expenses; Collateral received; Repo; Currency loans; PB margin
loan; — NAV; and a memo block Capital contributed, Treasury advances, FX clearing (base 0), Retained results
(= NAV − capital − advances) so the user sees `NAV = capital + advances + retained` tie.

Quality: `nav.quality` = worst mark quality among positions with non-zero quantity (`live` > `delayed` > `close` >
`computed` > `stale` > `unavailable`). When any position is `unavailable`, NAV is returned with
`partial=True, missing=[instrument_ids]` and the UI shows "NAV excludes N unpriced positions".

### 5.4 Realised, income, financing, fees — ledger sums per period

`pnl(period)` buckets from `journal_lines` with `trade_date` in the period (native, translated at the line's
`fx_rate`): realised (4000, 4100), dividends (4200), interest income (4300, 4310, 4320, 4330), financing cost
(5100…5300), fees (5000). Period boundaries are **accounting days** (§5.7).

### 5.5 Unrealised and FX result

```
unrealised_usd(now) = Σ_positions (mv_native − basis_native) × fx(ccy)           # price effect only, at today's rate
fx_result_usd(now)  = Σ_{ccy ≠ USD} [ Σ_{L ∈ AL(ccy)} amount(L) × fx(ccy)  −  Σ_{L ∈ AL(ccy)} base_amount(L) ]
   AL(ccy) = every journal line in ccy whose GL kind is ASSET or LIABILITY (cash, receivables, payables, loans,
             collateral AND the position cost accounts 1100/2200/2210); EQUITY (3000/3100/3900), INCOME and EXPENSE
             lines are excluded. Lines of accounts whose balance is now zero still count (their residual carrying base
             is the FX effect realised while they were open).
```
`fx_result` is the total currency effect to date — the dollars every foreign asset and liability is worth today minus
the dollars it was worth when posted. It is deliberately not split into realised/unrealised (convention shown on
screen: "FX result = foreign balances at today's rate minus the dollars they were worth when booked"). Why this
exact form: each entry balances in base (§3.2), so Σ base(assets − liabilities) = Σ base(capital + transfers) +
Σ base(income − expense); therefore `NAV = capital + transfers + Σ(realised + income − financing − fees at posting
rates) + unrealised + fx_result` holds to the cent, and the period explain in §5.6 has zero residual by construction.
Because 3900 nets to zero in base it drops out; the spread paid on an FX deal shows up inside `fx_result` on the
deal date (scenario 6: −20.00).

### 5.6 P&L periods — `pnl(account, period)`

```
NAV_t     = nav(now)
NAV_0     = nav_snapshots.nav_u at the last accounting-day close before the period start (today: yesterday's 17:00 ET snapshot; MTD: last snapshot of the previous month; YTD: last snapshot of the previous year; inception: 0)
flows     = Σ capital deposits − withdrawals (Treasury) and ± internal transfers (per account) in (period start, now]   (3000 and 3100 movements)
P&L(period) = NAV_t − NAV_0 − flows
explained   = Δrealised + Δincome − Δfinancing − Δfees + Δunrealised + Δfx_result      (each Δ vs the snapshot's stored cumulative)
residual    = P&L − explained   → must be 0.00 (integrity check "pnl_explains"); shown if not
```
Consolidated P&L uses consolidated snapshots (account_id = `ALL` stored alongside) and only capital flows.

### 5.7 The accounting day and snapshots

**Decision:** the accounting day closes at **17:00 ET** (after the US close and at the CME daily break). The EOD job
(`eod:{date}`, 17:05 ET, catch-up if missed) records `marks` (kind CLOSE/SETTLE) for every held instrument from the
quote cache (quality as observed: an equity quote with `quote_time ≥ 16:00 ET` of that day is `close`; a futures
quote observed between 16:00 and 17:05 is `SETTLE` labelled "observed, not the official exchange settlement"; a stale
quote is recorded with quality `stale` and the previous valid mark is carried with a note) and writes `nav_snapshots`
for every account and `ALL`. "Today" P&L before 17:00 is relative to yesterday's snapshot; after 17:05 it is relative
to today's. Weekends/holidays: the EOD job still runs (accruals change NAV) and snapshots carry the last valid marks
labelled `close`.

### 5.8 Treasury — `treasury()`

```
cash_by_ccy        = Treasury 1010 per currency (+ accrued interest 1160)
capital            = −balance(TREASURY, 3000)                       (natural sign)
advances_by_account = for each trading account A: −balance(A, 3100) (= net cash advanced to A; equals +balance(TREASURY,3100) in total)
retained_results   = Treasury NAV − capital + Σ advances         (Treasury's own income: cash interest)
available          = Treasury settled USD − Treasury committed (0) − reserve (settings.treasury_reserve, default 0)
identity (integrity "treasury_identity"): Treasury NAV == capital + retained_results − Σ advances
encumbrance        = 0 in this build (Treasury holds no positions); shown as such
```

### 5.9 `integrity()` — the checks, in order (all must pass; the report lists every failure)

| code | check (SQL unless noted) |
|---|---|
| entries_balance | for every (entry_id, currency): Σ amount_u = 0 (per-currency balance) |
| entries_balance_base | for every entry_id: Σ base_amount_u = 0 |
| lines_valid | every line's gl_code in chart, account in accounts, currency 3 letters, per_instrument codes have instrument_id, amount_u ≠ 0 |
| gl_balances_match | for every (account, gl, ccy, instr): gl_balances.balance_u == Σ journal_lines.amount_u (and base_u == Σ base_amount_u); no gl_balances row without lines; scope-limited when `scope` is given |
| ledger_identity | per currency: Σ ASSET − Σ LIABILITY − Σ EQUITY − Σ INCOME + Σ EXPENSE == 0 (natural signs) — holds by construction; catches projection corruption |
| transfers_net_zero | Σ over accounts of balance(3100) == 0 per currency; Σ base_u of 3900 over all accounts == 0 |
| capital_explained | −Σ balance(TREASURY,3000,USD) == Σ amounts of COMMITTED commands of type treasury.init_capital/deposit (−withdraw) |
| treasury_identity | §5.8 identity to the cent (uses marks for Treasury NAV; Treasury has no positions so marks only enter via fx of foreign cash) |
| positions_match_fills | per (account, instrument): positions.qty_u == Σ fills.qty_u |
| positions_match_lots | positions.qty_u == Σ lots.remaining_u × sign; positions.basis_u == Σ lots remaining basis (remaining_u/open_qty_u × basis_u, exact by construction since lots store price and basis_u = price×qty×mult rounded once; integrity recomputes `cents(remaining × price × mult)` and compares) |
| cost_accounts_match | per (account, instrument): balance(1100) (long) == positions.basis_u for long positions; balance(2200)+balance(2210) == basis_u for shorts; zero for flat positions |
| obligations_match | every TRADE fill has exactly one obligation; PENDING obligations' Σ amount_u per (account, gl_pending, ccy) == balance of that pending GL; SETTLED obligations have entry_id whose posting_key = 'settle:obl:{id}' |
| no_duplicate_keys | (schema-enforced; re-verified) counts of distinct keys == row counts for fills, obligations, job_runs |
| shorts_borrowed | every position with qty < 0 in a shortable cash instrument (EQUITY/ETF/ADR/REIT/PREFERRED/BOND) has Σ OPEN borrows.qty_u ≥ |qty|; every OPEN borrow has a short position or `reason` ('cover pending settlement' is set by the cover handler and cleared on return) |
| collateral_match | per OPEN borrow: balance(1400, instrument) == borrows.collateral_u |
| loans_match | Σ principal of OPEN loans by kind/ccy == balances of 2600 / 2700 / 2500 / 1500; accrued_u == 2340/2300/2330 balances |
| orders_consistent | orders.filled_u == Σ fills.qty_u (abs) for the order; FILLED ⇒ filled_u == qty_u; WORKING/PARTIAL ⇒ filled_u < qty_u |
| pnl_explains | (only in full mode, needs marks) residual of §5.6 for `today` == 0 for every account |
| snapshot_chain | (full mode) every account has a snapshot for every accounting day since inception (gaps reported, not fatal: WARN) |

Scope: after every command the bus runs the check restricted to `touched_accounts` (ms); on startup and every hour the
lifecycle worker runs the full check (marks included) and records the result in `attention` when it fails and in
`health.integrity`. Any failure after a command rolls the command back (§4); a failure found by the hourly check is
shown in the UI banner "Integrity check failed: …" and blocks new orders until resolved (reads continue).

---

## 6. Instrument master and symbol mapping

- Seeded from `finsim/finsim/data/universe.json` at first start: 827 equity-like rows (`ticker, name, asset_class,
  sector, country, dividend_yield, shares_outstanding, beta, cik, adv`), 4 rates (`^IRX ^FVX ^TNX ^TYX`), 25 FX, 23
  commodities (continuous `=F` symbols, used as reference marks for the per-contract futures), 7 indices. Bonds from
  the universe are **not** seeded as tradable: the terminal's bond set is the curated table in `products/bonds.py`
  (on-the-run UST bills/notes/bonds priced from the par curve; 20 curated IG corporates priced curve + OAS).
- Unknown tickers typed into the search box: `instruments.resolve` fetches Yahoo chart meta (`instrumentType`,
  `currency`, `exchangeName`, `fullExchangeName`, `exchangeTimezoneName`, `currentTradingPeriod`) and upserts an
  instrument with `product_group = US_EQUITY` for US exchanges (NMS, NYQ, PCX, NGM, ASE, BTS), else `FOREIGN` with the
  session taken from the quote's `currentTradingPeriod`, `settle_days` from the venue table (TSX 1, LSE 2, XETRA 2,
  PAR 2, TSE 2, HKG 2, ASX 2, default 2), and `support = LIVE`.
- Yahoo symbol rules: equities = ticker with Yahoo's hyphen for classes (`BRK-B`), preferreds `BAC-PL`; FX
  `FX:EURUSD → EURUSD=X` (USD per EUR), `FX:USDJPY → JPY=X` (JPY per USD, so fx(JPY) = 1/last), rates `^IRX`…, index
  `^VIX`, `^GSPC`, futures per contract `ESZ26.CME`, `NQZ26.CME`, `YMZ26.CBT`, `RTYZ26.CME`, `CLZ26.NYM`, `BZZ26.NYM`,
  `NGZ26.NYM`, `GCZ26.CMX`, `SIZ26.CMX`, `HGZ26.CMX`, `ZBZ26.CBT`, `ZNZ26.CBT`, `ZFZ26.CBT`, `ZTZ26.CBT`, `6EZ26.CME`,
  `6JZ26.CME`, `6BZ26.CME`, `ZCZ26.CBT`, `ZWZ26.CBT`, `ZSZ26.CBT` (continuous fallback `ES=F` etc. labelled
  "front-month continuous (reference)"). **Assumption:** these per-contract suffixes follow the probe's `CLX26.NYM`
  pattern; WP1 verifies each root once and records the working form in `instruments.spec.yahoo_pattern`.
- Cboe symbol: US equities/ETFs = ticker with `.` for `-` (`BRK.B`); indices `_SPX`, `_NDX`, `_RUT`, `_VIX`, `_DJX`.
  Options: `O:{UNDERLYING}{YYMMDD}{C|P}{strike×1000, 8 digits}` built from Cboe's `option` field (OCC), e.g.
  `O:SPY261120C00760000`; `underlying_id = SPY`, `multiplier 100`, `tick 0.01`, `product_group US_EQUITY_OPTIONS`
  (`US_INDEX_OPTIONS` for `_SPX`-style, cash-settled, European), `settle_days 1`.
- Futures: `F:{ROOT}{MONTHCODE}{YY}`; §10.3 table gives multiplier, tick, currency, group, margins, expiry rule, Yahoo
  exchange suffix.
- `support`: `LIVE` (Yahoo/Cboe quote), `LIMITED` (dividend pay dates by convention, foreign holidays unknown),
  `COMPUTED` (bonds, FX forwards, option model fallback), `UNSUPPORTED` (everything in TERMINAL_DECISIONS §5; stored so
  the UI can show "unsupported in this build" when searched).

---

## 7. Quote cache record and freshness

`Quote` (cache + `quotes` table): `instrument_id, source ('yahoo'|'cboe'|'treasury'|'fred'|'frankfurter'|'computed'),
quote_time (provider timestamp: Yahoo regularMarketTime; Cboe last_trade_time (ET, naive → ET-localised); FRED/Treasury
the observation date at 17:00 ET), fetched_at, last, bid, ask, bid_size, ask_size, prev_close, day_volume, bar_time,
bar_volume (last COMPLETED 1-minute bar: the last bar with t ≤ fetched_at − 60 s), bar_close, currency,
delay_minutes (Yahoo equities/ETFs/FX/futures/indices: 15 (conservative label "up to 15 min delayed"); Cboe: 15;
rates/curve: 1 business day), iv, greeks (Cboe JSON for options), error`.

### 7.1 Freshness state (`QuoteCache.quality(q, session, now)`)

```
if q is None or q.last is None:                                  → 'unavailable'
if q.source == 'computed':                                       → 'computed'
age_provider = now − q.quote_time ;  age_fetch = now − q.fetched_at
if session.state == 'OPEN':
    if age_provider > delay + grace(5 min) or age_fetch > 5 min: → 'stale'
    else:                                                        → 'live' if source == 'yahoo' else 'delayed'
else  (CLOSED / BREAK / HOLIDAY):
    if q.quote_time >= session.last_close − 1 min (or session not yet opened today and quote is from the previous session close): → 'close'
    else:                                                        → 'close' with note 'last valid mark {quote_time}'  (never invented)
```
Executions (`order.match`, market/limit) require `quality in {'live','delayed'}`. Valuation uses any quality but shows
it. A `stale` quote during the session is refetched with priority; if the provider keeps failing the health strip says
so ("Yahoo: 5 consecutive errors, backing off 60 s").

### 7.2 Workers and pacing

- `QuoteService` runs 2 fetch threads over a priority queue: `orders` subscriptions every 15 s while their session is
  open; `held` and `watch` every 60 s in session, every 5 min out of session; `page` subscriptions (what the UI is
  looking at) every 30 s with a 5-minute TTL after the last poll. Yahoo: one chart request per instrument
  (`range=1d&interval=1m`, ~35–110 KB; the probe shows 0.1–0.3 s), paced ≥ 0.25 s apart, hosts query1→query2, retries
  on 429/5xx with 1.5 s doubling, 3 per host, circuit breaker after 5 consecutive failures (60 s). Memory: parse,
  keep the meta + last 3 bars, drop the payload.
- Cboe chain: on demand only (`chain(underlying)`), one at a time, TTL 90 s, parsed into a reduced structure (per
  contract: bid, ask, sizes, iv, delta, gamma, theta, vega, oi, volume, last, last_trade_time); the raw 5–13 MB
  payload is discarded immediately; at most 2 chains cached (LRU).
- Treasury par curve: once per day at 17:30 ET and on demand if missing (16 s, in a worker). FRED: SOFR, DGS1MO…DGS30,
  ECBDFR, SONIA (`IUDSOIA`), BAMLC0A0CM, BAMLH0A0HYM2 daily at 18:00 ET and at startup if stale > 1 day. FINRA daily
  short volume at 19:00 ET (previous day). Frankfurter: only when a Yahoo FX quote is `unavailable` (daily rate,
  quality `close`).
- UA policy (`http.py`): `.gov` and `cdn.finra.org` → `LoganTerminal/1.0 (loganshaffer87@gmail.com)`; Yahoo →
  browser UA; Cboe and Frankfurter → `FinSimTerminal/1.0 (paper trading; python stdlib)`. SEC ≤ 10 req/s (only
  `company_tickers.json` weekly; optional). Never a crumb; never Nasdaq Trader; never Stooq.
- Persisted every 15 s through `quotes.upsert_batch` (light command) so a restart starts from the last marks.

---

## 8. Sessions, holidays, early closes (`sessions.py`)

| group | regular session (ET) | breaks | holidays | early close | DAY expiry |
|---|---|---|---|---|---|
| US_EQUITY | Mon–Fri 09:30–16:00 | — | NYSE list | 13:00 on listed days | session close |
| US_EQUITY_OPTIONS | 09:30–16:00 | — | NYSE | 13:00 | session close |
| US_INDEX_OPTIONS | 09:30–16:15 | — | NYSE | 13:15 | session close |
| US_BONDS | Mon–Fri 08:00–17:00 | — | NYSE (**Assumption**: SIFMA calendar approximated by NYSE) | 14:00 on NYSE early-close days | 17:00 |
| CME_EQUITY_INDEX (ES NQ YM RTY) | Sun 18:00 → Fri 17:00 | daily 17:00–18:00 | NYSE full holidays: closed from 17:00 the day before to 18:00 of the holiday (**Assumption**: simplified) | 13:00 on NYSE early-close days | 17:00 of the trade date |
| CME_RATES (ZB ZN ZF ZT) | Sun 18:00 → Fri 17:00 | 17:00–18:00 | same | 13:00 | 17:00 |
| CME_ENERGY_METALS (CL BZ NG GC SI HG) | Sun 18:00 → Fri 17:00 | 17:00–18:00 | same | — | 17:00 |
| CME_AGS (ZC ZW ZS) | Sun 20:00–Fri 14:20: nightly 20:00–08:45, day 09:30–14:20 | 08:45–09:30, 14:20–20:00 | same | — | 14:20 |
| CME_FX (6E 6J 6B) | Sun 18:00 → Fri 17:00 | 17:00–18:00 | same | — | 17:00 |
| FX_SPOT | Sun 17:00 → Fri 17:00 continuous | — | none (**Assumption**: 1 Jan and 25 Dec treated as closed) | — | 17:00 |
| FOREIGN | from Yahoo `currentTradingPeriod.regular` in `exchangeTimezoneName` | — | unknown: a day with no fresh quote during its period is simply `stale` → blocked | — | that session's end |

`day_order_expiry(group, now)` = the close of the current session if open, else the close of the next session.
`trade_date` for CME groups after 18:00 ET = next business day. The calendar file is `data/nyse-holidays.json` (repo
root); years missing from the file fall back to `finsim.calendar.us_equity_holidays` and the fallback is reported in
`/api/health.calendar`.

---

## 9. Orders: validation, preview, matching

### 9.1 Order request

```json
{"request_id":"ui:…","account_id":"US_EXCHANGE","instrument_id":"AAPL","side":"BUY","qty":"1000",
 "order_type":"LIMIT","limit_px":"339.50","stop_px":null,"tif":"DAY",
 "hedge":{"kind":"PROTECTIVE_PUT","option_id":"O:SPY270115P00715000","ratio":"0.01"},
 "preview_token":"<sha of the preview the user saw>"}
```
Sides: cash instruments BUY / SELL / SELL_SHORT / BUY_TO_COVER; options BUY_TO_OPEN / SELL_TO_OPEN / BUY_TO_CLOSE /
SELL_TO_CLOSE; futures BUY / SELL. The server derives the position effect; a SELL larger than the long position is
rejected ("use SELL_SHORT for the uncovered 200 and borrow first").

### 9.2 Preview (`orders.preview`, no writes)

1. Resolve instrument; `support` must be LIVE/LIMITED/COMPUTED-with-execution (bonds, FX) else 400 `UNSUPPORTED`.
2. `quotes.refresh_now([instrument, underlying, fx])` with a 3 s bound; compute `quality`; for market orders a
   non-fresh quote makes the preview `executable=false` with the reason (the Submit button is disabled; a limit order
   may still be submitted and rests).
3. Session state and the resulting `expected_execution`: "now at ask 339.50 (Cboe, delayed 15 min)" / "rests until
   Wed 09:30 ET" / "DAY order will expire 16:00 ET".
4. Price basis: BUY market → Cboe ask if `delayed`-fresh else Yahoo last; SELL → bid else last; limit → min(limit,
   executable) for buys, max for sells; stop → shows trigger and basis.
5. Quantity cap now (participation × last completed bar volume) → "fills up to 12,000 now; the remainder keeps
   working". Options: cap = Cboe ask_size/bid_size. No volume data → full, disclosed.
6. Fees (§10.1); gross; accrued (bonds); total; settlement date (`cal.settlement_date(trade_date, settle_days)`).
7. Funding: `spendable` vs needed → sweep-in amount from Treasury, PB financing amount, or `REFUSED: insufficient
   funds (needs 2,846,625.00; own cash 1,000,000.00; Treasury available 500,000.00)`.
8. Shorts: locate result (`products/credit.locate`): tier (GC/WARM/HTB from the FINRA short-volume ratio: > 50 % HTB
   fee 5.00 %, 40–50 % WARM 1.50 %, else GC 0.30 %; names with no FINRA row: GC with note), collateral 102 % × qty ×
   last, fee/rebate rates, "borrow executes first; the sale is rejected if the borrow fails".
9. Margin for short options / futures (§10.3, §10.4) and the 1300 movement it causes.
10. Hedge candidates (§11) if the ticket asked (`hedge_candidates=true`).
11. Compliance notices and risk warnings (§12).
12. Returns the preview + `preview_token` = sha256 of (instrument, side, qty, type, prices, fee, quote_time); `submit`
    re-validates and, if the executable price moved by more than 0.5 % against the user since the preview, returns
    409 `PRICE_MOVED` with a fresh preview (market orders only).

### 9.3 Validation (`orders.validate`) — what blocks an order (400 unless noted)

| code | rule |
|---|---|
| UNKNOWN_INSTRUMENT / UNSUPPORTED | not in master or `support=UNSUPPORTED` |
| ACCOUNT_MISMATCH | instrument's product group not allowed in this account (US_EXCHANGE: US_EQUITY, options, CME groups; DEBT: US_BONDS; CREDIT: loans/repo/borrows; FOREIGN_FX: FOREIGN, FX_SPOT; PB_*: everything US_EXCHANGE allows + FX + OTC) |
| BAD_QTY | qty ≤ 0, not a multiple of lot_size (bonds 1,000 face; options/futures integers) |
| BAD_PRICE | limit/stop not on tick; stop on the wrong side of the market |
| (session closed) | **not a block.** Every order type may rest outside the session; only *execution* needs an open session (§9.4 step 1). The preview states when the order can first trade and when a DAY order would expire |
| STALE_QUOTE (409) | market/stop orders: quote not `live/delayed` at submission (limit orders may rest) |
| INSUFFICIENT_FUNDS | needed > spendable + Treasury available (+ PB headroom) |
| NO_BORROW | SELL_SHORT and locate says unavailable (HTB names: available unless the FINRA ratio > 70 %, disclosed heuristic) |
| OVERSELL | SELL > long qty (net of working sells) |
| EXPIRED_INSTRUMENT | option on/after expiry 16:00 ET; future on/after last trade; bond matured |
| LAST_SESSION | future in its last trading session (expiry date) |
| CLOSE_ONLY_MISMATCH | BUY_TO_COVER/SELL_TO_CLOSE larger than the position |
| INTEGRITY_HOLD (409) | the hourly integrity check failed and is unresolved |

Risk limits never block (they warn, §12).

### 9.4 Matching (`order.match`) — one pass per order per new quote

Trigger: the `Matcher` thread wakes when the cache receives a quote whose `quote_time` is newer than
`orders.last_match_quote_time` for a working order on that instrument (and every 15 s as a sweep). It builds
`Command("order.match", {order_id, quote: asdict(q)}, request_id=f"match:{order_id}:{quote_time_epoch}")`. Same id
twice → replay → no double fill.

Rules inside the handler (all against the quote snapshot in the payload, re-checked for freshness with `ctx.now`):
1. Session must be OPEN for the instrument's group; else return `{"skipped":"session closed"}`.
2. `quality(q)` must be `live`/`delayed`; else skip (shown as "waiting for a fresh quote").
3. Trigger evaluation: STOP/STOP_LIMIT not yet triggered → trigger if `last ≥ stop` (buy) / `last ≤ stop` (sell);
   set `triggered=1` (event ORDER_TRIGGERED) and continue in the same pass.
4. Executable price:
   - MARKET or triggered STOP: buy = ask (if Cboe fresh) else last; sell = bid else last.
   - LIMIT (marketable or resting): buy fills when `ask (or last) ≤ limit` at **min(limit, ask/last)**; sell when
     `bid (or last) ≥ limit` at **max(limit, bid/last)**. Resting limit crossed by the observed last trades at the limit
     (conservative, disclosed).
   - STOP_LIMIT after trigger: as LIMIT.
   - Futures: round to tick. Options: Cboe bid/ask required (`delayed` fresh); a Cboe outage makes options
     `unavailable` → no fills, disclosed (no model fills).
5. Quantity this pass = `min(remaining, cap)`; cap = `floor(participation × bar_volume)` (≥ lot_size) when
   `bar_volume` known and the bar is from this session; options: `ask_size`/`bid_size` contracts; no data → remaining.
   If cap = 0 → skip ("no observed volume in the last minute").
6. Funding (§9.5) for the fill's cash need; shorts need the borrow to exist (the submit handler already opened it;
   if the borrow is smaller than the fill — partial borrow — the fill is scaled to the borrowed quantity).
7. `booking.book_fill` → fill row (`fill:{order_id}:{quote_time_epoch}`), lots, position, journal entry, obligation;
   order `filled_u`, `avg_px`, status PARTIAL/FILLED; events ORDER_PARTIAL/ORDER_FILLED; `last_match_quote_time`.
8. Hedge children (§11): on a primary fill, create/resize the child order(s) in the same transaction.

DAY orders: `lifecycle.day_order_expiry` at the session close (`expire:{order_id}:{date}`); GTC: `expires_ts` =
submitted + 90 days. Cancel: allowed in WORKING/PARTIAL/SUSPENDED; releases nothing (nothing was reserved) except an
unfilled short's borrow, which is returned in the same command (collateral comes back, fee accrued to the minute is
charged).

### 9.5 Sweep-in (`orders.sweep_in`) and order of funding

At fill time, for the fill's currency: `needed = gross + fee (+ accrued) + collateral/margin moved by this fill`;
`short = needed − spendable(account, ccy, settle_date)`; if `short > 0`:
1. Treasury settled cash in `ccy` ≥ short → post `TRANSFER` entry (posting_key `sweep:{fill_key}`): Treasury Dr 3100 /
   Cr 1010; account Dr 1010 / Cr 3100; event SWEEP_IN linked to the order. For foreign currency with no Treasury cash in
   that currency: refuse with "convert first" (the preview offered an FX spot leg; the user accepts it and the ticket
   submits `fx.spot` then the order as two commands with the FX as a prerequisite; **Decision:** no automatic FX).
2. PB account with `pb_financing=1`: draw `short` on 2700 (`credit.pb_draw` inline) up to the PB headroom (§10.6).
3. Else raise `RejectError INSUFFICIENT_FUNDS` → the order is **SUSPENDED** with the reason (resting orders) or
   rejected (new orders); a suspended order resumes when the user adds funds (`order.resume`) — never silently.

Sweep-out (`lifecycle.sweep_out`, `sweep_out:{acct}:{date}` at 16:30 ET, per account with `sweep_out=1`): idle =
settled − pending_out (all) − committed − margin headroom reserve (futures maintenance shortfall, 0 if none) −
collateral calls expected (0) − buffer; if idle > 0 → transfer idle to Treasury (account Dr 3100 / Cr 1010; Treasury
Dr 1010 / Cr 3100), event SWEEP_OUT.

---

## 10. Per-product rules, lifecycle jobs, fees and rates

### 10.1 Fee schedules (`fees.SCHEDULES`)

| schedule | equities | options | futures | FX | bonds | PB financing | borrow |
|---|---|---|---|---|---|---|---|
| DEFAULT (exchange accounts) | USD 0.005/share, min 1.00 | 0.65/contract | 2.25/contract/side | 0.2 pip (0.00002) spread cost shown, not a fee line | 0.02 per 100 face, min 5.00 | n/a | GC 0.30 % / WARM 1.50 % / HTB 5.00 % |
| PB_GS, PB_JPM, PB_MS | 0.003/share min 1.00 | 0.50 | 2.00 | 0.1 pip | 0.015/100 min 5 | SOFR + 1.25 % | GC 0.25 % |
| PB_DB, PB_BARC, PB_CITI, PB_UBS, PB_BNP | 0.004/share min 1.00 | 0.60 | 2.25 | 0.15 pip | 0.02/100 min 5 | SOFR + 1.50 % | GC 0.30 % |
Shown in each PB's "relationship" panel and in every preview. Fees post to 5000 with the instrument.

### 10.2 Rates (`fees.RATES`, from `rates` table)

| use | rate | day count | source |
|---|---|---|---|
| Treasury idle USD cash | SOFR − 0.50 % (floor 0) | ACT/360 | FRED SOFR (latest ≤ date) |
| PB margin financing | SOFR + schedule spread | ACT/360 | FRED |
| Stock borrow fee | tier rate on collateral value | ACT/360 | FINRA tier heuristic |
| Cash collateral rebate | max(0, SOFR − 0.25 %) for GC/WARM, 0 for HTB | ACT/360 | FRED |
| Repo (borrow cash) | SOFR + 0.15 %; haircut UST 2 %, IG corp 5 %, equities 15 % | ACT/360 | FRED |
| Reverse repo (lend cash) | SOFR − 0.05 % | ACT/360 | FRED |
| Currency loans | policy + 0.75 %: USD SOFR; EUR ECBDFR; GBP SONIA (ACT/365); JPY BoJ policy (FRED `IRSTCI01JPM156N`, monthly, labelled); CHF/CAD/AUD OECD monthly (labelled) | ACT/360 (GBP ACT/365) | FRED |
Every accrual stores the rate used and its observation date in the entry memo (`"SOFR 3.87 % (2026-10-19)"`).

Daily accrual convention (all accruals): for each calendar day `d` on which the balance was open, the amount for `d`
("the night of d") is posted by the first lifecycle tick at or after `d+1 00:05 ET`, with posting key
`accr:{kind}:{ref}:{d}`, on the principal/collateral **as it stands after day d's 17:05 ET marks** (so a collateral call
posted on Monday evening accrues from Monday night). Catch-up posts every missed day in order. Rounding: cumulative
method — `accrued_through(d) = cents(principal × rate × n_days / basis)` counted from the last principal change; the
posted amount for `d` is the difference from the previous cumulative (so the sum never drifts from the hand-worked
total). When the principal changes (collateral mark, partial repayment) the cumulative restarts from the change date
(stored `last_accrual_date` + `accrued_u`).

### 10.3 Futures (`products/futures.py`)

Contract table (`FUTURES_SPECS`; margins are a static table labelled "verify current exchange margins"):

| root | name | mult | tick | ccy | group | IM / MM (USD) | expiry rule | Yahoo suffix |
|---|---|---|---|---|---|---|---|---|
| ES | E-mini S&P 500 | 50 | 0.25 | USD | CME_EQUITY_INDEX | 22,000 / 20,000 | 3rd Fri of Mar/Jun/Sep/Dec, 09:30 ET cash | .CME |
| NQ | E-mini Nasdaq-100 | 20 | 0.25 | USD | CME_EQUITY_INDEX | 30,000 / 27,000 | same | .CME |
| YM | E-mini Dow | 5 | 1 | USD | CME_EQUITY_INDEX | 12,000 / 11,000 | same | .CBT |
| RTY | E-mini Russell 2000 | 50 | 0.10 | USD | CME_EQUITY_INDEX | 9,500 / 8,600 | same | .CME |
| CL | WTI crude | 1,000 | 0.01 | USD | CME_ENERGY_METALS | 7,000 / 6,300 | 3 business days before the 25th of the month before delivery; cash-settled in this build at the last observed settle | .NYM |
| BZ | Brent | 1,000 | 0.01 | USD | CME_ENERGY_METALS | 7,000 / 6,300 | last business day of the 2nd month before delivery | .NYM |
| NG | Henry Hub | 10,000 | 0.001 | USD | CME_ENERGY_METALS | 4,500 / 4,100 | 3 business days before the first day of delivery month | .NYM |
| GC | Gold | 100 | 0.10 | USD | CME_ENERGY_METALS | 12,000 / 10,900 | 3rd last business day of the delivery month | .CMX |
| SI | Silver | 5,000 | 0.005 | USD | CME_ENERGY_METALS | 14,000 / 12,700 | same | .CMX |
| HG | Copper | 25,000 | 0.0005 | USD | CME_ENERGY_METALS | 8,000 / 7,300 | same | .CMX |
| ZB | 30-year T-bond | 1,000 | 1/32 (0.03125) | USD | CME_RATES | 5,000 / 4,500 | 7 business days before the last business day of Mar/Jun/Sep/Dec | .CBT |
| ZN | 10-year note | 1,000 | 1/64 | USD | CME_RATES | 2,800 / 2,500 | same | .CBT |
| ZF | 5-year note | 1,000 | 1/128 | USD | CME_RATES | 1,800 / 1,600 | last business day of the month | .CBT |
| ZT | 2-year note | 2,000 | 1/128 | USD | CME_RATES | 1,200 / 1,100 | same | .CBT |
| 6E | Euro FX | 125,000 | 0.00005 | USD | CME_FX | 3,000 / 2,700 | 2nd business day before 3rd Wed of Mar/Jun/Sep/Dec | .CME |
| 6J | Japanese yen | 12,500,000 | 0.0000005 | USD | CME_FX | 3,200 / 2,900 | same | .CME |
| 6B | British pound | 62,500 | 0.0001 | USD | CME_FX | 2,800 / 2,500 | same | .CME |
| ZC | Corn | 5,000 | 0.25 (cents/bu → price in cents; Yahoo quotes cents: yahoo_scale 1) | USD | CME_AGS | 1,800 / 1,600 | business day before the 15th of the contract month | .CBT |
| ZW | Wheat | 5,000 | 0.25 | USD | CME_AGS | 2,500 / 2,300 | same | .CBT |
| ZS | Soybeans | 5,000 | 0.25 | USD | CME_AGS | 3,000 / 2,700 | same | .CBT |
Listed months: the next 4 quarterlies (equity index, rates, FX) / next 6 monthlies (energy, metals, ags). Grains are
quoted in cents per bushel: `price × multiplier / 100` → the spec carries `price_divisor 100` (ZC/ZW/ZS); everything
else divisor 1.

Jobs:
- `futures.vm` (`vm:{acct}:{inst}:{trade_date}`) after 17:05 ET: settle_px = mark SETTLE of the trade date (observed
  at the 17:00 break; `quality` recorded); VM = (settle_px − position.settle_px) × qty × mult / divisor, **plus** for
  fills of the day: (settle_px − fill_px) × fill_qty × mult (the position's `settle_px` is the previous settle for the
  carried quantity; fills booked today carry their own price — implemented by posting VM per lot: carried lots vs
  settle_px, today's lots vs fill price). Entry: VM > 0: Dr 1010 / Cr 4100 (instrument); VM < 0: Dr 4100 / Cr 1010;
  then `positions.settle_px = settle_px`. Missing settle mark → job SKIPPED with Attention "no settlement mark for
  ESZ26 on 2026-10-20 (quote stale since 14:29 ET)"; retried next tick; never invented.
- `futures.margin_mark` (`fmargin:{acct}:{date}`, after VM): required IM = Σ |qty| × IM; `delta = required −
  balance(1300)`; delta > 0 → Dr 1300 / Cr 1010 (sweep-in if needed); delta < 0 → release. Maintenance check: if
  settled cash < 0 after VM → sweep-in; if Treasury cannot → Attention WARN "margin shortfall 12,340.00 not covered"
  (no liquidation).
- `futures.expire` (`fexp:{acct}:{inst}:{expiry}`): at the expiry date's 17:05 ET job: final VM at the last observed
  settle, then a `MATURITY` fill at that price closing the position (qty → 0, basis relief; realised 0 beyond VM);
  release IM. Contract status EXPIRED; no new orders from the expiry date (LAST_SESSION).
- Fees: `fills.fee_u` posted Dr 5000 / Cr 1010 immediately (futures fees settle same day).
- Futures positions: `basis_u` = 0 by convention (P&L flows through VM); `avg_px` kept on the position for display.

### 10.4 Listed options (`products/options.py`)

- Chain from Cboe; contracts upserted on first sight. Marks: Cboe mid when `delayed` fresh; else `computed` BSM/CRR
  from `finsim.engines.options_pricing` with S = underlying mark, σ = last known Cboe IV for the contract (else the
  underlying's iv30), r = ^IRX/100 (or the par curve 3M), q = `dividend_yield` from the master, labelled
  `computed (model, IV 2026-10-20 15:46)`.
- Buy to open: Dr 1100 (option) premium / Dr 5000 fee / Cr 2100 (T+1). Sell to open: Dr 1200 (premium − fee) / Dr
  5000 / Cr 2210 (premium). Close: FIFO relief against 1100/2210 with realised to 4000.
- Margin (`options.margin_mark`, `omargin:{acct}:{date}` and at every option fill): Reg-T style, per underlying,
  USD: naked put `max(premium + 20 % × S − OTM, premium + 10 % × K)`; naked call `max(premium + 20 % × S − OTM,
  premium + 10 % × S)`; vertical spread (same expiry, long covers short) = width × contracts × 100; covered call
  (shares ≥ 100 × contracts in the same account) = 0; cash-secured puts are **not** special (margin as naked, shown).
  Requirement moves cash to 1300 like futures IM (sweep-in applies); released as positions close. Formula shown in
  the preview with "disclosed Reg T-style approximation".
- Expiry (`options.expire`, `oexp:{acct}:{inst}:{expiry}`, runs at 17:05 ET of the expiry date): needs the
  underlying's CLOSE mark of the expiry date (quality `close`; else job SKIPPED + Attention, retried; never invented).
  Intrinsic = max(0, S − K) calls / max(0, K − S) puts.
  - intrinsic < 0.01 → `EXPIRY` fill at 0: long: Dr 4000 realised loss / Cr 1100 (basis); short: Dr 2210 / Cr 4000.
  - ITM ≥ 0.01, physical (equity/ETF options): long call → `EXERCISE`: buy underlying at K (`fill_key
    exercise:{acct}:{inst}:{expiry}`): Dr 1100 (underlying) K × 100 × n / Cr 2100 (T+1 after expiry) and close the
    option at 0 (realised −premium); long put → sell underlying at K (SELL_SHORT if no shares; needs a borrow → if no
    borrow can be opened, the option is cash-settled at intrinsic instead, disclosed as "cash-settled: no borrow
    available"); short call assigned → sell underlying at K (same borrow rule); short put assigned → buy at K.
  - Index options (cash): `EXPIRY` fill at intrinsic: long Dr 1200 intrinsic × 100 × n / Cr 1100 / Cr 4000 (diff);
    short the reverse.
  - Early exercise by user: `options.exercise` {position, qty} any time before expiry at the current underlying mark
    basis = strike (physical only). Early assignment is **not** simulated (TERMINAL_DECISIONS: no invented events);
    shown as a disclosed limitation.
- Exercise cash needs follow the sweep-in order; if nothing can fund it, the option is cash-settled at intrinsic and
  an Attention WARN says so.

### 10.5 Equities/ETFs, settlement, dividends (`products/equities.py`, `lifecycle.settle/dividend`)

- Fill entries (§15 scenarios 3–5). `settle_days` 1 (US). Settlement job `lifecycle.settle`
  (`settle:obl:{id}`) runs when `et_date(now) ≥ due_date` (first tick that day): OUT: Dr 2100 / Cr 1010 (sweep-in if
  settled cash is short — normally the sweep already happened at fill); IN: Dr 1010 (or 1030 for short-sale proceeds)
  / Cr 1200. FX legs: Dr 1010 buy_ccy / Cr 1250 and Dr 2150 / Cr 1010 sell_ccy.
- Dividends (`support=LIMITED`): the daily 18:00 ET job fetches Yahoo `range=1mo&events=div` for held equities;
  for an event with ex-date `d` and a position at the close of `d−1` (from `nav_snapshots.detail`), post on ex-date:
  long: Dr 1210 / Cr 4200 (`div:{acct}:{inst}:{d}`); short: Dr 5210 / Cr 2320. Pay date is not available free:
  **convention** credited/paid `d + 15 business days` (`divpay:…`): Dr 1010 / Cr 1210 (or Dr 2320 / Cr 1010), labelled
  "pay date by convention".
- Corporate actions (splits): detected from Yahoo `events=split`; position qty and lots scaled (`split:{inst}:{d}`),
  basis unchanged; options are NOT adjusted automatically (Attention WARN "manual review: split in SPY affects
  options") — limitation.

### 10.6 Credit: stock borrow, currency loans, repo, PB financing (`products/credit.py`)

- `credit.borrow_stock` {account, instrument, qty, link_order}: locate (FINRA tier; `shortable=0` → NO_BORROW);
  collateral = cents(1.02 × qty × last_mark); entry `COLLATERAL` (`borrow:{request_id}`): Dr 1400 (instrument) /
  Cr 1010 (sweep-in allowed); borrow row OPEN with `reason='pending short sale'` until the linked order fills (cleared
  on fill; if the order is cancelled/expires unfilled the borrow is returned in the same command).
- Daily (`accr:borrow_fee:{borrow_id}:{d}`, `accr:rebate:{borrow_id}:{d}`): fee Dr 5200 / Cr 2310; rebate Dr 1160 /
  Cr 4320; both on `collateral_u` as of the 17:00 boundary of `d`. Settled monthly on the 1st and on return
  (`feepay:{borrow_id}:{YYYY-MM}` / `:return`): Dr 2310 / Cr 1010 and Dr 1010 / Cr 1160.
- Collateral mark (`cmark:{borrow_id}:{d}`, 17:05 ET, uses the CLOSE mark of `d`): required = cents(1.02 × qty ×
  close); delta = required − collateral_u; delta > 0 → Dr 1400 / Cr 1010 (sweep-in); delta < 0 → Dr 1010 / Cr 1400
  (excess returns). One movement per borrow per day (directive −10).
- `credit.return_stock` {borrow_id, qty}: allowed only up to `|short position| − qty` ≥ 0 after return (never leave
  a naked short); collateral returned pro rata; accrued fee/rebate settled; on a full cover the cover handler calls
  this automatically for the covered quantity.
- Recalls: only from observed data: the lifecycle job flags a borrow `RECALLED` when the name enters the FINRA
  ratio > 70 % tier for 3 consecutive days (disclosed heuristic) → Attention WARN "recall notice: cover within 3
  business days"; on day 3 without a cover a `BUY_IN` fill at the ask + 1 % penalty (5300). Never random.
- Restricted proceeds: short-sale proceeds settle to 1030; the cover fill releases `min(1030 balance for the
  instrument, proceeds for the covered quantity)` to 1010 (`RELEASE` entry).
- `credit.loan_draw` {account, currency, amount, term_days|open, index}: Dr 1010 ccy / Cr 2600 (`loan:{request_id}`);
  settled cash at once. Daily accrual `accr:loan:{loan_id}:{d}`: Dr 5100 / Cr 2340 at (index as of `d` + 0.75 %).
  `credit.loan_repay` {loan_id, amount}: needs settled cash in that currency ≥ amount + accrued (else REFUSED "buy
  EUR first: settled EUR 0.00, needed 1,000,152.78"); posts Dr 2600 principal / Dr 2340 accrued / Cr 1010. Maturity
  (`loanmat:{loan_id}`): auto-repay if cash allows, else roll with Attention WARN (no invented default).
- Repo/reverse repo (`credit.repo_open` {account, side, collateral instrument, face, term}): REPO: Dr 1010 cash
  (face × price × (1 − haircut)) / Cr 2500; the collateral position stays on the books flagged `pledged` (positions
  cannot be sold while pledged: OVERSELL check subtracts pledged qty). REVERSE: Dr 1500 / Cr 1010; collateral received
  is off-balance-sheet (memo). Interest daily (5120/2330, 4330/1160); close returns principal + interest.
- PB financing: `credit.pb_draw`/`pb_repay` (Dr 1010 / Cr 2700) and the automatic draw in §9.5 step 2; headroom =
  50 % × long market value in the PB account (Reg-T initial, disclosed) − current loan; interest daily (5110/2300),
  paid monthly.

### 10.7 Bonds (`products/bonds.py`, DEBT account)

- Curated set: UST bills 1M/3M/6M/1Y, notes 2/3/5/7/10Y, bonds 20/30Y (on-the-run by convention: coupon rounded to
  1/8 from the par yield at first sight, maturity = issue + tenor, issue = the most recent mid-month auction date
  **convention**; ids `B:UST-10Y-2036-08-15`); 20 IG corporates from the universe's bond list with their stated
  coupon/maturity, priced curve + FRED `BAMLC0A0CM` OAS (+ rating bucket adjustment table), all `support=COMPUTED`.
- Mark: clean price from the par curve (bootstrapped to zeros, linear interpolation; `pricing.bond_clean`), quality
  `computed`, with the curve date. Accrued ACT/ACT (`pricing.accrued`), daily true-up `accr:bond:{acct}:{inst}:{d}`:
  Dr 1150 / Cr 4300 for the change in accrued; coupon on schedule (`coupon:{acct}:{inst}:{date}`): Dr 1010 coupon /
  Cr 1150 (accrued reset); maturity: `MATURITY` fill at 100.
- Fill: gross = face × clean/100; accrued paid/received separately: BUY Dr 1100 clean / Dr 1150 accrued / Dr 5000 /
  Cr 2100 (clean + accrued + fee), T+1. Short bonds: allowed with borrow; the short pays coupons in lieu (5210).

### 10.8 FX (`products/fx.py`, FOREIGN_FX account; Treasury may also deal FX to fund foreign needs)

- `fx.spot` {account, buy_ccy, sell_ccy, amount, amount_ccy}: mid from Yahoo (`live` required), rate = mid ± 0.2 pip
  (schedule) against the user; value date T+2 business days (US calendar); entries §15 scenario 6; obligations
  `obl:fx:{deal}:BUY` (IN, 1250 → 1010) and `:SELL` (OUT, 2150 → 1010).
- `fx.forward`: rate by covered interest parity from the two policy/term rates (FRED), `computed`; booked off-balance
  sheet with mark-to-market shown as `unrealised` (an obligation pair at maturity). Limited; the UI labels it.
- `fx(ccy)` marks for valuation: Yahoo; Frankfurter daily as fallback (`close`).

### 10.9 Lifecycle job catalogue (`lifecycle.due_jobs`)

| job | when (ET) | key | idempotent on |
|---|---|---|---|
| lifecycle.settle | first tick on/after due_date 00:05 | settle:obl:{id} | obligations.status |
| lifecycle.accrue (borrow_fee, rebate, loan, pb, repo, bond, cash) | first tick after d+1 00:05 | accr:{kind}:{ref}:{d} | job_runs |
| lifecycle.interest_pay | 1st of month 00:05 | pay:{kind}:{ref}:{YYYY-MM} | job_runs |
| credit.collateral_mark | 17:05 | cmark:{borrow}:{d} | borrows.last_mark_date |
| futures.vm → futures.margin_mark | 17:05 | vm:…, fmargin:… | positions.settle_px, job_runs |
| options.margin_mark | 17:05 and after option fills | omargin:{acct}:{d} | job_runs |
| options.expire / futures.expire | expiry date 17:05 (index options 16:20) | oexp:…, fexp:… | fills.fill_key |
| lifecycle.dividend / coupon | 18:00 | div:…, coupon:… | job_runs |
| lifecycle.day_order_expiry | each group's session close | expire:{order}:{d} | orders.status |
| lifecycle.sweep_out | 16:30 | sweep_out:{acct}:{d} | job_runs |
| lifecycle.eod_snapshot | 17:05 | eod:{d} | nav_snapshots PK |
| marks.record (CLOSE/SETTLE) | 16:05 (US), 17:05 (CME, FX) | marks PK | marks PK |
| rates.record (FRED, Treasury curve, FINRA) | 17:30 / 18:00 / 19:00 | rates PK | rates PK |
| integrity.full | startup + hourly | — | — |
Catch-up: at startup `lifecycle.catch_up` walks every date from the last `eod` snapshot to today in order, generating
the same keys; a 3-day shutdown over a weekend therefore posts Fri/Sat/Sun accruals, Monday's settlements and the
missing EOD snapshots (with marks labelled by what was observed — a snapshot made from a `close` quote fetched after
the fact is labelled `close (recorded late)`).

---

## 11. Hedge candidates (`hedges.py`) and hedge legs

`candidates(instrument, side, qty, account, quotes)` — computed from the live Cboe chain (or the futures table) when the
ticket's instrument is entered; each candidate: `{kind, legs:[{instrument_id, side, qty, price_basis, price, quality}],
cost, protection_level, notes, as_of}`:
- long equity/ETF: PROTECTIVE_PUT (expiry = first listed expiry ≥ today + 90 days; strike = listed strike nearest
  0.93 × spot; contracts = floor(qty / 100); cost = ask × 100 × n), COLLAR (+ sell call nearest 1.07 × spot at bid),
  PUT_SPREAD (0.93 / 0.85 strikes), INDEX_FUTURES_OVERLAY (sell ES: n = round(qty × spot × beta / (ES × 50)); beta
  from the master, labelled static);
- short equity: PROTECTIVE_CALL (1.07), CALL_SPREAD, COLLAR;
- bond: TREASURY_FUTURES (DV01-matched: n = round(position DV01 / contract DV01), contract DV01 from the futures
  reference note's duration table: ZT 1.9, ZF 4.2, ZN 6.5, ZB 15 × price × 10 bp, labelled approximate);
- FX spot: FORWARD (CIP, computed) or FX_OPTION (unsupported → shown as such);
- futures: option on the future (not on Cboe free data → shown as unsupported).
Execution: the primary order carries `hedge_spec`; child orders are created at submission with `qty = ratio ×
primary.qty` (floor to contract size), status WORKING only when `parent` has a fill; on each primary fill the child's
qty is set to `floor(ratio × parent.filled) − child.filled` (resize); the child is cancelled when the parent is
cancelled/expired with no fills. All-or-none packages (collars, spreads) submit as one `group_id` and are matched
leg by leg in one `order.match` command that fills all legs or none against one quote snapshot (requires Cboe sizes to
cover every leg; otherwise waits).

---

## 12. Risk warnings and compliance notices (`risk.py`) — inform only

Computed on preview and on the account page: gross/net exposure, concentration (position / NAV > 10 %, 25 %), option
Greeks by underlying (from Cboe greeks), DV01 (bonds, computed), FX exposure by currency, margin headroom (1300 vs
required, maintenance), borrow recall risk (HTB tier). Compliance catalogue (each with rule name, threshold, "verify the
current rule text"): Reg SHO 203(b) locate (always satisfied: we borrow first), Rule 201 price test (short at/below
the bid after a 10 % drop from the prior close → notice), Reg T 50 % initial (PB accounts), FINRA 4210 maintenance
25 %, PDT (≥ 4 day trades in 5 business days in an account under 25k — informational), 13D/13G 5 % and Section 16 10 %
of `shares_outstanding`, 13h-1 (daily 2 M shares / 20 M USD), 13F (≥ 100 M in 13F securities), option position limits
(table: 250,000 contracts default), CFTC/CME spot-month limits (table), self-trading (a buy in one account against a
working sell in another), wash sale (a repurchase within 30 days of a loss sale — informational), EU/UK 0.1 %/0.5 %
short disclosure for FOREIGN names. Output: `[{rule, status: 'ok'|'notice'|'breach', message, threshold, value}]`.

---

## 13. HTTP API (`api.py`) and server model (`server.py`)

### 13.1 Routes (all JSON; GET = read model; POST/DELETE/PATCH = command, body carries `request_id`)

| method | path | params / body | response | errors |
|---|---|---|---|---|
| GET | /api/health | — | {version, pid, started_at, db_path, db_size, schema_version, clock: {now_utc, now_et}, workers: {quotes, lifecycle, matcher: {alive, last_run, last_error, errors_1h}}, providers: {yahoo, cboe, treasury, fred, finra, frankfurter: {ok, last_ok, last_error, consecutive_failures, backoff_until}}, integrity: {ok, checked_at, problems}, attention_open: n, calendar: {file, fallback_years}, lock: {path, pid}} | — (never locks) |
| POST | /api/shutdown | loopback only | {shutting_down: true} | 403 |
| GET | /api/accounts | — | [{id, name, kind, status, nav, cash_usd, pnl_today, quality}] | |
| POST | /api/accounts/open-pb | {broker, request_id} | account | 400 BROKER_UNKNOWN, 409 EXISTS |
| GET | /api/accounts/{id} | — | snapshot: {account, cash: {ccy: CashView}, buying_power, positions: [PositionView], orders_working, pending_settlements, borrows, loans, collateral, margin, pnl: {today, mtd, ytd, inception}, nav, attention, as_of, quality} | 404 |
| PATCH | /api/accounts/{id}/settings | {sweep_out?, sweep_buffer?, pb_financing?, request_id} | account | 400 |
| POST | /api/accounts/{id}/close | {request_id} | account | 409 NOT_EMPTY |
| GET | /api/accounts/{id}/positions | — | [PositionView] | |
| GET | /api/accounts/{id}/positions/{instrument_id} | — | {position, lots, fills, entries, borrows, marks_history} | 404 |
| GET | /api/accounts/{id}/orders | ?status=working\|all&limit= | [OrderView] | |
| GET | /api/accounts/{id}/transactions | ?from&to&kind&instrument&limit&offset&format=json\|csv | [{entry_id, ts, trade_date, kind, memo, lines:[…], ref}] | |
| GET | /api/accounts/{id}/cash | — | {ccy: CashView}, movements (last 200 cash lines) | |
| GET | /api/accounts/{id}/pnl | ?period= | PnlView (+ explain rows + residual) | |
| GET | /api/treasury | — | TreasuryView + log (3000/3100 lines) | |
| POST | /api/treasury/deposit | {amount, currency, note, request_id} | {entry_id, cash} | 400 BAD_AMOUNT |
| POST | /api/treasury/withdraw | {amount, currency, note, request_id} | | 400 INSUFFICIENT |
| POST | /api/treasury/transfer | {from_account, to_account, amount, currency, request_id} | | 400 |
| GET | /api/instruments/search | ?q=&limit= | [Instrument + quote summary] (resolves unknown US tickers via Yahoo) | |
| GET | /api/instruments/{id} | — | Instrument + quote + session + support + spec | 404 |
| GET | /api/instruments/{id}/bars | ?interval=1m\|1d&range=1d\|1mo\|1y | [{t,o,h,l,c,v}] + meta | 503 provider |
| GET | /api/instruments/{id}/chain | ?expiry= | {underlying quote, expiries, rows: [{strike, call:{…}, put:{…}}], as_of, quality} | 503 |
| GET | /api/quotes | ?ids=a,b,c | {id: QuoteView{last,bid,ask,quote_time,fetched_at,source,quality,delay_label,age_s}} | |
| GET | /api/sessions | — | {group: SessionState} | |
| GET | /api/calendar | ?year= | holidays, early closes, source | |
| POST | /api/orders/preview | OrderRequest | PreviewView (§9.2) | 400/409/503 |
| POST | /api/orders | OrderRequest + preview_token | {order: OrderView, fills: [...], sweep, hedge_orders} | 400 §9.3, 409 STALE_QUOTE/PRICE_MOVED/SESSION, 503 |
| GET | /api/orders/{id} | — | OrderView + fills + entries | 404 |
| DELETE | /api/orders/{id} | ?request_id= | OrderView | 409 NOT_CANCELLABLE |
| POST | /api/orders/{id}/resume | {request_id} | | |
| POST | /api/credit/borrow | {account_id, instrument_id, qty, request_id} | borrow + collateral entry | 400 NO_BORROW |
| POST | /api/credit/return | {borrow_id, qty, request_id} | | 400 |
| POST | /api/credit/loans | {account_id, currency, amount, term_days, request_id} | loan | |
| POST | /api/credit/loans/{id}/repay | {amount, request_id} | | 400 INSUFFICIENT_CASH |
| POST | /api/credit/repo | {account_id, side, instrument_id, face, term_days, request_id} | | |
| POST | /api/credit/repo/{id}/close | {request_id} | | |
| POST | /api/credit/pb/{account_id}/draw \| repay | {amount, request_id} | | |
| GET | /api/credit/locate | ?instrument_id=&qty= | {available, tier, fee_rate, rebate_rate, collateral, source} | |
| POST | /api/fx/spot | {account_id, buy_ccy, sell_ccy, amount, amount_ccy, request_id} | deal + entries | 409 STALE_QUOTE |
| POST | /api/fx/forward | {…, value_date} | | |
| POST | /api/options/exercise | {account_id, instrument_id, qty, request_id} | | |
| GET | /api/hedges | ?instrument_id=&side=&qty=&account_id= | [candidate] | |
| GET | /api/portfolio | — | consolidated positions across accounts, grouped by product group, with subtotals | |
| GET | /api/accounting/balance-sheet | ?account=ALL\|id | rows + ties | |
| GET | /api/accounting/pnl | ?account=&period= | | |
| GET | /api/accounting/journal | ?account=&from&to&gl&instrument&limit&offset | entries with lines | |
| GET | /api/accounting/integrity | ?full=1 | IntegrityReport | |
| GET | /api/risk | ?account= | exposures, Greeks, DV01, FX, margin, compliance | |
| GET | /api/activity | ?since=&limit= | events (newest first) | |
| GET | /api/attention | — | open attention items | |
| POST | /api/attention/{id}/resolve | {request_id} | | |
| GET | /api/jobs | — | due/recent job_runs, next run times | |
| POST | /api/jobs/run | {job?, request_id} | runs catch-up now (idempotent) | |
| GET/PATCH | /api/settings | | | |
| GET/POST/DELETE | /api/watchlist | {account_id, instrument_id} | | |
| GET | / and /static/* | | the UI (no-store headers) | |

Every money field is a decimal **string**; every mark-dependent field is `{"value","quality","as_of","source"}`.

### 13.2 Threading model

- `ThreadingHTTPServer` (daemon threads), `allow_reuse_address = False`; on Windows `server_bind` sets
  `SO_EXCLUSIVEADDRUSE` before `bind` so a second process **cannot** bind 8765 (the old `allow_reuse_address=True`
  let two servers share the port).
- Reads: a thread-local `sqlite3` read connection per request thread (`open_reader`), no lock; marks from the
  `QuoteCache` (RLock held microseconds).
- Writes: `CommandBus` owns the only writer connection; `execute` takes the process-wide RLock (bounded 30 s →
  503 `WRITER_BUSY`); typical command < 20 ms.
- Workers (daemon threads): `QuoteService` ×2 fetchers + 1 scheduler; `Matcher` ×1; `LifecycleWorker` ×1 (60 s);
  `Persister` (quotes batch, 15 s). Network I/O never happens while holding the writer lock. Each worker loop wraps its
  body in `try/except Exception: log.exception; health.errors += 1; attention(ERROR)`; a worker that dies is restarted
  by the supervisor tick (main thread, 30 s) and the death is reported.
- Bounded retries and rate limits: §7.2. Provider errors: counted per provider, surfaced in `/api/health.providers`
  and the UI strip ("Yahoo unreachable since 14:02 — quotes are stale; executions paused").
- Memory budget: ~60–150 MB (universe rows in SQLite not memory; quote cache ≤ 2,000 entries; ≤ 2 reduced chains).
- Graceful shutdown: `POST /api/shutdown` (loopback) → `stop_event.set()` → workers finish their current command
  (≤ 5 s) → bus `close()` (checkpoint WAL) → `httpd.shutdown()` in a thread → lock file removed → process exits 0.
  Ctrl-C does the same.
- Single-instance guard: `FINSIM_HOME/terminal.lock` created `O_CREAT|O_EXCL` with `{pid, started_at, port,
  version}`; if present: read pid; if that pid is alive (Windows: `OpenProcess`/`tasklist`) → exit 3 "FinSim Terminal
  is already running (pid N, port 8765)"; if dead → stale lock replaced. The exclusive bind is the second line. The
  scheduler/workers start only **after** the port is bound and the schema is open.
- Startup order: lock → backup db → open writer + `init_schema`/`migrate` → `init_capital` command → bind port →
  start workers → `catch_up` (lifecycle, in the lifecycle thread, so health answers at once) → full integrity.
- Logging: `FINSIM_HOME/terminal.log`, `RotatingFileHandler` 5 MB × 5, INFO; every command (type, request_id,
  duration, result code), every provider error, every job.

---

## 14. UI (`terminal/static/`), launcher and Windows deployment

### 14.1 Pages and components (vanilla JS, one file per page, no build step)

Shell: header (app name, "PAPER TRADING — fictional capital" badge, clock ET, provider/health dot, Attention strip),
left sidebar by account: **Overview** · **Treasury** · **US Exchange** · **Debt Instruments** · **Credit Instruments** ·
**Foreign Markets & FX** · **Prime Brokers** (list + "Open a PB account…") · divider · **Portfolio** (all accounts) ·
**Risk** · **Accounting** · **Activity** · **Settings**. The active account is always visible in the header
("Trading in: US Exchange — buying power 2,153,375.00").

| page / component | file | content | polling |
|---|---|---|---|
| Overview | page-overview.js | tiles: total NAV, today/MTD/YTD, cash (Treasury + accounts), pending settlements, working orders, margin/collateral; attention list; per-account cards | /api/accounts 10 s, /api/attention 30 s |
| Treasury | page-treasury.js | capital, advances by account, cash by ccy, available, interest accrued; Deposit / Withdraw / Transfer forms (amount required, confirm shows the amount) ; log | /api/treasury 10 s |
| Account page (×each) | page-account.js | tabs **Markets** (search + watchlist table with last/bid/ask/age badge; row click loads the ticket), **Positions** (qty, avg, mark+badge, MV, unrealised, realised, income, financing, day, weight), **Orders** (working/history; cancel), **Transactions** (journal view, filters, CSV), **Cash & financing** (settled/pending/restricted/collateral/margin by ccy; borrows; loans; sweeps) | snapshot 10 s; /api/quotes for visible ids 5 s |
| Ticket (docked right on every account page) | ticket.js | instrument, side, qty, type, limit/stop, TIF; **Preview** card (price basis + source + age, fee, total, settlement date, funding line "own 1,000,000 + Treasury sweep 2,846,625", borrow line, margin line, warnings, compliance notices); **Hedge panel** (candidates as cards: cost, protection, legs; edit strike/expiry/ratio; "add to order"); Submit disabled until a preview is < 30 s old and executable (or the order rests) | preview debounced 400 ms; re-preview every 15 s while open |
| Chain | chain.js | Cboe chain by expiry (bid/ask/size/iv/delta/oi) with "delayed 15 min, as of hh:mm" | on open, 90 s |
| Portfolio | page-portfolio.js | consolidated positions grouped by product group; subtotals; filters; CSV | 10 s |
| Accounting | page-accounting.js | tabs Balance sheet / P&L (explain + residual) / Journal / Integrity (last report, run now) | 30 s |
| Risk | page-risk.js | exposures, Greeks, DV01, FX, margin headroom, compliance table | 30 s |
| Activity | page-activity.js | events stream with filters | 10 s |
| Settings | page-settings.js | per-account sweep switch/buffer/PB financing; participation; display; "Legacy simulation (port 8766)" link | — |
| core.js | api(), request-id generation (`crypto.randomUUID`), retry-with-same-id on network error, number formatting (Decimal strings), freshness badge (`live` green · `delayed` blue · `close` grey "close 16:00" · `computed` purple · `stale` amber "stale 23 min" · `unavailable` red), visibility-aware polling (pause when hidden), toast + Attention strip | |
| charts.js | canvas line chart for 1-minute bars (optional) | |

Freshness and attention: every price cell shows a badge with age; the ticket repeats source + time + delay in words;
NAV tiles show the worst quality and "excludes N unpriced" when partial; the Attention strip lists worker errors,
integrity failures, margin shortfalls, recall notices, suspended orders. No simulation controls exist anywhere.

### 14.2 Launcher / CLI (`terminal/app.py`, wired from `finsim/__main__.py`)

`python -m finsim <cmd>` from `C:\Users\logan\intelligence-terminal\finsim`:
- `serve [--port 8765] [--db PATH] [--host 127.0.0.1]` — runs the terminal (foreground). Env: `FINSIM_HOME`
  (default `~/.finsim`), `FINSIM_TERMINAL_DB` (default `FINSIM_HOME/terminal.db`), `FINSIM_PORT`.
- `stop` — POST /api/shutdown; wait ≤ 10 s for the lock-file pid to exit; report; exit 1 if still alive (never kills).
- `status` — health (version, pid, uptime, db, integrity, providers, workers), lock file, login entry, backups.
- `install [--no-open]` — writes `FINSIM_HOME/terminal-server.vbs` and `terminal-open.vbs` (ASCII), copies the
  server VBS to the Startup folder as **`FinSim Server.vbs`** (same name as the old entry so it replaces it), removes
  the old scheduled task `FinSim Server` (`schtasks /Delete /F /TN "FinSim Server"`) and the old
  `~/.finsim/finsim-server.vbs` Startup copy, creates Desktop + Start Menu `FinSim.lnk` → `wscript terminal-open.vbs`,
  then stops any server answering on 8765 whose `/api/health` lacks `"terminal"` (the old 0.11.0 engine) via
  `/api/shutdown`, starts the terminal (detached `pythonw`), waits for health with `version`, and opens the window.
- `open` — start if not running (health probe 1 s, then wait up to 120 s — never spawns a second server: the lock
  file/exclusive bind make a duplicate exit 3, and `open` reports that instead) and open the Chrome/Edge app window
  (`finsim.app.open_window`, profile `FINSIM_HOME/terminal-window-profile`).
- `uninstall [--purge]` — removes Startup/shortcuts/VBS, stops the server; `--purge` deletes `terminal.db` only after
  typing its path (never touches `finsim.db`).
- `legacy [--port 8766]` — copies `finsim.db` to `finsim-legacy.db` once (if absent) and runs the OLD
  `finsim.api.server.serve` on that copy (read-only intent: `finsim.db` itself is never opened). Documented as
  archive viewing.
- `python -m finsim` with no args prints the help; the old `demo`/`phone` subcommands are not exposed for the
  terminal (`legacy` keeps the old app reachable).

`FinSim Server.vbs` (Startup), ASCII only, modelled on `finsim2/install/ShafferDeskTerminal-Server.vbs`: resolve
`%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe` (fallback: `py -3.12` via `where`), set `PYTHONPATH` to the
`finsim` folder, `FINSIM_HOME`, probe `http://127.0.0.1:8765/api/health` with a 1 s timeout; if it answers **and** the
JSON contains `"terminal"` → quit; if it answers without it (old engine) → POST `/api/shutdown`, wait 5 s; then
`pythonw -m finsim serve --port 8765` hidden (window style 0). The exclusive bind + lock file protect against the race
with a manual `open`.

Final deployment sequence (for the report): `cd C:\Users\logan\intelligence-terminal\finsim` → `py -3.12 -m finsim stop`
(stops whatever answers) → `py -3.12 -m finsim install --no-open` → `py -3.12 -m finsim status` → `py -3.12 -m finsim open`.

---

## 15. Test plan (`finsim/tests/test_terminal_*.py`, unittest, no network, temp SQLite, FakeClock, fake providers)

Fixtures (`tests/terminal_helpers.py`): `make_bus(tmpdir, clock, quotes)` → bus + db; `FakeQuotes` holds a dict of
`Quote`s (the test sets `quote_time`, `bid/ask/last`, `bar_volume`) and a `FakeChain`; `FakeRates` (SOFR 3.87 %,
ECBDFR 2.00 %); `fill(bus, order, quote)` runs `order.match`; `assert_integrity(tc, conn)`; `ledger(conn, acct,
gl, ccy)` natural balance. Every expected number below is worked by hand in the test comment. Dates use the real NYSE
file (2026-10-16 Fri, 10-19 Mon, 10-20 Tue, 10-22 Thu are business days; 2026-11-20 is the November expiry Friday).

### 15.1 Scenario 1 — deposit (test_terminal_treasury.py)
- Start: `init:capital:1` → Treasury 1010 USD 10,000,000.00; 3000 = 10,000,000.00; NAV 10,000,000.00.
- Restart the bus on the same file → `init_capital` replays; 1010 still 10,000,000.00 (not 20,000,000).
- `treasury.deposit` 2,500,000.00 (id `ui:d1`) → 1010 12,500,000.00; 3000 12,500,000.00; `capital_explained` ok.
  Replay `ui:d1` → `replayed=True`, balances unchanged. Same id with amount 1.00 → 409 REQUEST_ID_REUSED.
- `treasury.deposit` without `amount` → 400 BAD_AMOUNT; `amount=0` → 400.

### 15.2 Scenario 2 — Treasury transfer + sweep-in + sweep-out (test_terminal_treasury.py)
- Transfer 1,000,000.00 to US_EXCHANGE: Treasury 1010 9,000,000.00, Treasury 3100 (debit) 1,000,000.00; US_EXCHANGE
  1010 1,000,000.00, 3100 (credit) 1,000,000.00; Σ3100 = 0; total NAV 10,000,000.00 (`transfers_net_zero`).
- BUY 5,000 SPY market, quote ask 769.32 (Cboe, fresh), bar_volume 120,000 (cap 12,000 ≥ 5,000): gross 5,000 ×
  769.32 = 3,846,600.00; fee 5,000 × 0.005 = 25.00; needed 3,846,625.00; spendable 1,000,000.00 → sweep
  2,846,625.00. After: US_EXCHANGE 1010 = 1,000,000 + 2,846,625 = 3,846,625.00; 2100 = 3,846,625.00; 1100 SPY
  3,846,600.00; 5000 25.00; Treasury 1010 = 9,000,000 − 2,846,625 = 6,153,375.00; Treasury 3100 = 3,846,625.00. NAV
  US_EXCHANGE at mark 769.32 = 3,846,625 − 3,846,625 + 3,846,600 = 3,846,600.00; consolidated NAV = 6,153,375 +
  3,846,600 = 9,999,975.00 = 10,000,000 − 25 fee ✓.
- Sweep-out at 16:30 same day: idle = 3,846,625 − 3,846,625 (pending out) − 10,000 buffer < 0 → nothing (job_runs
  SKIPPED). Alternative account with settled 50,000.00, no needs → sweep 40,000.00 back (buffer 10,000.00).
- Treasury too small: Treasury 100.00, order needing 1,000.00 more → 400 INSUFFICIENT_FUNDS with the three amounts;
  no entries posted (`commands` row REJECTED; lines count unchanged).

### 15.3 Scenario 3 — equity buy, fee, T+1 settlement (test_terminal_equity.py)
- Mon 2026-10-19 10:00 ET, US_EXCHANGE settled 500,000.00. BUY 1,000 AAPL market; quote ask 339.50 (Cboe
  15:46-style fresh), bar_volume 120,000. Fill 1,000 @ 339.50; gross 339,500.00; fee 5.00.
  Entry `fill:1`: Dr 1100 AAPL 339,500.00; Dr 5000 5.00; Cr 2100 339,505.00. Obligation OUT 339,505.00 due
  2026-10-20. Lot 1,000 @ 339.50 basis 339,500.00.
- Reads at T: settled 500,000.00; pending_out 339,505.00; projected 160,495.00; spendable (settle 10-20)
  160,495.00; position 1,000 AAPL basis 339,500.00, mark 339.50 (quality live) → MV 339,500.00, unrealised 0.00;
  NAV = 500,000 − 339,505 + 339,500 = 499,995.00; P&L today −5.00 (fees −5.00, residual 0.00).
- Clock → Tue 2026-10-20 00:10 ET; lifecycle tick: `settle:obl:1`: Dr 2100 339,505.00 / Cr 1010 339,505.00 →
  settled 160,495.00; pending 0; NAV unchanged 499,995.00. Tick again → no second settlement (job_runs replay).
- Mark 342.00 → MV 342,000.00; unrealised 2,500.00; NAV 502,495.00; today (vs Mon 17:00 snapshot 499,995.00 at
  339.50) = +2,500.00; explain: Δunrealised 2,500.00, fees 0 → residual 0.00.

### 15.4 Scenario 4 — sell with FIFO realised P&L (test_terminal_equity.py)
- Lots: A 1,000 AAPL @ 339.50 (Mon, fee 5.00), B 500 @ 335.00 (Tue, fee 2.50). Wed: SELL 1,200 @ bid 345.00.
  Gross 414,000.00; fee 6.00; net 413,994.00. FIFO: 1,000 from A (339,500.00) + 200 from B (67,000.00) = 406,500.00
  relieved; realised 414,000 − 406,500 = 7,500.00.
  Entry: Dr 1200 413,994.00; Dr 5000 6.00; Cr 1100 AAPL 406,500.00; Cr 4000 AAPL 7,500.00 (413,994 + 6 = 414,000 =
  406,500 + 7,500 ✓). Remaining lot B 300 @ 335.00 basis 100,500.00; position 300; 1100 AAPL = 100,500.00
  (`cost_accounts_match`). Realised to date 7,500.00; fees to date 13.50. Settlement Thu: Dr 1010 / Cr 1200 413,994.00.
- SELL 400 with position 300 → 400 OVERSELL. SELL 300 + SELL_SHORT 100 (with borrow) is the accepted form.

### 15.5 Scenario 5 — short sale, borrow, 102 % collateral, fee across a weekend (test_terminal_credit.py)
- Fri 2026-10-16 10:00 ET, US_EXCHANGE settled 200,000.00; ABC last 50.00 (bid 50.00 / ask 50.02), FINRA ratio 30 %
  → GC 0.30 %, rebate 3.87 − 0.25 = 3.62 %. `order.submit` SELL_SHORT 1,000 ABC market with `borrow`: collateral
  1.02 × 1,000 × 50.00 = 51,000.00: Dr 1400 ABC 51,000.00 / Cr 1010 51,000.00 (settled → 149,000.00). Fill 1,000 @
  bid 50.00: gross 50,000.00; fee 5.00; Dr 1200 ABC 49,995.00; Dr 5000 5.00; Cr 2200 ABC 50,000.00. Lot SHORT 1,000 @
  50.00 basis 50,000.00; `shorts_borrowed` ok (borrow 1,000 OPEN).
- Mon 10-19 00:10: settle → Dr 1030 ABC 49,995.00 / Cr 1200 49,995.00 (restricted). Accruals for the nights of Fri,
  Sat, Sun (keys `accr:borrow_fee:1:2026-10-16`, `…10-17`, `…10-18`), cumulative method on 51,000.00 at 0.30 %
  ACT/360: through night 1 = 0.425 → 0.42 (HALF_EVEN); through night 2 = 0.85 → 0.85 (posts 0.43); through night 3 =
  1.275 → 1.28 (posts 0.43). The test asserts the three postings 0.42 / 0.43 / 0.43 and the totals 2310 = 5200 = 1.28.
  Rebate at 3.62 %: cumulative 51,000 × 0.0362 × 3/360 = 15.385 → 15.38: 1160 = 4320 = 15.38.
- Mon 17:05 collateral mark with close 52.00: required 1.02 × 52,000 = 53,040.00; call 2,040.00: Dr 1400 / Cr 1010
  (settled 149,000 − 2,040 = 146,960.00). With close 48.00 instead: required 48,960.00 → return 2,040.00.
- Position at 52.00: MV −52,000.00; unrealised = 50,000 − 52,000 = −2,000.00. NAV (close 52) = 146,960 + 49,995 +
  53,040 + 15.38 − 52,000 − 1.28 = 198,009.10; check: start 200,000 − fee 5 − 2,000 unrealised + 15.38 − 1.28 =
  198,009.10 ✓.
- Tue cover BUY_TO_COVER 1,000 @ ask 51.00: gross 51,000.00; fee 5.00. Dr 2200 ABC 50,000.00; Dr 4000 ABC 1,000.00
  (loss); Dr 5000 5.00; Cr 2100 51,005.00. Release: Dr 1010 49,995.00 / Cr 1030 49,995.00. Borrow return (same
  command): Dr 1010 53,040.00 / Cr 1400 53,040.00; fee Mon night 53,040 × 0.003/360 = 0.442 → cumulative on the new
  principal: 0.44 posted; settle fees Dr 2310 1.72 / Cr 1010 1.72; rebate Dr 1010 / Cr 1160 (15.38 + 5.33 = 20.71;
  Mon night 53,040 × 0.0362/360 = 5.3335 → 5.33). Borrow CLOSED; `shorts_borrowed` ok (no short, no borrow).
- No borrow available (FINRA ratio 80 %, 3 days) → SELL_SHORT → 400 NO_BORROW, nothing posted. Partial borrow
  (locate gives 600 of 1,000): order qty scaled to 600 with the reason in the preview and the order's `reason`.

### 15.6 Scenario 6 — currency loan + FX spot T+2 (test_terminal_fx.py)
- Tue 2026-10-20: CREDIT `credit.loan_draw` EUR 1,000,000.00, rate ECBDFR 2.00 % + 0.75 % = 2.75 %, ACT/360: Dr
  1010 EUR 1,000,000.00 / Cr 2600 EUR 1,000,000.00 (base at mid 1.1238 → 1,123,800.00). EUR settled 1,000,000.00.
- `fx.spot` sell EUR 1,000,000 buy USD, mid 1.1238, rate 1.1238 − 0.00002 = 1.12378 → USD 1,123,780.00, value Thu
  10-22. Entry (two legs): USD: Dr 1250 1,123,780.00 / Cr 3900 1,123,780.00; EUR: Dr 3900 1,000,000.00 / Cr 2150
  1,000,000.00 (both EUR lines at 1.12378 → base 1,123,780.00 each; 3900 base nets to 0).
- Reads at T (fx 1.1238): EUR settled 1,000,000.00, pending_out 1,000,000.00, projected 0.00; USD pending_in
  1,123,780.00; loan 1,000,000.00 EUR. NAV = 1,123,800 (EUR cash) + 1,123,780 (USD receivable) − 1,123,800 (EUR
  payable) − 1,123,800 (loan) = −20.00 = the spread cost ✓. fx_result (§5.5): EUR A/L lines 1010 +1,000,000 (base
  +1,123,800), 2600 −1,000,000 (base −1,123,800), 2150 −1,000,000 (base −1,123,780): Σ amount = −1,000,000 × 1.1238 =
  −1,123,800.00; Σ base = −1,123,780.00; fx_result = −20.00 ✓ (= NAV; explain residual 0.00).
- Wed 10-21 00:10: `accr:loan:1:2026-10-20`: 1,000,000 × 0.0275/360 = 76.3888… → 76.39: Dr 5100 EUR 76.39 / Cr 2340
  EUR 76.39 (Wed fx 1.1300 → base 86.32 each). Reads at fx 1.1300: NAV = 1,130,000 + 1,123,780 − 1,130,000 −
  1,130,000 − 86.32 = **−6,306.32**. Explain: financing = −86.32 (the 5100 line at its posting rate); fx_result = Σ
  amount (−1,000,000 − 1,000,000 + 1,000,000 − 76.39 = −1,000,076.39) × 1.13 = −1,130,086.32, minus Σ base
  (−1,123,800 + 1,123,800 − 1,123,780 − 86.32 = −1,123,866.32) → **−6,220.00** (= −6,200.00 rate move on the EUR
  loan vs the fixed USD receivable, plus the −20.00 spread); realised 0, fees 0; total −86.32 − 6,220.00 = −6,306.32
  = NAV ✓, residual 0.00. The test asserts NAV, financing, fx_result and the residual.
- Thu 10-22 00:10 settlement (fx 1.1300): EUR leg Dr 2150 +1,000,000 (base +1,130,000) / Cr 1010 −1,000,000 (base
  −1,130,000); USD leg Dr 1010 +1,123,780 / Cr 1250 −1,123,780. USD settled 1,123,780.00; EUR settled 0.00; loan +
  accrued (Tue and Wed nights) = 1,000,152.78 EUR (cumulative 152.777… → 152.78). NAV unchanged by settlement at the
  same rate: 1,123,780 − 1,130,000 − 172.64 (152.78 × 1.13) = −6,392.64, i.e. −6,306.32 − 86.32 (Wed night's
  accrual) ✓; fx_result still −6,220.00 (the zero-balance 1010/2150 EUR lines keep residual carrying −6,200 and
  +6,220, which the definition counts).
- `credit.loan_repay` 1,000,152.78 EUR with EUR settled 0 → 400 INSUFFICIENT_CASH "buy EUR 1,000,152.78 first"; after
  an FX spot buying EUR 1,000,160 and its T+2 settlement → repay posts Dr 2600 1,000,000.00 / Dr 2340 152.78 / Cr
  1010 EUR 1,000,152.78; loan CLOSED; `loans_match` ok.
- Borrow USD and buy stock the same day (loan USD 500,000 in CREDIT, transfer to US_EXCHANGE, buy): spendable rises
  by the drawdown at once (settled cash), interest accrues from the draw date.

### 15.7 Scenario 7 — option expiry, exercise and assignment (test_terminal_options.py)
- Long 10 `O:SPY261120C00760000` bought @ 15.20 (ask), fee 6.50: Dr 1100 15,200.00 / Dr 5000 6.50 / Cr 2100
  15,206.50; short 5 `O:SPY261120P00740000` sold @ 9.80 (bid), fee 3.25: Dr 1200 4,896.75 / Dr 5000 3.25 / Cr 2210
  4,900.00. Margin mark for the naked put at S 769.32: max(9.80 + 20 % × 769.32 − 29.32, 9.80 + 10 % × 740) = max(9.80
  + 153.864 − 29.32, 83.80) = 134.344 → × 500 = 67,172.00 → Dr 1300 / Cr 1010 67,172.00 (test asserts to the cent:
  134.344 × 500 = 67,172.00 ✓).
- Clock → Fri 2026-11-20 17:06 ET; SPY CLOSE mark 772.00 (quote_time 16:00:01, quality close). `oexp:…C00760000`:
  intrinsic 12.00 ≥ 0.01 → EXERCISE: Dr 1100 SPY 760,000.00 / Cr 2100 760,000.00 (due Mon 11-23; sweep-in from
  Treasury 760,000.00 at the fill); option closed at 0: Dr 4000 15,200.00 / Cr 1100 option 15,200.00. Put 740 OTM →
  EXPIRY at 0: Dr 2210 4,900.00 / Cr 4000 4,900.00; margin released Dr 1010 / Cr 1300 67,172.00. Posted realised =
  −15,200.00 + 4,900.00 = −10,300.00; SPY 1,000 basis 760,000.00, MV 772,000.00, unrealised +12,000.00; realised +
  unrealised = +1,700.00, which equals the economics: call paid 15,200 for intrinsic 12,000 (−3,200) plus the put
  premium kept (+4,900) ✓. Fees to date 9.75 shown separately.
- Alternative close 735.00: put ITM 5.00 → ASSIGNMENT: Dr 1100 SPY 370,000.00 / Cr 2100 370,000.00; Dr 2210 4,900.00
  / Cr 4000 4,900.00; call OTM → Dr 4000 15,200.00 / Cr 1100 15,200.00; SPY 500 @ 740 basis 370,000.00, MV
  367,500.00, unrealised −2,500.00.
- Missing close mark (quote stale) → job SKIPPED, Attention entry, position still open; a later tick with the mark
  completes it once (fill_key unique).
- Early exercise by the user Thu before expiry: `options.exercise` 10 contracts at S 771 → same bookings with
  `exercise:{acct}:{inst}:2026-11-19:1`.

### 15.8 Scenario 8 — futures variation margin, two days (test_terminal_futures.py)
- Mon: BUY 2 `F:ESZ26` @ 7,754.25 (last, fresh); fee 4.50: Dr 5000 / Cr 1010 4.50. IM 2 × 22,000 = 44,000.00: Dr
  1300 / Cr 1010 44,000.00 (sweep-in if needed). Position qty 2, settle_px 7,754.25, basis 0.
- Mon 17:05 SETTLE mark 7,760.00: VM = (7,760.00 − 7,754.25) × 50 × 2 = 575.00: Dr 1010 575.00 / Cr 4100 ESZ26
  575.00; settle_px → 7,760.00. Running the job again → replay, no second posting.
- Tue 17:05 SETTLE 7,741.50: VM = (7,741.50 − 7,760.00) × 100 = −1,850.00: Dr 4100 1,850.00 / Cr 1010 1,850.00.
  Cumulative 4100 = −1,275.00 = (7,741.50 − 7,754.25) × 100 ✓. Intraday Wed mark 7,750.00 → unrealised (7,750.00 −
  7,741.50) × 100 = 850.00 (computed, not posted).
- Settled cash negative after VM (account held only the IM) → sweep-in 1,850.00 from Treasury (`margin_mark`),
  Attention WARN if Treasury cannot.
- Expiry at the March contract's date: final VM then MATURITY fill closes the position; IM released.

### 15.9 Infrastructure tests
- **Restart persistence** (test_terminal_persistence.py): scenarios 3 and 5 → close the bus → reopen the same file →
  `cash/positions/orders/borrows` identical; `init_capital` replayed; `integrity()` ok; quotes table restored into the
  cache with their original `quote_time` (so they show as `close`/`stale`, never `live`).
- **Rollback on a failing write**: register a test handler that posts one valid entry then raises `RuntimeError`;
  after `execute` raises: `journal_entries` count unchanged, `commands` has no row for that id, `gl_balances`
  unchanged; the next command succeeds. Also: a handler that posts an unbalanced entry → `LedgerError` → rolled back;
  a handler that corrupts `positions` (qty off by 1) → `IntegrityFailure` → rolled back and `attention` has the ERROR.
- **Duplicate-server prevention** (test_terminal_server.py): start server A on a free port with a temp
  `FINSIM_HOME` → start B with the same home/port in a subprocess → exit code 3 within 5 s, stderr names A's pid;
  A still serves `/api/health`; stale lock (pid of a dead process) is replaced and the server starts.
- **Stale quote blocking** (test_terminal_orders.py): session OPEN (FakeClock Tue 10:30 ET), AAPL quote
  `quote_time` 25 min old → preview `executable=false` reason STALE_QUOTE; `order.submit` MARKET → 409 STALE_QUOTE;
  LIMIT 339.00 → accepted WORKING; fresh quote (last 338.90, bar_volume 50,000) → `order.match` fills 1,000 @ 339.00
  (limit); the same quote again → replay, no second fill; a fresh quote 339.20 → no fill.
- **Session blocking**: FakeClock Tue 17:30 ET: MARKET order → WORKING with `expected_execution` "Wed 09:30 ET";
  `order.match` with a fresh-looking quote at 17:31 → skipped (session closed); Wed 09:31 with a fresh quote → fills.
  DAY order placed Tue 17:30 expires Wed 16:00 (`expire:…:2026-10-21`), not Tue. Early close 2026-11-27: expiry 13:00.
- **Partial fills by observed volume**: MARKET 50,000 SPY, bar_volume 120,000 → fill 12,000 (cap 10 %), PARTIAL;
  next quote bar_volume 90,000 → +9,000; `bar_volume` 0 → no fill, order keeps WORKING with "no observed volume".
  Hedge child (protective put, ratio 1/100): after 12,000 → child qty 120; after 21,000 → 210.
- **Ledger/NAV reconciliation** (test_terminal_reconcile.py): run scenarios 1–8 in one database; assert
  `integrity(full)` ok; Σ account NAV == Treasury NAV + Σ trading NAVs == capital + Σ(net income) − 0 to the cent;
  balance sheet A − L == NAV for every account and ALL; `pnl('inception')` == NAV − capital for ALL; every P&L period
  residual 0.00; `transfers_net_zero`; `capital_explained`.
- **Accrual catch-up**: stop the clock Fri 16:00, restart Tue 09:00 → the lifecycle posts Fri/Sat/Sun/Mon accruals in
  order with their own keys, Monday settlements, two EOD snapshots (Fri, Mon… and Sat/Sun snapshots), none twice.
- **Compatibility**: `py -3.12 -m unittest tests.test_app tests.test_tzfallback tests.test_quant` and the FinSim2
  suites (`tests/test_fs2_*.py`, `tests/test_desk_*.py`) run before and after; the old package is unchanged except
  `__main__.py`, so differences must be zero; `finsim.db` sha256 identical before/after any terminal run.

---

## 16. Build order: work packages, ownership, interfaces, acceptance

| WP | owns (files) | consumes | acceptance |
|---|---|---|---|
| **WP0 Core** (first; one agent) | `__init__`, `money.py`, `clock.py`, `calendar.py`, `sessions.py`, `db.py`, `ledger.py`, `commands.py`, `accounting.py` (cash/nav/balance sheet/treasury/integrity; P&L periods), `instruments.py` (master + seeding + ids), `tests/terminal_helpers.py`, `test_terminal_core.py`, `test_terminal_treasury.py` | `finsim.money`, `finsim.tzfallback`, `data/nyse-holidays.json`, `universe.json` | schema creates on 3.12 and 3.14; scenarios 1–2 pass; integrity rejects an unbalanced entry and a bad projection; request-id replay; rollback test; `sessions` table unit tests for every group incl. early close and CME break; settlement dates over the 2026-11-26/27 holiday |
| **WP1 Market data** | `http.py`, `providers/*`, `quotes.py`, `pricing.py` (curve, bond price, CIP, option fallback), `test_terminal_quotes.py` | WP0 (`Command` light types, `instruments`) | fake-server tests for parsing the probe payloads (saved fixtures from PROBE), freshness rule table tests, circuit breaker, UA policy test (asserts the header per host), chain reducer memory test (< 150 MB peak on the 13 MB `_SPX` fixture), `MarkSet` snapshot immutability |
| **WP2 Orders & booking** | `orders.py`, `booking.py`, `fees.py`, `products/equities.py`, `test_terminal_orders.py`, `test_terminal_equity.py` | WP0, WP1 interfaces (`Quote`, `MarkSet`) | scenarios 3–4, stale/session/partial/sweep tests, FIFO crossing zero, preview token, hedge child resizing (with WP4 stubs) |
| **WP3 Lifecycle** | `lifecycle.py`, accrual helpers, `test_terminal_lifecycle.py`, `test_terminal_persistence.py` | WP0, WP2 bookings | settlement, accrual cumulative rounding, catch-up ordering, EOD snapshots, sweep-out, DAY expiry, idempotency under repeated ticks, restart persistence |
| **WP4 Products** | `products/options.py`, `products/futures.py`, `products/bonds.py`, `products/fx.py`, `products/credit.py`, `hedges.py`, `test_terminal_options.py`, `test_terminal_futures.py`, `test_terminal_credit.py`, `test_terminal_fx.py` | WP0–WP3 | scenarios 5–8; margin formulas to the cent; expiry with a missing mark is skipped not invented; recall only from the FINRA heuristic |
| **WP5 API + server** | `api.py`, `server.py`, `risk.py`, `test_terminal_server.py`, `test_terminal_api.py` | WP0–WP4 | every route in §13.1 answers with the documented shape; reads never block on a slow fake provider (test: a 5 s provider call during a GET /api/accounts returns < 200 ms); duplicate-server exit 3; graceful shutdown closes the lock; health reports a dead worker |
| **WP6 UI** | `static/*` | WP5 routes | browser check: pages load with zero console errors; ticket preview → submit → position visible → F5 and restart keep it; freshness badges change with injected quote ages; Attention shows a forced provider failure |
| **WP7 Launcher** | `terminal/app.py`, `finsim/__main__.py` (re-pointed + `legacy`), VBS templates, `docs/TERMINAL_EXECUTION.md`, `docs/TERMINAL_RUNBOOK.md` | WP5 | `install/open/status/stop/uninstall` on the laptop; old scheduled task removed; Startup `FinSim Server.vbs` replaced; `legacy` serves the copy on 8766 and `finsim.db` sha unchanged |
| **WP8 Reconciliation & compatibility** | `test_terminal_reconcile.py`, baseline runs | all | §15.9 reconciliation; compatibility suites equal to baseline; report with before/after numbers |

Parallelism: WP1–WP4 start as soon as WP0 publishes `db.py`, `ledger.py`, `commands.py`, `accounting.py`
signatures and the schema (they can stub `QuoteService` with `FakeQuotes`). WP5 starts after WP2's `preview/submit`
signatures exist. WP6 can begin against the route table with recorded JSON fixtures. Each WP merges only with its
tests green on `py -3.12 -m unittest` and `py -3.14 -m pytest`.

---

## 17. Assumptions and open points (stated, not blocking)

1. Yahoo per-contract futures symbol suffixes (`.CME/.CBT/.NYM/.CMX`) generalise from the probe's `CLX26.NYM`; WP1
   verifies and records.
2. CME holidays approximated by the NYSE list; SIFMA bond-market early closes approximated by NYSE early closes (14:00
   bonds). Shown in `/api/calendar.notes`.
3. Futures initial/maintenance margins are a static table (labelled); dividend pay dates by convention (ex + 15
   business days); Treasury auction/issue dates by convention; policy rates for non-USD/EUR/GBP are monthly OECD series
   (labelled with their observation month).
4. Yahoo `regularMarketTime` is treated as a delayed quote (label "up to 15 min"); Cboe `last_trade_time` is naive ET.
5. No early assignment of short options, no recalls except the FINRA heuristic, no forced liquidation — all disclosed
   as "not simulated" rather than invented.
6. FX result convention (§5.5) replaces a realised/unrealised FX split; stated on screen.
7. Options margin moves cash to 1300 (so "buying power" is honest); real brokers only reserve it.
