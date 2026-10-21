# FinSim Live Paper-Trading Terminal — architecture proposal (product-and-UI lens)

Author: Claude (Fable 5.1), 2026-10-20. Lens: the account-organised terminal the owner will use every day, with
accounting, execution and operations specified to the level a team of parallel coding agents can build from.
Builds on `docs/TERMINAL_DECISIONS.md` (not re-litigated), `docs/HANDOFF_2026-10-20.md`, `docs/DIRECTIVE_2026-10-20.md`
and the probe `docs/PROBE_2026-10-20.txt`. Where this document is silent, TERMINAL_DECISIONS.md wins.

Conventions used below: `Decimal` values travel as JSON strings; timestamps are ISO-8601 with offset; "NY" means
`America/New_York` through `finsim.tzfallback.get_zone` (Python 3.12 on this laptop has no tzdata); "business date" means
the NY calendar date of a US business day. Money is quantised to cents (`ROUND_HALF_EVEN`), prices to 4 dp
(`finsim.money`), quantities to the instrument's lot.

---

## 0. Scope and the two builds

**Build 1 (one long session, in this order):** core (ledger, bus, accounting, quotes, sessions, server, shell UI) →
Treasury + funding (deposits, transfers, sweeps) → US Exchange equities/ETFs incl. shorts with borrows → lifecycle jobs
(settlement, accruals, DAY/GTC expiry, sweep-out, daily NAV snapshot) → listed equity/index options (Cboe chain,
Reg-T margin, expiry/exercise/assignment, hedge candidates) → futures (core 20 contracts, IM/VM) → Credit Instruments
(currency loans) + FX spot → Portfolio / Accounting / Risk / Activity pages → launcher. Everything else is shown in the
UI as **LIMITED** or **NOT IN THIS BUILD** with the honest label; nothing is priced with invented data.

**Build 2 (later, same architecture):** Debt Instruments (Treasuries from the par curve, curated corporates with FRED
OAS), Foreign Markets (non-US listings via Yahoo meta sessions), FX forwards (CIP, computed), repo / reverse repo,
securities lending out of inventory, dividends from Yahoo chart events, PB OTC (IRS/TRS, computed), recall notices
from observable data, phone access.

Explicit **assumptions** are collected in §20; each is also stated where it bites.

---

## 1. Runtime architecture

One process: `pythonw.exe -m finsim serve` (Python 3.12; also runs on 3.14). Threads:

| Thread | Does | Touches network? | Touches DB? |
|---|---|---|---|
| HTTP (ThreadingHTTPServer, one daemon thread per request) | reads: own thread-local read connection, **no global lock**; writes: build a `Command` and call `bus.execute()` | never | reads directly; writes only through the bus |
| **Writer** (not a thread: the `CommandBus` holds one `threading.Lock` and one write connection; whichever thread calls `execute()` runs the command while holding the lock) | `BEGIN IMMEDIATE` → command → COMMIT/ROLLBACK | never | the only writer |
| **QuoteWorker** | every cycle: compute the subscribed symbol set, fetch Yahoo (spark for breadth, v8 chart 1m for focus/working-order symbols), Cboe chain on demand, update the in-memory `QuoteCache`, then `bus.execute(RecordMarks)` and, if any order is working, `bus.execute(MatchOrders(snapshot))` | yes, outside every lock | only via the bus |
| **LifecycleWorker** | every 60 s: `registry.due(now)` → `bus.execute(RunJob(job, key))` per due key (date-keyed jobs catch up every missed date) | never (rates it needs come from the `RatesCache` the QuoteWorker fills) | only via the bus |
| **RatesWorker** (part of QuoteWorker's loop, every 30 min) | FRED CSV (SOFR, DGS*, ECBDFR, IUDSOIA, OAS), Treasury.gov par curve (daily), Frankfurter (FX fallback) | yes | via the bus (`RecordRates`) |

Data flow for a trade: UI preview → `GET`-like `POST /api/orders/preview` (read connection + `QuoteCache` snapshot, no
write) → UI submit with `request_id` + `preview_id` → `bus.execute(SubmitOrder)` (validates again against the latest
snapshot, creates the order, matches immediately if marketable, posts fills + journal + settlement rows + sweep
transfer in ONE transaction) → response carries orders, fills, sweeps → UI repaints from the response, then polls.

Startup order (`server.start()`): acquire lock file → open DB + migrate → `InitialCapital` command (idempotent) →
`integrity()` (failures become attention items, never block startup) → bind port exclusively → start HTTP → start
QuoteWorker → start LifecycleWorker (catch-up) → log `ready`. Shutdown: `POST /api/shutdown` (loopback only) → stop
event → workers finish the current command → `httpd.shutdown()` → `PRAGMA wal_checkpoint(TRUNCATE)` → close → delete
lock file.

Memory budget (7.6 GB laptop, ~1.5 GB free): QuoteCache ≤ 2,000 records (~2 MB); Cboe chains reduced at parse time to
expiries ≤ 400 days and strikes within ±35 % of spot, at most 3 underlyings cached (LRU, ~5 MB each; SPX transient parse
≈ 100 MB, released at once); read connections cache_size 2 MB each; no in-memory ledger — every read model is a SQL
aggregate. Target steady state < 150 MB RSS.

---

## 2. Package layout: `finsim/finsim/terminal/`

Standard library only. Every module lists its public surface; private helpers are the implementer's choice.

```
finsim/finsim/terminal/
  __init__.py        VERSION = "1.0.0"; APP = "finsim-terminal"
  config.py          paths and constants
  clockcal.py        real clock, sessions, holidays/early closes, settlement dates
  db.py              SQLite open/migrate; reader connections; the writer connection
  ledger.py          chart of accounts, posting templates, balanced-entry posting
  bus.py             CommandBus, Command, errors, idempotency
  instruments.py     instrument master, symbol mapping, OCC/futures parsing, resolve-by-symbol
  quotes.py          QuoteRecord, freshness rules, QuoteCache, subscriptions, RatesCache
  providers/
    http.py          urlopen wrapper: UA policy, gzip, timeouts, size cap, bounded retries, circuit breaker
    yahoo.py         spark (batch last price), chart v8 (1m bars + meta), resolve
    cboe.py          delayed quotes JSON → reduced chain + underlying NBBO
    treasury_gov.py  daily par yield curve XML
    fred.py          fredgraph.csv series
    frankfurter.py   ECB reference rates (FX fallback)
    fake.py          deterministic in-memory provider for tests
  accounts.py        account registry, PB opening, per-account settings
  accounting.py      ALL read models + integrity()
  funding.py         deposits, internal transfers, sweep-in/out, funding plan for a trade
  orders.py          validation, submit, cancel, matching, fills → lots/ledger/settlements
  lifecycle.py       job registry + runner; settlement, accruals, expiries, VM, collateral marks, sweep-out, snapshots
  products/
    __init__.py      Product protocol + registry
    equities.py      stocks/ETFs incl. short sale + borrow leg
    options.py       listed options: contracts, margin, expiry/exercise/assignment
    futures.py       contract specs, IM/VM, expiry
    fx.py            FX spot (T+2), forwards (Build 2, computed)
    credit.py        currency loans; securities-lending borrow book; repo (Build 2)
    bonds.py         Build 2 (Treasuries from par curve)
  pricing.py         BSM/Black-76 (wraps finsim.engines.options_pricing), bond price from curve, CIP forward
  hedges.py          hedge candidates from the live chain
  risk.py            exposures, scenarios, concentration, warnings; compliance notices (inform only)
  tables.py          fee tables, rate spreads, PB schedules, futures specs, session groups, chart of accounts names
  api.py             routes → service calls; JSON codec; error mapping
  server.py          TerminalServer (exclusive bind), workers, health state, attention, lock file, shutdown
  app.py             CLI: serve/stop/status/install/open/uninstall/legacy; Windows VBS + shortcuts
  static/            index.html, terminal.css, core.js, components/*.js, pages/*.js (§16)
```

### 2.1 Signatures (public)

```python
# config.py
def home() -> str                       # $FINSIM_HOME or ~/.finsim
def db_path() -> str                    # $FINSIM_TERMINAL_DB or home()/terminal.db
def log_path() -> str                   # home()/terminal.log
def lock_path() -> str                  # home()/terminal.lock
PORT = 8765; LEGACY_PORT = 8766; HOST = "127.0.0.1"
INITIAL_CAPITAL = Decimal("10000000.00"); BASE_CCY = "USD"

# clockcal.py
class Clock:                            # injectable; tests pass FakeClock(now)
    def now_utc(self) -> datetime
    def now_ny(self) -> datetime
    def today_ny(self) -> date
def ny_zone() -> tzinfo                 # tzfallback.get_zone("America/New_York")
class Calendar:
    def __init__(self, holidays_json_path: str)          # repo data/nyse-holidays.json + finsim.calendar rule fallback
    def is_business_day(self, d: date) -> bool
    def early_close(self, d: date) -> time | None        # 13:00 on listed days
    def add_business_days(self, d: date, n: int) -> date
    def prev_business_day(self, d: date) -> date
class SessionState: group: str; state: str  # OPEN|CLOSED|BREAK ; opens_at; closes_at; next_open; is_early_close
def session_state(group: str, at: datetime, cal: Calendar, instrument_meta: dict | None = None) -> SessionState
def settlement_date(cal: Calendar, trade_date: date, cycle: int) -> date
def settle_time(group: str) -> time     # futures daily settlement time (NY)

# db.py
SCHEMA_VERSION = 1
class Database:
    def __init__(self, path: str)
    def migrate(self) -> None
    def reader(self) -> sqlite3.Connection        # thread-local, query_only, WAL snapshot
    def writer(self) -> sqlite3.Connection        # the single write connection (bus only)
    def close(self) -> None

# ledger.py
CHART: dict[str, tuple[str, str]]               # code -> (name, ASSET|LIABILITY|EQUITY|INCOME|EXPENSE)
class Line(NamedTuple): gl: str; ccy: str; debit: Decimal; credit: Decimal; fx_rate: Decimal; instrument_id: str|None; lot_id: str|None; memo: str
def dr(gl, amount, ccy="USD", fx_rate=Decimal(1), instrument_id=None, lot_id=None, memo="") -> Line
def cr(gl, amount, ccy="USD", fx_rate=Decimal(1), instrument_id=None, lot_id=None, memo="") -> Line
def post(tx: "Tx", *, idem_key: str, account_id: str, kind: str, memo: str, lines: list[Line], value_date: date, ref_type: str|None=None, ref_id: str|None=None) -> int
    # raises UnbalancedEntry if Σ debit_base != Σ credit_base (to the cent); raises DuplicatePosting on idem_key reuse
def balance(conn, account_id: str, gl: str, ccy: str | None = None, *, base: bool = False, instrument_id: str|None=None, lot_id: str|None=None, upto: date|None=None) -> Decimal

# bus.py
class CommandError(Exception): code: str; message: str; reasons: list[dict]   # -> HTTP 400
class ConflictError(CommandError): ...                                         # -> HTTP 409
class Tx:                                   # what a command receives
    conn: sqlite3.Connection; clock: Clock; cal: Calendar; request_id: str; now: datetime
    def event(self, type: str, payload: dict, account_id=None, instrument_id=None, ref_type=None, ref_id=None) -> int
    def post(self, **kw) -> int             # ledger.post bound to this tx
class Command:
    name: str; request_id: str; params: dict; origin: str = "api"
    def execute(self, tx: Tx) -> dict       # pure function of (db state, params, clock); NO network
class CommandResult(NamedTuple): request_id: str; result: dict; replayed: bool; seq: int
class CommandBus:
    def __init__(self, db: Database, clock: Clock, cal: Calendar, log: logging.Logger, after_commit: Callable[[Command, dict], None])
    def execute(self, cmd: Command, timeout: float = 10.0) -> CommandResult
    state_seq: int                          # bumps after every commit (UI polls it)

# accounting.py  (every function takes a read connection and a QuoteCache snapshot; nothing here writes)
def cash(conn, account_id, ccy=None) -> dict          # {ccy: {settled, pending, restricted, available, carried_base}}
def positions(conn, snap, account_id) -> list[dict]
def lots(conn, account_id, instrument_id) -> list[dict]
def buying_power(conn, snap, account_id, ccy="USD") -> dict
def balance_sheet(conn, snap, account_id | "ALL", as_of=None) -> dict
def nav(conn, snap, account_id | "ALL") -> Decimal
def pnl(conn, snap, account_id | "ALL", period: str) -> dict  # TODAY|MTD|YTD|ITD
def treasury(conn, snap) -> dict
def transactions(conn, account_id, **filters) -> list[dict]
def integrity(conn, snap) -> dict                             # {ok, checks:[{name, ok, detail}]}

# orders.py
def preview(conn, snap, clock, cal, req: dict) -> dict       # read-only; raises CommandError on malformed input
class SubmitOrder(Command); class CancelOrder(Command); class MatchOrders(Command)   # params: snapshot
def fee_for(instrument: dict, qty: Decimal, price: Decimal, account: dict) -> tuple[Decimal, str]

# funding.py
class Deposit(Command); class Transfer(Command); class SetAccountSettings(Command)
def funding_plan(conn, snap, account_id, ccy, need: Decimal, when: date) -> dict   # settled/pending/available/sweep_in/pb_financing/ok/reasons
def apply_sweep_in(tx, account_id, ccy, amount, link_ref) -> int                   # posts the pair of entries, returns transfer id

# lifecycle.py
class Job: name: str; def due_keys(self, conn, now, cal) -> list[str]; def run(self, tx, key) -> dict
class RunJob(Command)       # params {job, key}; inserts jobs_done(key) in the same tx (duplicate key = replay, no-op)
class LifecycleWorker(threading.Thread)
REGISTRY: list[Job]

# quotes.py
@dataclass class QuoteRecord: ...     # §7.3
class QuoteCache:
    def get(self, instrument_id) -> QuoteRecord | None
    def snapshot(self, ids: Iterable[str]) -> dict[str, QuoteRecord]   # deep-copied, immutable for a command
    def update(self, records: list[QuoteRecord]) -> list[str]           # returns changed ids
    def subscribe(self, instrument_id, ttl_s=90) -> None                 # viewed symbols
def classify(rec: QuoteRecord, session: SessionState, now: datetime) -> str   # live|delayed|close|computed|stale|unavailable
def executable(rec, session, now) -> tuple[bool, str|None]
class RecordMarks(Command); class RecordRates(Command)

# server.py
class AlreadyRunning(RuntimeError)
class TerminalServer:
    def __init__(self, home: str, db_path: str, port: int, clock=None, providers=None)
    def start(self) -> None;  def stop(self) -> None;  def health(self) -> dict
def acquire_lock(path) -> int   # returns our pid; raises AlreadyRunning(pid) if another live process holds it

# app.py
def main(argv) -> int           # serve|stop|status|install|open|uninstall|legacy
def launcher_files(home, python_exe, repo_root, port) -> dict[str, str|bytes]   # pure: path -> content
```

Reused from the existing code without modification: `finsim.money` (D, money, price, qty), `finsim.tzfallback`,
`finsim.calendar.us_equity_holidays` (rule fallback), `finsim.engines.options_pricing` (bsm, crr, greeks, implied_vol),
the Yahoo/Cboe parsing ideas in `finsim2/data/yahoo.py` and `finsim2/data/cboe.py` (copied, not imported: Cboe gets an
honest UA here, and the terminal must not depend on finsim2).

---

## 3. SQLite schema (DDL)

File `%USERPROFILE%\.finsim\terminal.db`. `finsim.db` is never opened by this package.

```sql
PRAGMA journal_mode = WAL;  PRAGMA synchronous = FULL;  PRAGMA foreign_keys = ON;  PRAGMA busy_timeout = 5000;

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- keys: schema_version, created_at, base_ccy, initial_capital_request_id

CREATE TABLE accounts (
  id TEXT PRIMARY KEY,                    -- TREASURY | US_EXCHANGE | DEBT | CREDIT | FOREIGN_FX | PB_GS | PB_JPM | PB_MS | PB_DB | PB_BARC | PB_CITI | PB_UBS | PB_BNP
  kind TEXT NOT NULL,                     -- TREASURY | EXCHANGE | DEBT | CREDIT | FOREIGN | PB
  name TEXT NOT NULL, base_ccy TEXT NOT NULL DEFAULT 'USD', broker TEXT,
  status TEXT NOT NULL DEFAULT 'OPEN',    -- OPEN | CLOSED
  sweep_out INTEGER NOT NULL DEFAULT 1, sweep_buffer TEXT NOT NULL DEFAULT '0.00', pb_financing INTEGER NOT NULL DEFAULT 0,
  opened_at TEXT NOT NULL, sort INTEGER NOT NULL DEFAULT 0, settings_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE commands (
  request_id TEXT PRIMARY KEY, name TEXT NOT NULL, params_hash TEXT NOT NULL, params_json TEXT NOT NULL,
  result_json TEXT NOT NULL, origin TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT NOT NULL
);

CREATE TABLE events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL REFERENCES commands(request_id),
  at TEXT NOT NULL, type TEXT NOT NULL, account_id TEXT, instrument_id TEXT, ref_type TEXT, ref_id TEXT, payload_json TEXT NOT NULL
);
CREATE INDEX ix_events_acct ON events(account_id, seq);  CREATE INDEX ix_events_type ON events(type, seq);

CREATE TABLE journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL REFERENCES commands(request_id),
  idem_key TEXT NOT NULL UNIQUE,           -- §4.3: the no-duplicate-posting guarantee
  at TEXT NOT NULL, value_date TEXT NOT NULL, account_id TEXT NOT NULL REFERENCES accounts(id),
  kind TEXT NOT NULL,                      -- FILL|SETTLE|DEPOSIT|TRANSFER|SWEEP_IN|SWEEP_OUT|ACCRUAL|PAY|VM|MARGIN|COLLATERAL|EXPIRY|EXERCISE|ASSIGN|FX|LOAN|FEE
  memo TEXT NOT NULL, ref_type TEXT, ref_id TEXT
);
CREATE TABLE journal_lines (
  id INTEGER PRIMARY KEY AUTOINCREMENT, journal_id INTEGER NOT NULL REFERENCES journal(id) ON DELETE CASCADE,
  account_id TEXT NOT NULL, gl TEXT NOT NULL, ccy TEXT NOT NULL,
  debit TEXT NOT NULL DEFAULT '0', credit TEXT NOT NULL DEFAULT '0',            -- local currency
  debit_base TEXT NOT NULL DEFAULT '0', credit_base TEXT NOT NULL DEFAULT '0',  -- USD at fx_rate
  fx_rate TEXT NOT NULL DEFAULT '1', instrument_id TEXT, lot_id TEXT, memo TEXT
);
CREATE INDEX ix_jl_acct_gl ON journal_lines(account_id, gl, ccy);
CREATE INDEX ix_jl_instr ON journal_lines(account_id, instrument_id, gl);
CREATE INDEX ix_jl_journal ON journal_lines(journal_id);

CREATE TABLE instruments (
  id TEXT PRIMARY KEY, cls TEXT NOT NULL,  -- EQUITY|ETF|OPTION|FUTURE|FX|BOND|INDEX
  symbol TEXT NOT NULL, name TEXT, ccy TEXT NOT NULL, exchange TEXT, timezone TEXT,
  yahoo_symbol TEXT, cboe_symbol TEXT, session_group TEXT NOT NULL, settle_cycle INTEGER NOT NULL,
  multiplier TEXT NOT NULL DEFAULT '1', tick TEXT NOT NULL DEFAULT '0.01', lot TEXT NOT NULL DEFAULT '1',
  underlying_id TEXT, expiry TEXT, strike TEXT, "right" TEXT, style TEXT,     -- options: right C|P, style AMERICAN|EUROPEAN, settle PHYSICAL|CASH in spec_json
  last_trade TEXT, settle_time TEXT,                                           -- futures
  support TEXT NOT NULL DEFAULT 'FULL',    -- FULL | LIMITED | UNSUPPORTED (the honest label the UI shows)
  spec_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'ACTIVE', created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_instr_yahoo ON instruments(yahoo_symbol) WHERE yahoo_symbol IS NOT NULL;

CREATE TABLE marks (                       -- last valid mark per instrument (survives restart; UI shows it with its time)
  instrument_id TEXT PRIMARY KEY REFERENCES instruments(id),
  price TEXT NOT NULL, bid TEXT, ask TEXT, bid_size INTEGER, ask_size INTEGER, prev_close TEXT, day_volume TEXT,
  bar_ts TEXT, bar_volume TEXT, quote_time TEXT NOT NULL, fetched_at TEXT NOT NULL,
  source TEXT NOT NULL, delay_label TEXT NOT NULL, quality TEXT NOT NULL, method TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE close_marks (                 -- the observed close used for snapshots, expiries, VM and collateral marks
  instrument_id TEXT NOT NULL, mark_date TEXT NOT NULL, price TEXT NOT NULL, quote_time TEXT NOT NULL,
  source TEXT NOT NULL, quality TEXT NOT NULL, PRIMARY KEY (instrument_id, mark_date)
);
CREATE TABLE rates (                       -- FRED / Treasury.gov observations
  series TEXT NOT NULL, obs_date TEXT NOT NULL, value TEXT NOT NULL, fetched_at TEXT NOT NULL, source TEXT NOT NULL,
  PRIMARY KEY (series, obs_date)
);

CREATE TABLE packages (                    -- a primary order and its dependent hedge orders
  id TEXT PRIMARY KEY, request_id TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE orders (
  id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES commands(request_id),
  account_id TEXT NOT NULL REFERENCES accounts(id), instrument_id TEXT NOT NULL REFERENCES instruments(id),
  side TEXT NOT NULL,                      -- BUY | SELL | SELL_SHORT | BUY_TO_COVER | BTO | STO | BTC | STC
  qty TEXT NOT NULL, filled_qty TEXT NOT NULL DEFAULT '0', avg_price TEXT,
  order_type TEXT NOT NULL,                -- MARKET | LIMIT | STOP | STOP_LIMIT
  limit_px TEXT, stop_px TEXT, tif TEXT NOT NULL,            -- DAY | GTC
  moo INTEGER NOT NULL DEFAULT 0,          -- market order accepted outside the session, works at the first fresh quote after the open
  status TEXT NOT NULL,                    -- ACCEPTED | WORKING | PARTIALLY_FILLED | FILLED | CANCELLED | EXPIRED | REJECTED | WAITING_PARENT
  triggered INTEGER NOT NULL DEFAULT 0, reason TEXT, last_bar_ts TEXT,
  package_id TEXT REFERENCES packages(id), depends_on TEXT REFERENCES orders(id), ratio TEXT,   -- hedge leg: qty = parent filled × ratio
  participation TEXT NOT NULL DEFAULT '0.10', borrow_id TEXT, preview_id TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, expires_at TEXT
);
CREATE INDEX ix_orders_working ON orders(status) WHERE status IN ('ACCEPTED','WORKING','PARTIALLY_FILLED','WAITING_PARENT');
CREATE INDEX ix_orders_acct ON orders(account_id, created_at);

CREATE TABLE fills (
  id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id), idem_key TEXT NOT NULL UNIQUE,   -- '{order_id}:{bar_ts|now}'
  account_id TEXT NOT NULL, instrument_id TEXT NOT NULL, side TEXT NOT NULL, qty TEXT NOT NULL, price TEXT NOT NULL,
  gross TEXT NOT NULL, fee TEXT NOT NULL, fee_basis TEXT NOT NULL, ccy TEXT NOT NULL,
  trade_at TEXT NOT NULL, trade_date TEXT NOT NULL, settle_date TEXT NOT NULL,
  quote_json TEXT NOT NULL,                -- {price_basis, source, quote_time, fetched_at, quality, bid, ask, bar_ts, bar_volume, cap}
  journal_id INTEGER NOT NULL REFERENCES journal(id)
);
CREATE TABLE lots (
  id TEXT PRIMARY KEY, account_id TEXT NOT NULL, instrument_id TEXT NOT NULL, fill_id TEXT NOT NULL REFERENCES fills(id),
  side TEXT NOT NULL,                      -- LONG | SHORT
  orig_qty TEXT NOT NULL, open_qty TEXT NOT NULL, cost_per_unit TEXT NOT NULL,   -- SHORT: proceeds per unit
  ccy TEXT NOT NULL, opened_at TEXT NOT NULL, closed_at TEXT
);
CREATE INDEX ix_lots_open ON lots(account_id, instrument_id, opened_at) WHERE open_qty <> '0';
CREATE TABLE lot_relief (
  id INTEGER PRIMARY KEY AUTOINCREMENT, lot_id TEXT NOT NULL REFERENCES lots(id), fill_id TEXT REFERENCES fills(id),
  event_kind TEXT NOT NULL,                -- SELL | COVER | EXPIRE | EXERCISE | ASSIGN | FUT_SETTLE
  qty TEXT NOT NULL, unit_proceeds TEXT NOT NULL, realised TEXT NOT NULL, journal_id INTEGER NOT NULL
);
CREATE TABLE positions (                   -- projection, verified by integrity() against fills ± option events
  account_id TEXT NOT NULL, instrument_id TEXT NOT NULL, qty TEXT NOT NULL, settle_px TEXT, updated_at TEXT NOT NULL,
  PRIMARY KEY (account_id, instrument_id)
);
CREATE TABLE settlements (
  id TEXT PRIMARY KEY, fill_id TEXT UNIQUE REFERENCES fills(id), fx_deal_id TEXT UNIQUE,
  account_id TEXT NOT NULL, ccy TEXT NOT NULL, amount TEXT NOT NULL,     -- signed: + receivable, − payable
  due_date TEXT NOT NULL, status TEXT NOT NULL,                           -- PENDING | SETTLED
  settled_at TEXT, journal_id INTEGER REFERENCES journal(id)
);
CREATE INDEX ix_settle_due ON settlements(status, due_date);

CREATE TABLE borrows (
  id TEXT PRIMARY KEY, account_id TEXT NOT NULL, instrument_id TEXT NOT NULL, qty TEXT NOT NULL, open_qty TEXT NOT NULL,
  lender TEXT NOT NULL, fee_bps TEXT NOT NULL, rebate_bps TEXT NOT NULL, htb_tier TEXT NOT NULL,
  collateral TEXT NOT NULL,                -- current cash collateral posted (= ledger 1400 for this borrow)
  status TEXT NOT NULL, opened_at TEXT NOT NULL, closed_at TEXT, order_id TEXT
);
CREATE TABLE borrow_marks (borrow_id TEXT NOT NULL, mark_date TEXT NOT NULL, mv TEXT NOT NULL, required TEXT NOT NULL,
  delta TEXT NOT NULL, journal_id INTEGER, PRIMARY KEY (borrow_id, mark_date));

CREATE TABLE loans (
  id TEXT PRIMARY KEY, account_id TEXT NOT NULL, lender TEXT NOT NULL, ccy TEXT NOT NULL,
  principal TEXT NOT NULL, outstanding TEXT NOT NULL, index_series TEXT NOT NULL, spread_bps TEXT NOT NULL,
  day_count TEXT NOT NULL DEFAULT 'ACT/360', drawn_at TEXT NOT NULL, maturity TEXT, status TEXT NOT NULL
);
CREATE TABLE accruals (                    -- one row per (kind, ref, date): the idempotency of every daily accrual
  idem_key TEXT PRIMARY KEY, kind TEXT NOT NULL, ref_type TEXT NOT NULL, ref_id TEXT NOT NULL, account_id TEXT NOT NULL,
  accrual_date TEXT NOT NULL, ccy TEXT NOT NULL, amount TEXT NOT NULL, rate TEXT NOT NULL, basis TEXT NOT NULL,
  journal_id INTEGER NOT NULL
);
CREATE TABLE fx_deals (
  id TEXT PRIMARY KEY, account_id TEXT NOT NULL, buy_ccy TEXT NOT NULL, sell_ccy TEXT NOT NULL,
  buy_amt TEXT NOT NULL, sell_amt TEXT NOT NULL, rate TEXT NOT NULL, mid TEXT NOT NULL, spread_cost_base TEXT NOT NULL,
  trade_at TEXT NOT NULL, settle_date TEXT NOT NULL, status TEXT NOT NULL, quote_json TEXT NOT NULL, journal_id INTEGER NOT NULL
);
CREATE TABLE futures_settle (
  account_id TEXT NOT NULL, instrument_id TEXT NOT NULL, settle_date TEXT NOT NULL, settle_px TEXT NOT NULL,
  qty_after TEXT NOT NULL, vm TEXT NOT NULL, journal_id INTEGER NOT NULL, PRIMARY KEY (account_id, instrument_id, settle_date)
);
CREATE TABLE option_events (
  id TEXT PRIMARY KEY, idem_key TEXT NOT NULL UNIQUE,   -- 'optexp:{account}:{instrument}'
  kind TEXT NOT NULL,                      -- EXPIRE_WORTHLESS | EXERCISE | ASSIGN | CASH_SETTLE | EARLY_EXERCISE
  account_id TEXT NOT NULL, instrument_id TEXT NOT NULL, qty TEXT NOT NULL, underlying_px TEXT NOT NULL,
  at TEXT NOT NULL, journal_id INTEGER NOT NULL, fill_id TEXT
);
CREATE TABLE transfers (
  id TEXT PRIMARY KEY, request_id TEXT NOT NULL, from_account TEXT NOT NULL, to_account TEXT NOT NULL,
  ccy TEXT NOT NULL, amount TEXT NOT NULL, kind TEXT NOT NULL,   -- DEPOSIT | MANUAL | SWEEP_IN | SWEEP_OUT
  link_ref TEXT, at TEXT NOT NULL, journal_from INTEGER, journal_to INTEGER
);
CREATE TABLE jobs_done (idem_key TEXT PRIMARY KEY, job TEXT NOT NULL, run_key TEXT NOT NULL, request_id TEXT NOT NULL, at TEXT NOT NULL, result_json TEXT NOT NULL);
CREATE TABLE nav_snapshots (
  account_id TEXT NOT NULL, as_of_date TEXT NOT NULL, nav TEXT NOT NULL, cash_json TEXT NOT NULL,
  realised_itd TEXT NOT NULL, unrealised TEXT NOT NULL, income_itd TEXT NOT NULL, fees_itd TEXT NOT NULL, financing_itd TEXT NOT NULL,
  flows_itd TEXT NOT NULL, partial INTEGER NOT NULL DEFAULT 0, missing_json TEXT NOT NULL DEFAULT '[]', at TEXT NOT NULL,
  PRIMARY KEY (account_id, as_of_date)
);
CREATE TABLE attention (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL, severity TEXT NOT NULL,   -- error | warn | info
  message TEXT NOT NULL, ref_type TEXT, ref_id TEXT, acked_at TEXT
);
CREATE TABLE watchlists (id TEXT PRIMARY KEY, name TEXT NOT NULL, sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE watchlist_items (watchlist_id TEXT NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE, instrument_id TEXT NOT NULL,
  sort INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (watchlist_id, instrument_id));
CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
```

**Idempotency and no-duplicate guarantees, by index:** `commands.request_id` (a retried command returns the stored
result); `journal.idem_key` (one posting per economic event); `fills.idem_key` (one fill per order per bar); `settlements.fill_id`
/ `fx_deal_id` UNIQUE (one settlement per trade); `accruals.idem_key` and `borrow_marks` / `futures_settle` primary keys
(one accrual / mark / VM per reference per date); `option_events.idem_key` (one expiry outcome per position);
`jobs_done.idem_key` (one job run per key); `nav_snapshots` PK (one close per day). The initial capital posting uses
`idem_key = 'capital:initial'`.

---

## 4. Ledger and chart of accounts

### 4.1 Model
One double-entry ledger; every line carries `account_id`. Entries are balanced **in base currency** (USD, to the cent);
same-currency entries are therefore also balanced locally. Each account's sub-ledger balances on its own because inter-
account movements are posted as **two** entries (one per account) through the intercompany pair `1600` / `3100`.
The ledger is at **cost**: marks are never posted. `accounting.py` overlays the current marks when it builds the
balance sheet, NAV and unrealised P&L (§6). Foreign cash is carried at historical base amounts; outflows relieve the
carried base at the account/currency's **average carried rate**, the difference against the entry's spot goes to `4650`.

### 4.2 Chart (code → name; type)
```
ASSETS      1010 Cash (settled) [per ccy: 1010 + ccy column]      1200 Receivable – sales pending settlement
            1230 Accrued interest receivable – cash               1250 FX settlement receivable [per ccy]
            1300 Margin deposit – futures clearing                1400 Cash collateral posted – stock borrow
            1410 Accrued rebate receivable                        1100 Investments – long securities at cost
            1700 Options purchased at cost                        1600 Advances to accounts (Treasury only)
LIABILITIES 2100 Payable – purchases pending settlement           2310 Accrued borrow fees payable
            2350 FX settlement payable [per ccy]                  2600 Securities sold short (at proceeds)
            2700 PB margin financing                              2750 Currency loans – principal [per ccy]
            2755 Accrued interest payable – currency loans        2900 Options written (at premium received)
EQUITY      3000 Contributed capital (Treasury only)              3100 Funding from Treasury (non-Treasury accounts)
INCOME      4000 Realised gain/loss – securities & options        4300 Interest income (cash)
            4350 Rebate income – cash collateral                  4400 Futures variation margin
            4650 Realised FX gain/loss
EXPENSE     5000 Commissions & fees                               5150 Currency loan interest
            5300 Securities borrow fees                           5600 PB financing interest
Build 2:    1220/4200 bond accrued & coupons, 4200 dividends, 2320/5400 manufactured dividends, 1500/2500 repo, 1800/2800 OTC PV, 4900/4910 OTC
```

### 4.3 Posting templates (the only places that post; each gives its `idem_key`)
| Event | Entry (account where it happens) | idem_key |
|---|---|---|
| Initial capital (startup, once) | TREASURY: Dr 1010:USD 10,000,000 / Cr 3000 | `capital:initial` |
| Deposit | TREASURY: Dr 1010:ccy / Cr 3000 | `deposit:{request_id}` |
| Transfer / sweep-in (Treasury → A, amount X) | TREASURY: Dr 1600 X / Cr 1010 X ; A: Dr 1010 X / Cr 3100 X | `xfer:{transfer_id}:from`, `xfer:{transfer_id}:to` |
| Sweep-out (A → Treasury) | the reverse pair | same pattern |
| Equity/ETF/option buy (fill) | Dr 1100 (or 1700) gross ; Dr 5000 fee ; Cr 2100 gross+fee | `fill:{fill_id}` |
| Equity sell (long relief) | Dr 1200 gross−fee ; Dr 5000 fee ; Cr 1100 cost relieved ; Cr/Dr 4000 realised | `fill:{fill_id}` |
| Short sale | Dr 1200 gross−fee ; Dr 5000 fee ; Cr 2600 gross | `fill:{fill_id}` |
| Cover | Dr 2600 proceeds relieved ; Dr/Cr 4000 realised ; Dr 5000 fee ; Cr 2100 gross+fee | `fill:{fill_id}` |
| Option write (STO) | Dr 1200 premium−fee ; Dr 5000 fee ; Cr 2900 premium | `fill:{fill_id}` |
| Option close (STC/BTC) | long: Dr 1200 ; Cr 1700 cost ; 4000 diff. short: Dr 2900 ; Cr 2100 ; 4000 diff | `fill:{fill_id}` |
| Settlement of a fill (T+n) | receivable: Dr 1010 / Cr 1200 ; payable: Dr 2100 / Cr 1010 | `settle:{fill_id}` |
| Futures fill | Dr 5000 fee / Cr 1010 fee (same day; no cost posting) | `fill:{fill_id}` |
| Futures initial margin post/release | Dr 1300 / Cr 1010 (post) ; reverse (release) | `margin:{account}:{date}:{seq}` |
| Futures VM | gain: Dr 1010 / Cr 4400 ; loss: Dr 4400 / Cr 1010 | `vm:{account}:{instrument}:{date}` |
| Borrow collateral post / mark | Dr 1400 / Cr 1010 (more collateral) ; reverse (excess back) | `collat:{borrow_id}:open`, `cmark:{borrow_id}:{date}` |
| Borrow fee accrual (daily) | Dr 5300 / Cr 2310 | `accr:bfee:{borrow_id}:{date}` |
| Rebate accrual (daily) | Dr 1410 / Cr 4350 | `accr:rebate:{borrow_id}:{date}` |
| Borrow fee / rebate settlement (monthly 1st, or on return) | Dr 2310 / Cr 1010 ; Dr 1010 / Cr 1410 | `pay:bfee:{borrow_id}:{yyyy-mm|return}` |
| Treasury cash interest accrual (daily) | TREASURY: Dr 1230 / Cr 4300 | `accr:cashint:TREASURY:{ccy}:{date}` |
| Cash interest paid (monthly 1st) | Dr 1010 / Cr 1230 | `pay:cashint:{ccy}:{yyyy-mm}` |
| Currency loan draw | Dr 1010:ccy / Cr 2750:ccy (base at spot mid) | `loan:{loan_id}:draw` |
| Loan interest accrual (daily) | Dr 5150 / Cr 2755:ccy (base at the day's mid) | `accr:loan:{loan_id}:{date}` |
| Loan repayment | Dr 2755 accrued ; Dr 2750 principal ; Cr 1010:ccy (relieved at carried avg; diff → 4650) | `loan:{loan_id}:repay:{request_id}` |
| FX spot deal | Dr 1250:buy_ccy / Cr 2350:sell_ccy (both at the deal rate's base) | `fx:{deal_id}` |
| FX settlement (T+2) | Dr 1010:buy / Cr 1250:buy ; Dr 2350:sell / Cr 1010:sell (carried avg; diff → 4650) | `settle:fx:{deal_id}` |
| Option expiry worthless | long: Dr 4000 / Cr 1700 ; short: Dr 2900 / Cr 4000 | `optexp:{account}:{instrument}` |
| Long call exercise | Dr 1100 strike×qty×100 + premium ; Cr 1700 premium ; Cr 2100 strike×qty×100 | same key |
| Long put exercise | Dr 1200 strike×qty×100 ; Cr 1100 cost relieved (FIFO) ; Cr 1700 premium ; 4000 realised = strike×qty×100 − cost − premium | same key |
| Short call assignment | Dr 1200 strike proceeds ; Dr 2900 premium ; Cr 1100 cost ; 4000 realised = strike + premium − cost | same key |
| Short put assignment | Dr 1100 strike×qty×100 − premium ; Dr 2900 premium ; Cr 2100 strike×qty×100 | same key |
| Index option cash settlement | long: Dr 1200 intrinsic ; Cr 1700 cost ; 4000 diff. short: Dr 2900 ; Cr 2100 intrinsic ; 4000 diff | same key |
| Futures expiry | final VM at the observed settle (same as VM), position → 0, margin released | `vm:…:{date}`, `margin:…` |

Exercise/assignment are commission-free (disclosed). The premium of an exercised/assigned contract rolls into the
stock's cost basis or proceeds (broker convention, disclosed in the preview and glossary).

---

## 5. CommandBus contract

```python
def execute(self, cmd, timeout=10.0) -> CommandResult:
    if not self.writer_lock.acquire(timeout=timeout): raise WriterBusy  # -> HTTP 503 after 10 s (never seen in practice)
    try:
        conn = self.db.writer(); conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT params_hash, result_json FROM commands WHERE request_id=?", (cmd.request_id,)).fetchone()
            if row:
                conn.execute("ROLLBACK")
                if row[0] != hash_of(cmd.params): raise ConflictError("REQUEST_ID_REUSED", ...)
                return CommandResult(cmd.request_id, json.loads(row[1]), replayed=True, seq=None)
            tx = Tx(conn, self.clock, self.cal, cmd.request_id, self.clock.now_utc())
            conn.execute("INSERT INTO commands(...) VALUES (?, ?, ?, ?, '{}', ?, ?, ?)", ...)   # inside the tx
            result = cmd.execute(tx)                                  # may raise CommandError (clean rollback, 400)
            fast_invariants(tx, cmd.touched_accounts())              # positions == lots; entries balanced (cheap, SQL)
            conn.execute("UPDATE commands SET result_json=?, finished_at=? WHERE request_id=?", ...)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK"); raise
    finally:
        self.writer_lock.release()
    self.state_seq += 1; self.after_commit(cmd, result)              # outside the lock: attention/notify only
    return CommandResult(cmd.request_id, result, replayed=False, seq=self.state_seq)
```

Rules:
1. **One transaction per command**; the `commands` row is written inside it, so a failed command leaves **no** trace and
   a retry with the same `request_id` runs afresh. A succeeded command replays its stored result (HTTP 200,
   `replayed: true`) when the same `request_id` arrives again with the same `params_hash`; a different payload under a
   known id is `409 REQUEST_ID_REUSED`.
2. Commands are **pure functions of (database, params, clock)**. No network inside the bus: the quote snapshot a
   matching pass uses is a parameter (`MatchOrders(snapshot=cache.snapshot(ids))`), and the preview the user saw is
   referenced by `preview_id` (the server keeps the last 200 previews in memory for 120 s).
3. **Errors surface, never swallow.** `CommandError` → HTTP 400 `{error:{code,message,reasons}}`; `ConflictError` →
   409; anything else → ROLLBACK, `log.exception`, HTTP 500 `{error:{code:"INTERNAL", incident_id}}` and an
   `attention` row (severity error) written by a follow-up `RecordAttention` command (if that fails too, the item lives
   in the in-memory ring buffer that `/api/health` also reports).
4. **Background callers** (QuoteWorker, LifecycleWorker) call the same `execute()`. A job failure is logged, recorded as
   attention, and retried on the next tick with backoff (1, 2, 4, 8, 16 min; then hourly) — the job key stays due until
   it succeeds. Workers never catch-and-continue silently: every failure is visible in `/api/health.workers` and the
   Attention strip.
5. `request_id`: client-generated UUID v4 (the UI keeps it across retries of the same click); workers derive
   deterministic ids (`job:{job}:{key}`, `marks:{cycle_ts}`), so a crashed-and-restarted worker cannot double-post.
6. Reads never take the writer lock. WAL gives readers a consistent snapshot; a read model computed mid-command sees
   the previous committed state.

---

## 6. Accounting read models (`accounting.py`) and `integrity()`

All figures come from these functions and nowhere else (the UI formats, it never computes). `snap` is a `QuoteCache`
snapshot; `mark(instrument)` returns the last valid mark of any quality except `unavailable`, with its quality so the
caller can label it.

| Name | Definition |
|---|---|
| `settled_cash[a][c]` | Σ 1010 (local) for account a, currency c |
| `pending_cash[a][c]` | Σ 1200 − Σ 2100 + Σ 1250 − Σ 2350 (local): receivables not yet settled minus payables not yet settled |
| `restricted_cash[a]` | 0 in Build 1 by construction: a short's proceeds-equivalent sits at the lender as 102 % cash collateral (`1400`), shown as "held at lender", not spendable (§11.2) |
| `reserved[a][c]` | open BUY/BTO orders' estimated net cash (limit price, else last ask/last × 1.01) + option margin requirement (§11.3) not yet backed by posted cash |
| `treasury_available[c]` | Treasury settled cash − Treasury's own pending payables − `sweep_buffer` (Treasury setting; default 0) |
| `available[a][c]` | settled + pending − reserved (the account's own spendable cash at the next settlement) |
| `buying_power[a][c]` | max(0, available) + `treasury_available[c]` + PB financing headroom (PB accounts with `pb_financing=1`: `financing_limit − 2700 balance`); the preview shows each component |
| `cost_basis` (long) | Σ open LONG lots `open_qty × cost_per_unit` (local); options × multiplier; short basis = Σ open SHORT lots proceeds |
| `market_value` | long/short securities: qty × mark × multiplier (negative for shorts); options: contracts × mark × 100 (sign by side); futures: **0** (notional shown separately = qty × mark × multiplier); FX cash: local × spot |
| `unrealised` | securities/options: MV − cost basis (shorts: proceeds − |MV|); futures: Σ qty × (mark − last settle_px) × multiplier ("open trade equity" since the last VM); foreign cash: local × spot − carried base; loans: carried base − local × spot |
| `realised(period)` | Σ 4000 + 4400 + 4650 lines with value_date in the period |
| `income(period)` | Σ 4300 + 4350 (+ 4200 Build 2) ; `fees` = Σ 5000 ; `financing` = Σ 5150 + 5300 + 5600 |
| `assets[a]` | settled cash (base, at current spot) + 1200 + 1250 + 1230 + 1300 + 1400 + 1410 + long securities at market + long options at market + Treasury: 1600 |
| `liabilities[a]` | 2100 + 2350 + 2310 + 2700 + 2750 at spot + 2755 at spot + shorts at market + written options at market |
| `nav[a]` | assets − liabilities (per account; 3100 funding is equity). `nav["ALL"]` = Σ nav[a] − Σ 1600 (advances eliminated) = Treasury cash + Treasury accrued + Σ non-Treasury nav |
| `equity` | ≡ NAV (shown once; "margin excess" = NAV − maintenance requirements for margined accounts) |
| `pnl(a, period)` | NAV_now − NAV at the period's opening snapshot − net transfers/deposits into a in the period; shown with the decomposition realised + Δunrealised + income − fees − financing (must equal; the integrity check asserts it to the cent for ITD) |
| `treasury()` | capital = Σ 3000; retained = Σ 4xxx − Σ 5xxx in TREASURY; advances by account = Σ 1600 per `instrument_id` dimension (the to-account id is stored in `journal_lines.instrument_id` for 1600 lines); cash = capital + retained − advances; deposits, sweeps and interest logs |

Period snapshots: `nav_snapshots` is written by the `nav_snapshot` job at 16:35 NY every business day using
`close_marks`; TODAY compares with the latest snapshot before today; MTD/YTD with the last snapshot of the previous
month/year; ITD with inception (NAV 0 before the initial capital, so ITD P&L = NAV − capital − net flows).

### 6.1 `integrity()` — runs after every command batch (fast subset) and on startup / `GET /api/accounting/integrity` (full)
| # | Check | How |
|---|---|---|
| 1 | every entry balances | `SELECT journal_id FROM journal_lines GROUP BY journal_id HAVING SUM(debit_base) <> SUM(credit_base)` is empty |
| 2 | cash reconciles | `cash()` figures are sums of 1010/1200/2100/1250/2350 — the check asserts `settled + pending` equals the same sums computed from `fills`+`settlements`+`transfers`+`fx_deals` rows (projection vs ledger) |
| 3 | positions = fills ± events | per (account, instrument): `positions.qty` = Σ signed fill qty − exercised/assigned/expired qty from `option_events` (+ futures expiries) |
| 4 | lots = positions | Σ open lot qty (signed) = positions.qty; 1100 balance per instrument = Σ open LONG lot cost; 2600 = Σ open SHORT lot proceeds; 1700 / 2900 likewise for options |
| 5 | NAV = assets − liabilities | the balance sheet's assets − liabilities − (3000 + 3100 + net income) = 0 per account and consolidated (trial balance + mark overlay) |
| 6 | no duplicates | `fills.idem_key`, `journal.idem_key`, `accruals`, `futures_settle`, `borrow_marks`, `option_events`, `jobs_done` are unique (DB-enforced; the check counts rows vs distinct keys for belt-and-braces) |
| 7 | Treasury = capital + retained − advances | to the cent |
| 8 | every short has a live borrow | per (account, instrument) with qty < 0: Σ open borrow qty ≥ |qty| |
| 9 | 1300 = futures margin required | after the daily margin sweep (intraday differences are reported as info, not failure) |
| 10 | 1400 = Σ borrows.collateral | and each borrow's collateral = 102 % × last mark × qty as of the last mark date |
| 11 | 2750 = Σ loans.outstanding | per currency |
| 12 | settlements consistent | every SETTLED row has a journal; no PENDING row with due_date < today − 5 business days (would mean the job is stuck) |
| 13 | P&L decomposition | ITD: NAV − capital − net flows = realised + unrealised + income − fees − financing |

A failed check → `attention` (severity error, kind INTEGRITY) + `health.integrity.ok=false`; the UI shows a red strip
with the check name and detail. The app never continues silently.

---

## 7. Instruments, symbol mapping, quotes, freshness

### 7.1 Instrument master
Seeded from the old `finsim/finsim/data/universe.json` (equities/ETFs/ADRs: ticker, name, currency, Yahoo symbol) for the
typeahead; **any other Yahoo symbol** is added on demand through `GET /api/instruments/resolve?symbol=…`, which fetches
the v8 chart meta (currency, exchange, instrumentType, exchangeTimezoneName, currentTradingPeriod) and creates the row.
Instrument ids and mappings:

| Class | id | yahoo_symbol | cboe_symbol | session_group | settle_cycle |
|---|---|---|---|---|---|
| EQUITY / ETF (US) | ticker in Yahoo form (`BRK-B`) | same | ticker with `.` (`BRK.B`) | `US_EQUITY` | 1 |
| OPTION (equity/ETF) | OCC symbol `AAPL261120C00340000` | — (not quoted by Yahoo here) | underlying's cboe symbol | `US_EQ_OPTIONS` | 1 |
| OPTION (index) | OCC `SPX…`, `SPXW…` | — | `_SPX` | `US_INDEX_OPTIONS` (09:30–16:15) | 1 |
| INDEX (reference only) | `SPX`, `NDX`, `RUT`, `VIX`, `DXY` | `^GSPC`, `^NDX`, `^RUT`, `^VIX`, `DX-Y.NYB` | `_SPX`… | per index | — |
| FUTURE | `ESZ26` (root + month code + yy) | `ESZ26.CME`, `CLX26.NYM`, `GCZ26.CMX`, `ZNZ26.CBT`, `6EZ26.CME`, `ZCZ26.CBT` | — | §8 group | 0 (margined) |
| FX | `EURUSD`, `USDJPY` | `EURUSD=X`, `JPY=X` (inverted; `spec_json.invert=true`) | — | `FX_SPOT` | 2 |
| Foreign listing (Build 2) | Yahoo symbol (`SAP.DE`, `0700.HK`) | same | — | `FOREIGN:{exchange}` from meta | per market table |
| BOND (Build 2) | `UST-10Y-2036-08-15` | — (curve-priced) | — | `US_BONDS` | 1 |

Yahoo exchange suffix for futures from the root: CME (ES NQ YM RTY 6E 6J 6B), CBT (ZB ZN ZF ZT ZC ZW ZS), NYM (CL BZ NG), CMX
(GC SI HG). Month codes F G H J K M N Q U V X Z. The probe verified `CLX26.NYM` and `ES=F`; per-contract symbols for
the other roots follow the same form (**assumption**, checked at resolve time — a symbol Yahoo rejects is marked
`support=UNSUPPORTED` with the reason, never priced).

### 7.2 Providers (`providers/`)
- `http.get(url, *, ua, timeout, accept) -> bytes` — UA policy: `.gov` hosts get `LoganTerminal/1.0 (loganshaffer87@gmail.com)`;
  Yahoo gets the browser UA; Cboe and Frankfurter get `FinSimTerminal/1.0 (paper trading; stdlib urllib)`. gzip, 64 MB
  cap, timeout 10 s (Treasury.gov 30 s), **retries: 2** with 1 s / 3 s backoff on 429/5xx/timeout; **circuit breaker**
  per host: 3 consecutive failures → paused 60 s (then 120, 300 s), state visible in health. No crumb, no cookie jar.
- `yahoo.spark(symbols) -> {sym: {price, time, prev_close, day_high, day_low, volume, currency}}` in batches of 20
  (0.2 s between batches). `yahoo.chart_1m(symbol) -> (meta, bars)` v8 `range=1d&interval=1m`. `yahoo.resolve(symbol) -> meta`.
- `cboe.delayed_quotes(cboe_symbol) -> Chain` reduced to `{underlying:{price,bid,ask,sizes,last_trade_time,iv30}, contracts:[{occ, expiry, right, strike, bid, ask, bid_size, ask_size, iv, delta, gamma, theta, vega, oi, volume, last, last_time}]}`
  filtered to expiries ≤ 400 days and strikes within ±35 % of spot at parse time; one underlying at a time; cache 90 s
  (Cboe is ~15 min delayed, so 90 s is fine); ≥ 1.0 s between requests; LRU of 3 chains.
- `treasury_gov.par_curve(year_month) -> {date: {tenor: yield}}` once per day (cache for the day; 16 s fetch runs in the worker).
- `fred.series(id) -> [(date, value)]` for SOFR, DGS1MO…DGS30, ECBDFR, IUDSOIA, BAMLC0A0CM, BAMLH0A0HYM2; refreshed every 6 h.
- `frankfurter.latest(base="USD")` fallback FX mid only when Yahoo has no FX quote for a currency (labelled `close`, "ECB reference").
- `fake.FakeProvider(quotes, chains, rates, clock)` for tests and the `--offline` serve flag.

Rate limits (self-imposed): Yahoo ≤ 4 req/s and ≤ 300 subscribed symbols; Cboe ≤ 1 req/s; FRED ≤ 1 req/s; SEC not used
in Build 1 (shares outstanding for 13D/G notices is Build 2, weekly cache).

### 7.3 Quote record (`QuoteRecord`, in memory; persisted to `marks` on change, at most once per minute per instrument)
```
instrument_id, price, bid, ask, bid_size, ask_size, prev_close, day_high, day_low, day_volume,
bar_ts, bar_volume (latest 1-minute bar when the chart endpoint was used; else null),
quote_time (provider timestamp), fetched_at, source ('yahoo'|'cboe'|'treasury.gov'|'fred'|'ecb'|'model'),
delay_label ('≤15 min' | '~15 min' | 'daily' | 'prior business day' | 'computed'), method ('direct'|'nbbo'|'curve'|'cip'|'bsm'),
quality (stored at update; recomputed at read), note
```
Executable price basis: Cboe NBBO (ask for buys, bid for sells) when a fresh chain for that underlying is cached (the
instrument is being viewed or has a working option order); otherwise Yahoo last trade. The basis used is recorded in
`fills.quote_json.price_basis` and shown in the preview.

### 7.4 Freshness (`classify(rec, session, now)`)
```
if rec is None or rec.price is None:                       -> 'unavailable'
if rec.source == 'model':                                   -> 'computed'
if session.state != 'OPEN' (CLOSED or BREAK):               -> 'close'   (last regular-session mark, shown with quote_time)
delay = {'yahoo': 15 min, 'cboe': 15 min}[rec.source]
if now - rec.quote_time > delay + 5 min:                    -> 'stale'
if now - rec.fetched_at > 5 min:                            -> 'stale'   (nothing fetched for 5 min while open)
if delay <= 1 min:                                          -> 'live'    (no such source today; reserved)
else                                                        -> 'delayed'
```
`executable()` is true only for `live` / `delayed` with an OPEN session. Marks for valuation use the last record of any
quality except `unavailable` (a `stale`/`close` mark is displayed as such). Nothing is ever substituted or invented:
an instrument with no mark shows "unavailable" in the UI and is excluded from NAV with a visible `partial` flag.

Subscriptions: positions + working orders + watchlist items + viewed instruments (90 s TTL after the last UI poll) +
the hedge candidates' legs while the ticket is open. Cycle: 10 s while any subscribed session is OPEN (chart v8 for
working-order instruments and the viewed instrument; spark for the rest), 60 s otherwise.

---

## 8. Sessions and calendar (`tables.SESSIONS`, NY time)

Holidays and 13:00 early closes from `data/nyse-holidays.json` (2026–2028); `finsim.calendar.us_equity_holidays`
generates the rule-based set for years the file does not cover (logged as "rule-based" in `/api/clock`).

| group | regular session (Mon–Fri unless noted) | daily break | early-close days | settle time (futures) |
|---|---|---|---|---|
| `US_EQUITY` | 09:30–16:00 | — | 13:00 | — |
| `US_EQ_OPTIONS` | 09:30–16:00 | — | 13:00 | — |
| `US_INDEX_OPTIONS` | 09:30–16:15 | — | 13:15 | — |
| `US_BONDS` (Build 2) | 08:00–17:00 | — | 14:00 (SIFMA; assumption: same dates as NYSE early closes) | — |
| `CME_EQUITY_INDEX` (ES NQ YM RTY) | Sun 18:00 → Fri 17:00 | 17:00–18:00 | NYSE holidays treated as **closed** (conservative; CME holiday hours not modelled, disclosed) | 16:00 |
| `CBOT_RATES` (ZB ZN ZF ZT) | Sun 18:00 → Fri 17:00 | 17:00–18:00 | same | 15:00 |
| `NYMEX_ENERGY` (CL BZ NG) | Sun 18:00 → Fri 17:00 | 17:00–18:00 | same | 14:30 |
| `COMEX_METALS` (GC SI HG) | Sun 18:00 → Fri 17:00 | 17:00–18:00 | same | 13:30 |
| `CBOT_GRAINS` (ZC ZW ZS) | Sun 20:00 → Fri 14:20 | 08:45–09:30 and 14:20–20:00 | same | 14:15 |
| `CME_FX` (6E 6J 6B) | Sun 18:00 → Fri 17:00 | 17:00–18:00 | same | 15:00 |
| `FX_SPOT` | Sun 17:00 → Fri 17:00 continuous | — | — | — |
| `FOREIGN:{exchange}` (Build 2) | from Yahoo meta `currentTradingPeriod.regular` in `exchangeTimezoneName` | — | — | — |

Rules: orders entered while a group is CLOSED/BREAK rest until it opens (MARKET only with the explicit `moo` flag);
DAY orders expire at that group's close; GTC rest 90 days. Settlement dates use the NYSE business calendar for all
US products; futures have no DVP settlement (variation margin daily at the group's settle time, using the observed
price at/after that time from the 1-minute bars, labelled "observed price used as settlement proxy").

---

## 9. Orders

### 9.1 Lifecycle
`ACCEPTED` → `WORKING` (resting) / `PARTIALLY_FILLED` → `FILLED` | `CANCELLED` | `EXPIRED`; `REJECTED` at submit;
`WAITING_PARENT` for a hedge leg until its parent fills (then sized `parent.filled_qty × ratio`, rounded to the lot).
Stop orders carry `triggered=0/1`. Every transition writes an `events` row (`ORDER_ACCEPTED`, `ORDER_WORKING`,
`ORDER_FILLED`, `ORDER_PARTIAL`, `ORDER_CANCELLED`, `ORDER_EXPIRED`, `ORDER_REJECTED`, `ORDER_TRIGGERED`).

### 9.2 Validation at preview and submit (blocks → `REJECTED` / HTTP 400 `BLOCKED`)
| code | condition |
|---|---|
| `UNSUPPORTED` | instrument `support != FULL`, or product not tradeable in this account (e.g. futures in CREDIT) |
| `INVALID_QTY` | qty ≤ 0, not a multiple of `lot`, option/futures qty not whole |
| `LIMIT_REQUIRED` / `STOP_REQUIRED` | type needs a price; limit/stop not on the tick grid |
| `SESSION_CLOSED` | MARKET order while the group is not OPEN and `moo=false` |
| `QUOTE_STALE` / `QUOTE_UNAVAILABLE` | MARKET order (or marketable LIMIT at submit) while `executable()` is false |
| `EXPIRED` | option/future past its last trade; option expiring today after 15:30 (no new opens) |
| `NO_BORROW` | SELL_SHORT with no locate/borrow available for the quantity (`credit.locate()` says 0 or tier NONE) |
| `INSUFFICIENT_FUNDS` | `funding_plan().ok` is false (after settled cash, Treasury sweep and PB financing) |
| `NO_POSITION` | SELL/STC/BTC larger than the position (a SELL that would flip short must be entered as SELL_SHORT) |
| `PREVIEW_STALE` (409 at submit) | the executable price moved > 0.5 % from the preview, or the preview expired (120 s); the response carries the fresh preview |
Risk warnings and compliance notices never block (§13).

### 9.3 Matching (`MatchOrders(snapshot)`; runs after every quote cycle and immediately inside `SubmitOrder`)
For each working order whose instrument has `executable()` true and session OPEN (or any session for resting checks):
```
px_buy  = ask if nbbo fresh else last ; px_sell = bid if nbbo fresh else last ; last = snapshot.price
MARKET:      fill at px_side
LIMIT:       buy: if px_buy <= limit  fill at min(px_buy, limit)  else rest; sell: if px_sell >= limit fill at max(px_sell, limit)
             resting (not marketable at entry): fills at the LIMIT price when last crosses it (buy: last <= limit; sell: last >= limit)
STOP:        trigger when last >= stop (buy) / last <= stop (sell) -> then MARKET
STOP_LIMIT:  trigger as STOP -> then LIMIT
partial fills: if snapshot.bar_volume is known and > 0 and bar_ts != order.last_bar_ts:
                 cap = max(lot, floor(participation * bar_volume / lot) * lot); fill min(remaining, cap); order.last_bar_ts = bar_ts
               else: fill remaining in full (disclosed in fills.quote_json.cap = "no volume information")
options:     cap by the chain's displayed size at the touch (bid_size for sells, ask_size for buys, ×1 contract); no size → full
futures:     cap by 10 % of the 1-minute bar volume when known; else full
```
A fill posts the journal template (§4.3), creates/relieves lots FIFO (shorts: cover FIFO against SHORT lots), updates
`positions`, inserts the `settlements` row (equities/options: T+1; FX: T+2; futures: none), computes the fee (§11.1),
executes the **funding plan** (sweep-in exactly the shortfall as a linked transfer before the cost posting; if the plan
fails at fill time because Treasury cash changed, the order is `REJECTED` with `INSUFFICIENT_FUNDS` and an attention
item — never a negative cash balance), and for shorts opens the borrow leg **first** in the same transaction (if the
borrow cannot be opened the order is rejected, nothing is posted). Package legs: when a parent fills, dependent legs
move `WAITING_PARENT → WORKING` with their resized qty and are matched in the same pass (sequential; a collar's call leg
depends on the put leg, which depends on the primary).

### 9.4 Expiry
`day_order_expiry` job at each group's close (+1 min, key `dayexp:{order_id}`), `gtc_expiry` at 90 days
(`gtcexp:{order_id}`); both record the reason. Cancels are user commands (`CancelOrder`); a partially filled order
keeps its fills.

---

## 10. Funding: deposits, transfers, sweeps (`funding.py`)

- **Deposit** (Treasury only): `Deposit{ccy, amount, memo}` → 3000. Never P&L. UI: Treasury page → "Add capital".
- **Transfer** (manual): Treasury ↔ account, any currency the Treasury holds; account → account goes via Treasury (two
  transfers) so advances stay explainable.
- **Sweep-in** (automatic, at fill / collateral post / margin post / settlement shortfall): `funding_plan()` computes
  `need − available` in the trade's currency; if Treasury holds that currency, exactly the shortfall moves as one
  transfer `kind=SWEEP_IN, link_ref=order_id|borrow_id|settle_id`, visible in Transactions. If Treasury lacks the
  currency, the preview offers an **FX conversion leg** (Treasury USD → ccy at the Yahoo mid ± spread, T+2 — for a T+1
  need the preview says so and the trade is refused unless the user converts earlier or holds the currency; disclosed).
  Order of funding: own settled cash → Treasury sweep → PB financing (PB accounts, switch on) → refuse with the reason.
- **Sweep-out** (`sweep_out` job, 16:30 NY once per calendar day, key `sweepout:{account}:{date}`): for each account
  with `sweep_out=1`: idle = settled − pending payables due within 2 business days − option margin requirement −
  futures margin shortfall − next-day collateral headroom (2 % of short MV) − `sweep_buffer`; if idle > 0 move it to
  Treasury as `SWEEP_OUT`. Never sweeps a currency into negative.
- **PB financing** (PB accounts): a 2700 draw happens only when `pb_financing=1` and the Treasury sweep cannot cover;
  interest SOFR + PB spread accrues daily (`accr:pbfin:{account}:{date}`), swept down by sweep-out first.

---

## 11. Product modules, lifecycle jobs, fee and rate tables

### 11.1 Fees (`tables.FEES`, disclosed in every preview with `fee_basis`)
| product | fee |
|---|---|
| US equities / ETFs | USD 0.005 per share, minimum USD 1.00 per fill |
| listed options | USD 0.65 per contract per fill; exercise/assignment free |
| futures | USD 2.25 per contract per side |
| FX spot | no commission; dealt at mid ± 0.1 pip (full spread 0.2 pip; pip = 0.0001, JPY pairs 0.01); the cost vs mid is shown and lands in 4650 at settlement |
| bonds (Build 2) | USD 0.02 per 100 face, min USD 5.00 |
| PB accounts | the broker's schedule (§11.6) replaces the equity/option/futures rows |

### 11.2 Rates (`tables.RATES`; indices from FRED, observation date shown)
| item | rate | day count | accrual |
|---|---|---|---|
| idle Treasury cash (USD) | SOFR − 0.50 % (floor 0) | ACT/360 | daily on settled 1010:USD ≥ 0; paid monthly |
| PB margin financing | SOFR + 1.50 % (or the PB schedule) | ACT/360 | daily on 2700 |
| stock borrow, general collateral | 0.30 % on market value; HTB tiers: `GC 0.30 %`, `WARM 1.00 %`, `HARD 5.00 %`, `NONE` (no borrow) from `tables.HTB` seeded with a short static list and (Build 2) FINRA daily short-volume ratio > 60 % → WARM | ACT/360 | daily on last mark × qty |
| rebate on posted cash collateral | SOFR − 0.50 % (floor 0) | ACT/360 | daily on collateral |
| currency loans | policy index + 0.75 %: USD SOFR; EUR ECBDFR; GBP IUDSOIA; JPY `IRSTCI01JPM156N` (monthly, labelled); other currencies unsupported | ACT/360 (all, disclosed) | daily on outstanding; paid at repayment |
| repo (Build 2) | SOFR ± spread table, haircuts 2 % UST / 5 % IG / 10 % HY | ACT/360 | daily |

**Short-sale collateral convention (decision for this build):** a borrow requires cash collateral of **102 % of the
borrowed shares' market value**, posted from the shorting account's settled cash (Treasury sweep covers a shortfall)
when the borrow opens, and **marked daily both ways** at the close (`cmark:{borrow}:{date}`: price up → the account
posts the increase; price down → the excess returns). The sale proceeds settle T+1 into the account as ordinary
settled cash. Net effect after T+1 = the real-world one: 102 % of the short's value is held at the lender and is not
spendable until the borrow is returned; the account's own cash is down by 2 % plus fees. "Short proceeds restricted
until covered" is implemented by this construction (the UI line reads "held at lender (102 % collateral)"). Fees accrue
from the trade date inclusive; daily amounts are rounded to the cent (HALF_EVEN) per day.

### 11.3 Options (`products/options.py`)
Contracts come from the Cboe chain (OCC symbol parsed: root, yymmdd, C/P, strike/1000); equity/ETF options are
AMERICAN/PHYSICAL (100 shares), index options (`SPX`, `SPXW`, `NDX`, `RUT`) EUROPEAN/CASH. Sides BTO/STO/BTC/STC. Margin
for written options (Reg T style, **requirement**, reserved in buying power and kept as settled cash by sweep-in; not a
ledger movement): naked call per contract = max(premium + 20 % × S × 100 − OTM × 100, premium + 10 % × S × 100); naked put
= max(premium + 20 % × S × 100 − OTM × 100, premium + 10 % × K × 100); index options 15 %/10 %; vertical spreads = width
× 100 per spread; covered calls 0 (shares reserved; cannot be sold while the call is open). Expiry job
(`option_expiry`, after 16:00 / 16:15 on the expiry date, key `optexp:{account}:{instrument}`): underlying close from
`close_marks` (Yahoo `regularMarketPrice` captured after the close with quality `close`); ITM by ≥ USD 0.01 → exercise
(long) / assignment (short) per §4.3, else worthless. If the close is not available, the job **waits** (attention
"expiry pending: no observed close for X") and retries hourly — never a guess. Early exercise is a user command
(`ExerciseOption{position, qty}`, long American only, uses the live underlying mark as the deliverable price basis
is the strike anyway). Short options are never randomly assigned.

### 11.4 Futures (`products/futures.py`, `tables.FUTURES`)
| root | name | multiplier (USD per quoted point) | tick | group | IM (USD/contract) **assumed; verify vs CME** | last trade rule |
|---|---|---|---|---|---|---|
| ES | E-mini S&P 500 | 50 | 0.25 | CME_EQUITY_INDEX | 15,000 | 3rd Friday of H/M/U/Z, 09:30 |
| NQ | E-mini Nasdaq-100 | 20 | 0.25 | CME_EQUITY_INDEX | 22,000 | same |
| YM | E-mini Dow | 5 | 1 | CME_EQUITY_INDEX | 9,000 | same |
| RTY | E-mini Russell 2000 | 50 | 0.10 | CME_EQUITY_INDEX | 7,000 | same |
| CL | WTI crude | 1,000 | 0.01 | NYMEX_ENERGY | 6,500 | 3 business days before the 25th of the month before delivery |
| BZ | Brent (last day) | 1,000 | 0.01 | NYMEX_ENERGY | 6,500 | last business day of the 2nd month before delivery |
| NG | Henry Hub | 10,000 | 0.001 | NYMEX_ENERGY | 4,000 | 3 business days before the 1st of the delivery month |
| GC | Gold | 100 | 0.10 | COMEX_METALS | 10,000 | 3rd last business day of the delivery month |
| SI | Silver | 5,000 | 0.005 | COMEX_METALS | 10,000 | same |
| HG | Copper | 25,000 | 0.0005 | COMEX_METALS | 6,000 | same |
| ZB / ZN / ZF / ZT | T-Bond / 10Y / 5Y / 2Y | 1,000 / 1,000 / 1,000 / 2,000 | 1/32 / 1/64 / 1/128 / 1/128 | CBOT_RATES | 4,500 / 2,500 / 1,500 / 1,000 | ZB/ZN: 7 business days before month end; ZF/ZT: last business day of the month |
| 6E / 6J / 6B | EUR / JPY / GBP | 125,000 / 12,500,000 / 62,500 | 0.00005 / 0.0000005 / 0.0001 | CME_FX | 3,000 / 2,500 / 2,500 | 2 business days before the 3rd Wednesday |
| ZC / ZW / ZS | Corn / Wheat / Soybeans (quoted in cents/bu) | 50 / 50 / 50 (USD per cent) | 0.25 | CBOT_GRAINS | 1,500 / 2,000 / 2,500 | business day before the 15th of the contract month |
Maintenance margin = 90 % of IM (assumption). Per account: `required = Σ |qty| × IM` (no spread credits in Build 1);
the `futures_margin` job (after each group's settle time, key `margin:{account}:{date}:{group}`) tops up or releases 1300
to `required` (sweep-in covers a top-up). VM job (`futures_vm`, key `vm:{account}:{instrument}:{date}`): settle_px =
the last 1-minute bar at/after the group's settle time from the chart (quality `close`, "settlement proxy"); VM =
qty_carried × (settle − prev settle) × mult + Σ today's fills qty × (settle − fill px) × mult; posts 4400. Expiry
(`futures_expiry`): final VM at the observed settle, position → 0, margin released. Contracts listed: the front 4 for
each root (resolved on demand via Yahoo; a symbol Yahoo rejects is `UNSUPPORTED`).

### 11.5 FX and credit (`products/fx.py`, `products/credit.py`)
FX spot: any account; pairs with a Yahoo quote; dealt at mid ± 0.1 pip; T+2 (`settle:fx:{deal_id}`); crosses through
USD when there is no direct quote. Currency loans (CREDIT account): lender "Money market (paper)" in Build 1 (PB loans in
PB accounts), floating index + 0.75 %, drawn as settled cash at once, accrues daily, repaid in the loan's currency (an
insufficient balance is refused with the reason; the UI offers the FX spot to buy the currency). Securities-lending
borrow book: `locate(instrument, qty) -> {available_qty, tier, fee_bps, rebate_bps, lender}` from `tables.HTB` (static
in Build 1: everything in the universe is GC except a short static HARD/NONE list; disclosed as "static availability
table"); borrows are booked in the **shorting account** (the Credit page shows all borrows across accounts, plus its own
loans). Returns happen automatically when a short is covered (`borrow_return:{borrow_id}`), collateral comes back, fees
and rebates settle.

### 11.6 Prime brokers (`tables.PB_SCHEDULES`; opened on demand, each a relationship with a disclosed schedule)
| broker | id | equities | options | futures | financing | borrow spread over GC | OTC half-widths (Build 2) |
|---|---|---|---|---|---|---|---|
| Goldman Sachs | PB_GS | 0.0030/sh min 1.00 | 0.55/contract | 2.00 | SOFR + 1.25 % | +0.10 % | IRS 1.0 bp, TRS fin SOFR + 0.60 % |
| JPMorgan | PB_JPM | 0.0030 | 0.55 | 2.00 | SOFR + 1.20 % | +0.10 % | 1.0 bp, +0.55 % |
| Morgan Stanley | PB_MS | 0.0035 | 0.60 | 2.10 | SOFR + 1.30 % | +0.12 % | 1.2 bp, +0.60 % |
| Deutsche Bank | PB_DB | 0.0035 | 0.60 | 2.10 | SOFR + 1.40 % | +0.15 % | 1.5 bp, +0.70 % |
| Barclays | PB_BARC | 0.0035 | 0.60 | 2.10 | SOFR + 1.35 % | +0.12 % | 1.2 bp, +0.65 % |
| Citi | PB_CITI | 0.0030 | 0.55 | 2.00 | SOFR + 1.30 % | +0.10 % | 1.0 bp, +0.60 % |
| UBS | PB_UBS | 0.0035 | 0.60 | 2.10 | SOFR + 1.35 % | +0.12 % | 1.2 bp, +0.65 % |
| BNP Paribas | PB_BNP | 0.0040 | 0.65 | 2.25 | SOFR + 1.45 % | +0.15 % | 1.5 bp, +0.70 % |
All schedules are **modelled assumptions**, shown on the "Open prime broker account" modal and the PB account page.
In Build 1 a PB account trades the same US products as US Exchange with its schedule and optional financing; OTC is
labelled NOT IN THIS BUILD.

### 11.7 Lifecycle job registry (`lifecycle.REGISTRY`; all keys are idempotent, all catch up missed dates)
| job | when (NY) | key | notes |
|---|---|---|---|
| `settle_due` | every tick | `settle:{fill_id}` / `settle:fx:{deal_id}` for rows with due_date ≤ today | business days only |
| `cash_interest` | 00:05 for the previous calendar day | `accr:cashint:TREASURY:{ccy}:{date}` | SOFR of the latest observation ≤ date |
| `cash_interest_pay` | 00:10 on the 1st | `pay:cashint:{ccy}:{yyyy-mm}` | |
| `loan_interest` | 00:05 | `accr:loan:{loan_id}:{date}` | from the drawdown date inclusive |
| `borrow_accruals` | 00:05 | `accr:bfee:…`, `accr:rebate:…` | mark = last `close_marks` ≤ date (weekend uses Friday's) |
| `borrow_settle_monthly` | 00:10 on the 1st | `pay:bfee:{borrow}:{yyyy-mm}` | and on return |
| `collateral_mark` | 16:30 business days | `cmark:{borrow}:{date}` | 102 % both ways; sweep-in linked |
| `futures_vm`, `futures_margin` | after each group's settle time | `vm:…`, `margin:…` | observed settle proxy |
| `option_expiry` | 16:05 / 16:20 on expiry dates | `optexp:{account}:{instrument}` | waits for an observed close |
| `futures_expiry` | after settle on last trade date | `futexp:{account}:{instrument}` | |
| `day_order_expiry` / `gtc_expiry` | group close + 1 min / daily | `dayexp:{order}` / `gtcexp:{order}` | |
| `close_marks_capture` | 16:02 (equities), group settle times | `close:{instrument}:{date}` | stores the observed close for held/working instruments; missing → attention, snapshot `partial` |
| `sweep_out` | 16:30 | `sweepout:{account}:{date}` | per-account switch |
| `nav_snapshot` | 16:35 business days | `nav:{account}:{date}` | marks `partial` with the missing list; never invents |
| `rates_refresh` | every 6 h | `rates:{series}:{fetched_hour}` | via `RecordRates` |

---

## 12. Hedge candidates (`hedges.py`, pops up on the ticket as soon as an instrument is entered)

`candidates(conn, snap, chain, primary: {instrument, side, qty}) -> list[Candidate]`; each `Candidate` =
`{id, label, legs:[{instrument_id, side, qty, price_basis, est_price, quote}], cost, protection_level, net_cash, greeks, chain_quality, editable:{strike, expiry, ratio}}`.
| primary | candidates (default sizing; strikes snap to listed; expiry = nearest listed ≥ 90 calendar days) |
|---|---|
| long equity/ETF | `PUT_7`: protective put K ≈ 0.93 × S, qty = round(shares/100); `COLLAR_7`: that put + call K ≈ 1.07 × S; `PUT_SPREAD`: 0.93/0.85 puts; `INDEX_OVERLAY`: short ES, n = round(notional / (ES price × 50)) (beta 1.0 assumed; editable) |
| short equity | `CALL_7` protective call 1.07 × S; `CALL_SPREAD` 1.07/1.15; `COLLAR_SHORT` (call + put 0.93) |
| long/short option | opposite-direction underlying delta hedge (shares = −delta × contracts × 100, rounded to 100) |
| futures | opposite futures of the same root in the next month (calendar spread) — options on futures NOT IN THIS BUILD |
| FX spot | forward (Build 2, computed CIP) — NOT IN THIS BUILD label in Build 1 |
| bond (Build 2) | ZN DV01-matched |
Cost uses the chain's ask (buys) / bid (sells); protection level = K/S − 1; the panel shows the chain's freshness
("Cboe · 15 min delayed · 14:31:05"). Choosing a candidate adds its legs to the package: the primary order first, the
hedge legs `WAITING_PARENT` with `ratio = hedge_qty / primary_qty`, resized on partial fills. Collar legs are sequential
(put, then call); nothing is all-or-none in Build 1 (disclosed: "sequential; a leg can rest").

---

## 13. Risk and compliance (`risk.py`; inform only)

`risk.summary(conn, snap, account|ALL)` → exposures by class/account, Greeks by underlying (from the chain's greeks ×
contracts × 100, signed), DV01 (Build 2 bonds; futures ZN/ZB via assumed duration 6.5 / 15, disclosed), FX exposure by
currency, scenario table (equities ±5/10/20 % linear + option re-pricing via `pricing.bsm` with the chain IV; rates ±25/50/100 bp
for rate futures/bonds; vol ±5 pts for options; FX ±5 %), concentration (top positions % NAV), financing & liquidity
(available cash, Treasury headroom, margin excess, collateral posted). **Warnings** (shown in preview, Risk page,
Attention strip; never block): concentration > 25 % NAV, gross leverage > 2×, option margin > 50 % of account NAV,
stale-mark positions, loans > 50 % Treasury capital. **Compliance notices** (each: rule name, threshold used, data
used, "verify the current rule text"): Reg SHO 203(b) locate (satisfied by the borrow leg); Rule 201 price test when the
day's low ≤ 0.90 × prev close (uses the quote's `prev_close` and `day_low`); Reg T / FINRA 4210 initial 50 % on margined
equity (informational, since the terminal funds trades fully); PDT (4+ day trades in 5 business days with NAV <
25,000 — informational); 13D/G at 5 % and Section 16 at 10 % of shares outstanding (Build 2 when SEC data is wired;
Build 1 shows "shares outstanding not loaded"); 13h-1 (2 M shares or USD 20 M in a day, 20 M shares / USD 200 M in a month);
Form SHO / 13f-2 (USD 10 M gross short); 13F (USD 100 M); listed option position limits (static table 25,000–250,000
contracts); CFTC/CME position limits (static table); self-trading between own accounts (same instrument opposite sides
across accounts within 60 s); wash sale (IRS 1091: a loss realised within 30 days of a buy of the same instrument);
EU/UK short disclosure 0.1 %/0.5 % (Build 2 foreign names).

---

## 14. HTTP API (`api.py`)

All responses `application/json`; Decimals as strings; errors `{error:{code, message, reasons?:[{code,message}], request_id?, incident_id?}}`.
Codes: 400 `VALIDATION` | `BLOCKED`; 404 `NOT_FOUND`; 409 `REQUEST_ID_REUSED` | `PREVIEW_STALE` | `STATE_CHANGED`; 500 `INTERNAL`;
503 `WRITER_BUSY` | `SHUTTING_DOWN`. Writes take `request_id` in the body (UUID; generated and echoed if absent). `/api/shutdown`
and `/api/admin/*` are loopback-only.

| method path | params | response (shape) |
|---|---|---|
| GET `/api/health` | — | `{app:"finsim-terminal", version, pid, started_at, now_ny, db_path, state_seq, writer:{busy,last_command_ms}, workers:{quotes:{last_cycle, last_ok, subscribed, errors_1h, paused_until}, lifecycle:{last_tick, due, failed:[{job,key,error,next_retry}]}}, providers:{yahoo:{state, last_ok, last_error}, cboe:{…}, fred:{…}, treasury_gov:{…}}, integrity:{ok, checked_at, failures:[…]}, attention:[{id, at, kind, severity, message, ref}]}` |
| GET `/api/clock` | — | `{now_utc, now_ny, business_date, sessions:{US_EQUITY:{state, opens_at, closes_at, next_open, early_close}, …}, next_jobs:[{job, at}]}` |
| GET `/api/accounts` | — | `[{id, kind, name, broker, status, nav, cash_settled:{ccy:amt}, buying_power:{USD:…}, working_orders, pending_settlements, attention_count, sweep_out, pb_financing}]` |
| POST `/api/accounts/pb` | `{request_id, broker}` | the new account + its schedule (idempotent per broker) |
| GET `/api/accounts/{id}` | — | `{account, cash:{ccy:{settled, pending, restricted, reserved, available, carried_base, spot}}, buying_power:{…components}, nav, equity, pnl:{today, mtd, ytd, itd, decomposition}, positions_count, working_orders, pending_settlements:[…], borrows:[…], loans:[…], margin:{futures_required, futures_posted, options_requirement}, collateral:{posted, held_at_lender}, sweeps_today:[…], marks_partial:[…]}` |
| GET `/api/accounts/{id}/positions` | `?cls=` | `[{instrument:{id, cls, symbol, name, ccy, multiplier}, qty, avg_cost, mark:QuoteRecord, market_value, notional, unrealised, pnl_today, realised_itd, income_itd, financing_itd, pct_nav, days_held, borrow:{id, fee_bps, collateral}|null, settle_px, lots:[…]}]` |
| GET `/api/accounts/{id}/orders` | `?status=working|all&limit&offset` | `[{id, instrument, side, qty, filled_qty, avg_price, order_type, limit_px, stop_px, tif, status, reason, triggered, created_at, updated_at, expires_at, package_id, depends_on, fills:[…]}]` |
| GET `/api/accounts/{id}/transactions` | `?from&to&kind&q&limit&offset` | `[{journal_id, at, value_date, kind, memo, ref_type, ref_id, lines:[{gl, name, ccy, debit, credit, instrument_id}], net_cash:{ccy:amt}}]` |
| GET `/api/accounts/{id}/cash` | — | cash by ccy + loans + borrows + collateral + margin + financing accruals + sweep log |
| GET `/api/accounts/{id}/markets` | `?tab=` | `{tabs:[…], rows:[{instrument, mark:QuoteRecord, change_pct, support}]}` (US Exchange: stocks&ETFs / options underlyings / futures; CREDIT: loans/borrows; FOREIGN_FX: FX pairs (+ listings Build 2); PB: same as US Exchange + OTC placeholder) |
| POST `/api/accounts/{id}/settings` | `{request_id, sweep_out?, sweep_buffer?, pb_financing?}` | the account |
| GET `/api/instruments` | `?q=&cls=&limit=` | typeahead rows `[{id, symbol, name, cls, ccy, exchange, support}]` |
| GET `/api/instruments/resolve` | `?symbol=` | creates/returns the instrument (fetches Yahoo meta); 404 `NOT_FOUND` with Yahoo's reason |
| GET `/api/instruments/{id}` | — | instrument + spec + session + mark + `support` |
| GET `/api/instruments/{id}/bars` | `?range=1d` | `{meta, bars:[{t, o, h, l, c, v}], fetched_at, source}` (triggers a chart fetch if older than 60 s; bounded wait 3 s, else returns the cached bars with `stale:true`) |
| GET `/api/instruments/{id}/chain` | `?expiry=&strikes=±N` | `{underlying:{price, bid, ask, …, quote:QuoteRecord}, expiries:[…], contracts:[…], fetched_at, delay_label, quality}` |
| GET `/api/quotes` | `?ids=a,b,c` | `{id: QuoteRecord}` (subscribes the ids for 90 s) |
| POST `/api/orders/preview` | `{account_id, legs:[{instrument_id, side, qty, order_type, limit_px?, stop_px?, tif, moo?}], hedge?:{candidate_id, ratio?, edits?}}` | §14.1 |
| POST `/api/orders` | `{request_id, preview_id, account_id, legs, hedge?, accept_moves?:bool}` | `{orders:[…], fills:[…], sweeps:[…], package_id, preview_diff?}` ; 400 `BLOCKED`; 409 `PREVIEW_STALE` (body carries the fresh preview) |
| POST `/api/orders/{id}/cancel` | `{request_id}` | the order |
| POST `/api/treasury/deposit` | `{request_id, ccy, amount, memo}` | `{transfer, treasury}` |
| POST `/api/transfers` | `{request_id, from_account, to_account, ccy, amount, memo}` | `{transfer}` |
| GET `/api/treasury` | — | `{capital, retained, advances:[{account, amount}], cash:{ccy:…}, interest:{rate, accrued, paid_itd}, deposits:[…], sweeps:[…], transfers:[…]}` |
| POST `/api/fx/spot` | `{request_id, account_id, buy_ccy, sell_ccy, amount, amount_ccy}` | `{deal, quote}` |
| POST `/api/fx/preview` | same without request_id | `{mid, rate, buy_amt, sell_amt, spread_cost, settle_date, quote}` |
| POST `/api/credit/loans` | `{request_id, account_id, ccy, amount}` | `{loan, rate:{index, obs_date, value, spread}}` |
| POST `/api/credit/loans/{id}/repay` | `{request_id, amount}` | `{loan, paid_interest, paid_principal}` |
| GET `/api/credit/locate` | `?instrument_id=&qty=` | `{available_qty, tier, fee_bps, rebate_bps, lender, collateral_pct:"1.02", basis}` |
| POST `/api/credit/borrows/{id}/return` | `{request_id, qty}` | the borrow (only the uncovered-short-free part can be returned) |
| POST `/api/options/exercise` | `{request_id, account_id, instrument_id, qty}` | `{event, fills}` |
| GET `/api/portfolio` | — | consolidated: `{nav, pnl, by_account:[…], by_class:[…], positions:[…], marks_partial}` |
| GET `/api/accounting/balance-sheet` | `?account=ALL|id` | `{as_of, assets:[{gl, name, ccy, local, base}], liabilities:[…], equity:[…], unrealised_overlay:[…], totals:{assets, liabilities, nav}, eliminations}` |
| GET `/api/accounting/pnl` | `?account=&period=` | `{period, from, to, nav_open, nav_close, flows, pnl, decomposition:{realised, unrealised_change, income, fees, financing}, categories:[…]}` |
| GET `/api/accounting/ledger` | `?account=&gl=&ccy=&from&to&limit&offset` | journal entries with lines |
| GET `/api/accounting/trial-balance` | `?account=` | rows + balanced flag |
| GET `/api/accounting/integrity` | — | `{ok, checked_at, checks:[{name, ok, detail}]}` (runs the full check) |
| GET `/api/risk` | `?account=` | §13 |
| GET `/api/activity` | `?type=&account=&from&to&limit&offset` | `[{seq, at, type, account_id, instrument_id, ref, payload}]` |
| GET/POST/DELETE `/api/watchlists`, `/api/watchlists/{id}/items` | … | watchlists |
| GET/PUT `/api/settings` | — | `{theme, hotkeys, default_qty, participation, sweep_buffer_treasury, …}` |
| POST `/api/attention/{id}/ack` | `{request_id}` | the item |
| POST `/api/shutdown` | — (loopback) | `{shutting_down:true}` |
| POST `/api/admin/run-lifecycle` | `{job?, key?}` (loopback) | `{ran:[…]}` |
| GET `/api/hedges/candidates` | `?account_id&instrument_id&side&qty` | `[Candidate]` |

### 14.1 Preview response
```json
{"preview_id":"pv_…","as_of":"2026-10-20T15:46:50-04:00","expires_at":"…+120s",
 "legs":[{"instrument":{…},"side":"BUY","qty":"100","order_type":"MARKET","tif":"DAY",
          "exec_price":"339.50","price_basis":"Cboe NBBO ask","quote":{…QuoteRecord…},
          "gross":"33950.00","fee":"1.00","fee_basis":"USD 0.005/share, min 1.00","net_cash":"-33951.00","ccy":"USD",
          "settle_date":"2026-10-21","session":{"state":"OPEN","closes_at":"16:00"},
          "fill_estimate":{"immediate_qty":"100","cap":"5000","cap_basis":"10% of 1-min volume 50,000"},
          "borrow":null,"margin":null,"blocks":[],"warnings":[]}],
 "funding":{"ccy":"USD","settled":"1000000.00","pending":"0.00","reserved":"0.00","available":"1000000.00",
            "need":"33951.00","sweep_in":"0.00","pb_financing":"0.00","fx_leg":null,"ok":true,"reasons":[]},
 "hedge":{"candidates":[…],"selected":null},
 "totals":{"net_cash":"-33951.00","fees":"1.00"},
 "risk_warnings":[],"compliance":[{"rule":"Reg SHO 203(b)","applies":false}],"blocks":[]}
```

---

## 15. Server threading model, single instance, shutdown (`server.py`)

- `TerminalHTTPServer(ThreadingHTTPServer)`: `allow_reuse_address = False`; `server_bind()` sets
  `SO_EXCLUSIVEADDRUSE` on Windows (`socket.SO_EXCLUSIVEADDRUSE`) before `bind`; `daemon_threads = True`;
  `request_queue_size = 32`. A second bind on 8765 raises `OSError(WSAEADDRINUSE)` → `AlreadyRunning`.
- Lock file `FINSIM_HOME/terminal.lock`: `os.open(path, O_CREAT|O_EXCL|O_WRONLY)` → write `{pid, port, started_at,
  version}`. If it exists: read the pid; on Windows test liveness with `ctypes.windll.kernel32.OpenProcess(0x1000, False,
  pid)` + `GetExitCodeProcess == 259 (STILL_ACTIVE)` (never `os.kill(pid, 0)`: on Windows that **terminates** the
  process); if alive → `AlreadyRunning(pid)` (exit code 3, message in the log and on stderr); if dead → the stale file is
  replaced. The lock is acquired **before** the DB is opened and the workers start.
- Reads: `db.reader()` returns a thread-local connection (`sqlite3.connect(path, check_same_thread=True)`,
  `PRAGMA query_only=ON`), opened lazily per HTTP thread; the writer connection belongs to the bus. No request ever waits
  for a network call: previews use the cache; `bars`/`chain`/`resolve` wait at most 3 s on a worker future, else return
  what is cached with `stale:true`.
- Workers: `QuoteWorker`, `LifecycleWorker` are daemon threads with a shared `stop_event`; each loop iteration is
  `try/except Exception → log.exception + attention.record + sleep(backoff)`; the loop itself never dies. Health shows
  each worker's last tick; a worker silent for > 3 cycles is itself an attention item ("quote worker stalled").
- Provider errors: `providers.http` records `{at, host, status, message}` into a ring buffer (last 100) and bumps
  per-host counters; `/api/health.providers` exposes state, and the UI strip shows "Yahoo paused until 14:40 (3
  failures)". Quotes keep their last valid record (labelled `stale` when the session is open).
- Logging: `logging.handlers.RotatingFileHandler(terminal.log, maxBytes=5 MB, backupCount=5)`, INFO; every command
  (name, request_id, ms, outcome), every job run, every provider failure, every integrity failure.
- Graceful shutdown: `POST /api/shutdown` → `stop_event.set()`; workers exit at their next check (≤ 1 s, never mid-
  command); `httpd.shutdown()` from a helper thread; `wal_checkpoint(TRUNCATE)`; lock removed; process exits 0. SIGTERM/
  Ctrl-C does the same. A crash leaves a stale lock, handled above, and the WAL guarantees the last COMMIT is intact.

---

## 16. UI (`static/`, vanilla JS, no build, no CDN)

### 16.1 Files
```
index.html                  shell: header, sidebar, main, right dock, attention strip, modal/toast roots; loads css + js in order
terminal.css                tokens + components (one file)
core.js                     T.api (fetch wrapper with request_id + retry on network error for idempotent calls), T.state (store + subscribe),
                            T.router (hash routes), T.poll (named pollers; pause when document.hidden), T.fmt (numbers from strings via Intl),
                            T.keys (hotkeys), T.ui (toast, modal, confirm)
components/sidebar.js       accounts list with NAV/cash, PB opener, watchlists, global pages
components/header.js        NY clock, session chips, active-account card (NAV, buying power, pending orders/settlements, borrow/collateral)
components/attention.js     the strip (health.attention + integrity + provider states), ack buttons
components/freshness.js     price cell renderer + tooltip
components/grid.js          dense table with sticky header, sort, filter, keyboard row nav, CSV/copy
components/ticket.js        the single ticket (fields, keyboard map, live preview, submit, PREVIEW_STALE diff)
components/preview.js       the preview panel (legs, funding, borrow, margin, warnings, blocks)
components/hedge.js         hedge candidate panel (auto-populated; pick/edit)
components/chain.js         option chain table (calls | strike | puts; bid/ask/iv/delta/oi; click → ticket leg)
components/chart.js         canvas 1-minute chart (line + volume), no library
pages/markets.js            account Markets tab (product tabs; row → instrument workspace; row → ticket prefill)
pages/instrument.js         workspace: quote header with freshness, chart, chain (if optionable), position/lots, orders on it, ticket docked
pages/positions.js          account positions (+ close/hedge actions)
pages/orders.js             working + history, cancel, fills drill-down
pages/cash.js               Cash & financing per account: cash by ccy, settlements, loans, borrows/collateral, margin, sweeps, FX spot form, loan form
pages/transactions.js       per-account ledger-derived transactions
pages/treasury.js           capital, cash by ccy, advances by account, interest, deposits form, transfer form, sweep log
pages/portfolio.js          consolidated positions/P&L by account & class
pages/accounting.js         tabs: P&L, Balance sheet, Ledger, Trial balance, Integrity
pages/risk.js               exposures, greeks, scenarios, concentration, warnings, compliance notices
pages/activity.js           events/fills/orders/transfers/jobs with filters
pages/watchlists.js         lists with live prices; add by symbol (resolve)
pages/settings.js           hotkeys, defaults, per-account sweep switches, theme
```

### 16.2 Look
Dark terminal: tokens `--bg #0b0e14; --panel #121722; --panel-2 #161c28; --line #1f2735; --text #d7dde8; --muted #8a94a6;
--accent #4f8cff; --up #35c47c; --down #ff5c5c; --warn #f5b942; --stale #b08cff; --mono "JetBrains Mono","Cascadia Mono",Consolas,monospace;
--sans "Segoe UI",system-ui,sans-serif`. Base 12.5 px, numbers mono and right-aligned, 28 px rows, sticky headers, 1 px
lines, focus rings visible, reduced-motion respected. Layout grid: `220px sidebar | 1fr main | 380px dock` (dock holds
the ticket on account pages; collapsible with `t`). Header: product name **FINSIM PAPER TERMINAL** (the "PAPER" tag is
always visible), NY clock, session chips (`US EQ · OPEN · closes 16:00`, `CME · BREAK 17:00–18:00`), the active account
card. The attention strip sits under the header: red (error), amber (warn), blue (info); each item has its time, text
and an ack button; integrity failures and provider pauses cannot be acked away while they persist.

### 16.3 Freshness on every price (`components/freshness.js`)
`T.px(rec, {size})` renders `<span class="px q-delayed">339.50</span><span class="fr">≤15m · 15:46:50 · Yahoo</span>`;
quality classes: `q-live` (green dot), `q-delayed` (green hollow dot + delay), `q-close` (grey, "CLOSE 16:00"), `q-computed`
(purple, "MODEL"), `q-stale` (amber, "STALE 23m"), `q-unavailable` (red dash, "N/A"). Tooltip: source, provider time,
fetched time, delay label, the rule that produced the state, price basis. Positions, markets, watchlists, chain, ticket,
preview, portfolio and risk all use it; nothing shows a bare number.

### 16.4 State and polling (`core.js`)
`T.state = {clock, health, accounts, active_account, account_detail, positions, orders, quotes:Map, watchlists, selection:{instrument}, ticket, preview, settings}`.
Pollers (names → interval): `health` 10 s; `clock` 30 s; `accounts` 10 s; `account_detail` 10 s (active account);
`positions` 10 s; `orders` 5 s while any working order, else 30 s; `quotes` 5 s while a visible session is OPEN else 15 s
(ids = visible rows + ticket instrument + hedge legs); `chain` 60 s on the instrument page; `preview` re-runs every 5 s
while the ticket has a preview open (read-only call). After every successful write the page refreshes from the
response immediately; `health.state_seq` changes also trigger an `accounts`+`positions` refresh. Polling pauses when the
tab is hidden and resumes with an immediate fetch.

### 16.5 Pages (routes)
- `#/a/{account}/markets[/{tab}]` — product tabs; dense grid (symbol, name, last, chg%, bid/ask when known, volume,
  freshness); typeahead "add symbol" (resolve); row Enter → instrument workspace; `b`/`s` on a row → ticket prefilled.
- `#/a/{account}/i/{instrument}` — **Instrument workspace**: quote header (last, bid/ask, day range, prev close, session,
  freshness, support label), 1-minute chart, position & lots for this account, working orders on it, option chain (if
  optionable; expiry selector; click bid/ask → ticket leg), news-free (no invented content). The ticket is docked right
  with the hedge panel already populated for the default side/qty.
- `#/a/{account}/positions` — table per §14 positions; actions: Close (ticket prefilled opposite side, full qty), Hedge
  (ticket with hedge panel open), Exercise (options), Return borrow.
- `#/a/{account}/orders` — working (cancel with `x`), history with reasons, fills with the quote used.
- `#/a/{account}/cash` — Cash & financing: cash by ccy (settled/pending/available/held at lender), pending settlements
  ladder (next 5 business days), loans (draw/repay forms), borrows & collateral (today's mark), futures margin,
  option margin requirement, sweeps today, FX spot form (preview → confirm), per-account sweep switch.
- `#/a/{account}/transactions` — every ledger entry of the account (filters, CSV).
- `#/treasury` — capital (deposits form), cash by ccy, advances by account (click → account), interest, sweep log,
  manual transfer form, Treasury sweep buffer.
- `#/portfolio` — all accounts: NAV, P&L periods, positions grouped by account then class, footer totals.
- `#/accounting` — P&L (periods × categories), Balance sheet (account selector incl. ALL with eliminations shown),
  Ledger browser, Trial balance, Integrity (last result + "run now").
- `#/risk` — §13.
- `#/activity` — §14 activity.
- `#/watchlists` — add/remove; prices live.
- `#/settings`.

### 16.6 The ticket (`components/ticket.js`) — one component, every page
Fields: account (fixed to the page's account, shown, not editable), instrument (typeahead, resolve on Enter), side
(`BUY`/`SELL`/`SELL SHORT`/`BUY TO COVER`; options `BTO`/`STO`/`BTC`/`STC` by context), qty (+ lot stepper), type
(`MKT`/`LMT`/`STP`/`STP LMT`), limit, stop, TIF (`DAY`/`GTC`), MOO toggle (only shown when the session is closed),
hedge panel (auto), Preview (auto, debounced 300 ms; manual `Enter`), Submit (`Ctrl+Enter`, enabled only when the
preview is fresh and has no blocks). Preview panel shows every leg (price + freshness + basis), fees, net cash, settle
date, fill estimate (participation cap), the funding plan (settled / pending / available / **sweep-in from Treasury** /
PB financing / FX leg), borrow terms for shorts (availability, tier, fee, rebate, collateral 102 % amount), margin for
written options/futures, risk warnings (amber), compliance notices (blue), blocks (red, with codes). On `409
PREVIEW_STALE` the panel shows the diff (`339.50 → 341.10 (+0.47 %)`) with **Accept & submit** / **Cancel**. After a
submit the panel shows the result (fills with prices, resting qty, sweep executed) and the positions/orders grids refresh.

Keyboard map (global unless noted): `/` symbol search; `g m`/`g p`/`g o`/`g c`/`g x` markets/positions/orders/cash/transactions
of the active account; `g t` Treasury; `g f` portfolio; `g a` accounting; `g r` risk; `g l` activity; `Alt+1…9` switch
account (sidebar order); `t` toggle ticket dock; in the ticket: `b s x c` sides, `m l k j` types, `+ -` qty by lot
(`Shift` ×10), `h` hedge panel, `1…5` pick candidate / `0` none, `Enter` preview, `Ctrl+Enter` submit, `Esc` clear; grids:
`↑↓` move, `Enter` open, `b`/`s` prefill; `x` cancel order; `?` help overlay.

### 16.7 What the user always sees
Header card (active account): NAV, buying power (USD, with "incl. Treasury" sub-line), settled / pending cash, working
orders count, pending settlements count, borrows & collateral held at lender, loans; a `PAPER` tag; the session chip;
the integrity dot (green/red). Unsupported products show a grey **NOT IN THIS BUILD** row state and cannot be ticketed;
`LIMITED` products (e.g. a future Yahoo quotes only intermittently) show the limitation text in the workspace header.

---

## 17. Launcher / CLI (`terminal/app.py`) and `finsim/finsim/__main__.py`

`python -m finsim <cmd>` (re-pointed to `finsim.terminal.app.main`):
| cmd | behaviour |
|---|---|
| `serve [--port 8765] [--db PATH] [--home DIR] [--offline] [--log-level INFO]` | foreground server; exit 3 if another instance holds the lock/port; prints `ready http://127.0.0.1:8765/ (terminal 1.0.0, db …)` |
| `stop [--force] [--timeout 15]` | `POST /api/shutdown`; waits for the port to close and the lock to disappear; `--force` → `taskkill /PID` of the lock's pid if still alive after the timeout; exit 0 only when stopped |
| `status` | prints: served version (from `/api/health`) vs code version on disk (warns "restart to serve the new code"), pid, uptime, db path/size, log path, lock state, worker/provider states, integrity, last lifecycle tick, Startup entry present? |
| `install [--no-open]` | writes `FINSIM_HOME/FinSim Server.vbs` and `FinSim Terminal.vbs` (+ icon), copies the server VBS to the Startup folder as **`FinSim Server.vbs`** (replacing the old one), deletes the old `schtasks` task `FinSim Server` and `~/.finsim/finsim-server.vbs`/`finsim-open.vbs`, creates Desktop + Start Menu `FinSim.lnk` (replacing the old), stops any server answering on 8765 that is not the terminal (its `/api/health` lacks `app:"finsim-terminal"`) via its own loopback `/api/shutdown`, starts the terminal via the VBS, waits for health, prints the final state |
| `open` | if the lock names a live pid → wait up to 60 s for health; else start via `FinSim Server.vbs`; then open a Chrome/Edge/Brave `--app` window with its own profile under `FINSIM_HOME/window-profile` (fallback: default browser) |
| `uninstall [--purge]` | removes Startup entry, shortcuts, VBS files, window profile; stops the server; `--purge` deletes `terminal.db` (never `finsim.db`) |
| `legacy [--port 8766]` | copies `~/.finsim/finsim.db` to `~/.finsim/legacy-view.db` (SQLite backup API) and runs the OLD simulation server (`finsim.api.server.serve`) on that copy, so `finsim.db` stays byte-identical; prints the URL; documented as the read-only archive view |

Python resolution at install: `py -3.12 -c "import sys;print(sys.executable)"` → `pythonw.exe` next to it (fallback
`%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe`); the absolute path is written into the VBS.

`FinSim Server.vbs` (ASCII; Startup folder; replaces the old entry):
```vbscript
' FinSim Paper Terminal - starts the local server at login unless it already answers.
Option Explicit
Dim sh, fso, http, up, py, repo, home
Set sh = CreateObject("WScript.Shell"): Set fso = CreateObject("Scripting.FileSystemObject")
py = "{PYTHONW}": repo = "{REPO_FINSIM_DIR}": home = "{FINSIM_HOME}"
If Not fso.FileExists(py) Then WScript.Quit 1
up = False
On Error Resume Next
Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0"): http.setTimeouts 1000, 1000, 2000, 2000
http.Open "GET", "http://127.0.0.1:{PORT}/api/health", False: http.Send
If Err.Number = 0 Then If http.Status = 200 Then If InStr(http.responseText, """finsim-terminal""") > 0 Then up = True
On Error GoTo 0
If up Then WScript.Quit 0
sh.Environment("Process")("PYTHONPATH") = repo
sh.Environment("Process")("FINSIM_HOME") = home
sh.CurrentDirectory = repo
sh.Run """" & py & """ -m finsim serve --port {PORT}", 0, False
```
If an old simulation server answers on the port, the VBS does nothing (the new server would refuse the port anyway);
`install` is the only thing that stops the old server, once, and `status` keeps saying "a non-terminal server answers
on 8765" until it is gone. `FinSim Terminal.vbs` runs `pythonw -m finsim open`.

---

## 18. Test plan (`finsim/tests/test_terminal_*.py`, unittest, no network, temp SQLite, `FakeClock`, `FakeProvider`)

Fixture: `make_terminal(tmpdir, now="2026-10-20T10:00:00-04:00")` → `TerminalCore` (db + bus + cache + registry, no HTTP);
`FakeProvider` quotes (all `delayed`, session OPEN unless the clock says otherwise):
AAPL last 339.50 bid 339.48 ask 339.50 1m-bar volume 50,000; ACME last 50.00 bid 50.00 ask 50.02 volume 100,000; ESZ26 7,754.25;
EURUSD mid 1.1238; rates SOFR 3.87 (obs 2026-10-19), ECBDFR 2.00 (assumed), so: idle/rebate rate 3.37 %, EUR loan 2.75 %.
Dates: Tue 2026-10-20 (T), T+1 = Wed 10-21, T+2 = Thu 10-22; Fri 10-23 → Mon 10-26 for the weekend case. Every expected
number below is worked by hand; the test comments repeat the arithmetic.

**S1 Deposit + initial capital.** Start → `capital:initial` posted once: Treasury 1010:USD 10,000,000.00, 3000 10,000,000.00.
Restart the core on the same file → still one posting. `Deposit(R1, USD 500,000)` → 1010 10,500,000.00; 3000 10,500,000.00;
replay R1 → `replayed=True`, balances unchanged; R1 with amount 600,000 → `409 REQUEST_ID_REUSED`. treasury(): capital
10,500,000, retained 0, advances 0, cash 10,500,000 ✓.

**S2 Transfer + sweep-in.** `Transfer(R2, TREASURY→US_EXCHANGE, USD 1,000,000)`: Treasury 1010 9,500,000; 1600 1,000,000;
US Exchange 1010 1,000,000; 3100 1,000,000. Buy 4,000 AAPL MARKET: cap = 10 % × 50,000 = 5,000 ≥ 4,000 → full fill at ask
339.50; gross 1,358,000.00; fee 4,000 × 0.005 = 20.00; net 1,358,020.00. Funding: available 1,000,000 → shortfall
358,020.00 → one `SWEEP_IN` linked to the order. After: US Exchange 1010 1,358,020.00; 2100 1,358,020.00; 1100 AAPL
1,358,000.00; 5000 20.00; Treasury 1010 9,141,980.00; 1600 1,358,020.00. Preview shows `sweep_in = 358020.00`.
After `settle_due` at T+1 09:00: US Exchange 1010 0.00; 2100 0.00. Buying power = 0 + Treasury available 9,141,980.00.

**S3 Equity buy, fee, T+1.** Fresh account funded 100,000. Buy 100 AAPL @ 339.50: Dr 1100 33,950.00 / Dr 5000 1.00 / Cr 2100
33,951.00 (fee = max(1.00, 0.50)). Before settlement: settled 100,000.00, pending −33,951.00, available 66,049.00. Run
`settle_due` with the clock at 2026-10-21 09:00 → settled 66,049.00, pending 0; run it again → no new journal
(`settle:{fill}` exists). Run it at 2026-10-20 16:00 → nothing settles (due date not reached).

**S4 FIFO sell.** Account 100,000. Buy 100 @ 339.50 (+1.00), then (provider AAPL ask → 335.00) buy 100 @ 335.00 (+1.00);
settle both: settled 100,000 − 33,951 − 33,501 = 32,548.00. Provider bid → 345.00; sell 150 MARKET: gross 51,750.00; fee
max(1.00, 0.75) = 1.00; FIFO cost = 100 × 339.50 + 50 × 335.00 = 33,950 + 16,750 = 50,700.00; realised = 51,750 − 50,700 =
**1,050.00** (4000); entry Dr 1200 51,749.00 / Dr 5000 1.00 / Cr 1100 50,700.00 / Cr 4000 1,050.00. Remaining lot 50 @ 335.00
(open_qty 50). After settlement cash 84,297.00; at mark 345.00: MV 17,250.00, unrealised 500.00, NAV 101,547.00 =
100,000 + 1,050 + 500 − 3.00 fees ✓ (check 13).

**S5 Short, borrow, 102 %, weekend accruals, Monday mark.** Clock Fri 2026-10-23 10:00; US Exchange cash 0. Order SELL_SHORT
1,000 ACME MARKET. Locate: GC, fee 30 bps, rebate 3.37 %, lender "Lender A", available. Borrow opens first: collateral
1.02 × 1,000 × 50.00 = **51,000.00** → sweep-in 51,000.00 (Treasury 1600 +51,000) → Dr 1400 51,000 / Cr 1010 51,000. Sale at bid
50.00: gross 50,000.00; fee 5.00; Dr 1200 49,995.00 / Dr 5000 5.00 / Cr 2600 50,000.00. Settlement Mon 10-26 (T+1): Dr 1010
49,995 / Cr 1200. Accruals for Fri, Sat, Sun (run Mon 00:05; mark = Friday close 50.00): fee per day = 1,000 × 50.00 × 0.0030
/ 360 = 0.41666… → **0.42**; rebate per day = 51,000 × 0.0337 / 360 = 4.77416… → **4.77**; three keys each → 2310 1.26, 5300 1.26,
1410 14.31, 4350 14.31. Monday close mark 52.00 (`close_marks`): required 1.02 × 52,000 = 53,040.00; delta **+2,040.00** → Dr
1400 2,040 / Cr 1010 2,040 (settled 49,995 → 47,955.00; no sweep needed). Monday-close NAV = 47,955.00 + 53,040.00 + 14.31
− 52,000.00 (short at market) − 1.26 = **49,008.05**; funding 51,000.00 → ITD P&L −1,991.95 = −2,000.00 (unrealised) − 5.00
(fee) − 1.26 (borrow fee) + 14.31 (rebate) ✓. integrity 8 (short has borrow 1,000 ≥ 1,000) and 10 (1400 = 53,040 =
borrows.collateral) pass. Running `borrow_accruals` twice posts nothing new. Variant: price down to 48.00 → required
48,960.00 → delta −2,040.00 returns to cash.

**S6 Currency loan + FX spot T+2.** CREDIT account, Tue 10-20. `Loan(R6, EUR 1,000,000)`: rate 2.00 + 0.75 = 2.75 %; Dr
1010:EUR 1,000,000 (fx 1.1238, base 1,123,800.00) / Cr 2750:EUR 1,000,000 (base 1,123,800.00). Same day FX spot sell EUR
1,000,000 for USD: mid 1.1238, dealt 1.1238 − 0.00001 = **1.12379**, USD **1,123,790.00**, spread cost 10.00 shown; Dr 1250:USD
1,123,790.00 / Cr 2350:EUR 1,000,000 (base 1,123,790.00); settle Thu 10-22. State after the deal: EUR settled 1,000,000, EUR
pending −1,000,000, USD settled 0, USD pending +1,123,790. Accruals 10-20 and 10-21 (posted 10-21 and 10-22 00:05): per day
1,000,000 × 0.0275 / 360 = 76.3888… → **76.39 EUR** (base at that day's mid: 76.39 × 1.1238 = 85.85); 2755:EUR 152.78 after two
days. Settlement 10-22: Dr 1010:USD 1,123,790 / Cr 1250:USD; Dr 2350:EUR 1,000,000 (base 1,123,790) / Cr 1010:EUR 1,000,000 at
the carried average 1.1238 (base 1,123,800) → Dr 4650 **10.00** realised FX loss. After: EUR settled 0, USD settled 1,123,790.
Mid moves to 1.1300: loan liability at spot 1,130,000 vs carried 1,123,800 → unrealised FX **−6,200.00**; NAV = 1,123,790 −
1,130,000 − 152.78 × 1.13 (172.64) = −6,382.64; decomposition: −6,200.00 − 10.00 (realised FX) − 172.64 (financing at spot)
✓ (to the cent, given the accrual base is retranslated at the current spot for NAV). Repay with 0 EUR → `400 BLOCKED
INSUFFICIENT_FUNDS` ("EUR 1,000,152.78 needed; buy EUR first"). Buy EUR 1,000,152.78 spot (T+2) then repay after settlement.

**S7 Options: ITM exercise and assignment.** US Exchange funded 100,000. Chain (fake Cboe, 15 min delayed): AAPL 2026-11-20
C340 bid 5.10 ask 5.20; P330 bid 4.00 ask 4.10. BTO 2 C340 @ 5.20: premium 1,040.00; fee 1.30; Dr 1700 1,040.00 / Dr 5000 1.30 /
Cr 2100 1,041.30 (T+1). STO 1 P330 @ 4.00: Dr 1200 399.35 / Dr 5000 0.65 / Cr 2900 400.00; margin requirement = max(400 +
0.20 × 339.50 × 100 − (339.50 − 330) × 100, 400 + 0.10 × 330 × 100) = max(400 + 6,790 − 950, 400 + 3,300) = max(6,240,
3,700) = **6,240.00** reserved. Expiry Fri 2026-11-20 16:05 with observed close 345.00: C340 ITM by 5.00 → exercise: Dr
1100 AAPL **69,040.00** (68,000 strike + 1,040 premium) / Cr 1700 1,040.00 / Cr 2100 68,000.00; stock lot 200 @ 345.20;
unrealised at 345.00 = −40.00. P330 OTM (345 > 330) → worthless: Dr 2900 400 / Cr 4000 400.00. Variant close 325.00: P330
assigned: Dr 1100 AAPL **32,600.00** / Dr 2900 400.00 / Cr 2100 33,000.00 (lot 100 @ 326.00); C340 worthless: Dr 4000 1,040 /
Cr 1700 1,040. Variant: no observed close → nothing posts, attention "expiry pending", job stays due. Running the job
twice → one `option_events` row.

**S8 Futures VM, two days.** US Exchange funded 50,000. Buy 2 ESZ26 @ 7,754.25 (Mon 10-19 10:00): fee 4.50 → Dr 5000 4.50 /
Cr 1010 4.50; margin job: required 2 × 15,000 = 30,000 → Dr 1300 30,000 / Cr 1010 30,000 (settled 19,995.50). Day-1 settle
(16:00 bar) 7,760.00: VM = 2 × (7,760.00 − 7,754.25) × 50 = 2 × 5.75 × 50 = **+575.00** → Dr 1010 575 / Cr 4400 575; settle_px
7,760.00. Day-2 settle 7,741.50: VM = 2 × (7,741.50 − 7,760.00) × 50 = **−1,850.00** → Dr 4400 1,850 / Cr 1010 1,850. Net 4400
= −1,275.00 = 2 × (7,741.50 − 7,754.25) × 50 ✓; settled cash 50,000 − 4.50 − 30,000 + 575 − 1,850 = 18,720.50; NAV = 18,720.50
+ 30,000 = 48,720.50 = 50,000 − 4.50 − 1,275 ✓ (open trade equity 0 at the settle). Rerun day-2 job → no new row
(`futures_settle` PK). Sell 2 @ 7,745.00 on day 3: fee 4.50; day-3 settle 7,750.00: VM = carried 2 × (7,750.00 − 7,741.50)
× 50 + fill (−2) × (7,750.00 − 7,745.00) × 50 = 850.00 − 500.00 = **+350.00** → total 4400 = −925.00 = 2 × (7,745.00 −
7,754.25) × 50 ✓ (the round trip's loss); position 0, margin released 30,000.00.

**Restart persistence** (`test_terminal_persistence.py`): run S2–S4 on a file DB; close the core; reopen → identical
`cash()`, `positions()`, `lots()`, `treasury()`, order/fill counts; `positions` projection equals Σ fills; `integrity()` ok.

**Rollback on a failing write** (`test_terminal_bus.py`): monkeypatch `orders._update_positions` to raise `RuntimeError`
after the journal post; `SubmitOrder` raises; assert no `commands`, `journal`, `fills`, `orders` rows for that request_id,
balances unchanged, `attention` has one error item; retry with the same request_id after un-patching succeeds once.

**Duplicate server** (`test_terminal_server.py`): start `TerminalServer` on a temp home/port → second `start()` with the
same home raises `AlreadyRunning(pid)` before opening the DB; a stale lock (dead pid) is replaced; two servers on the
same port → the second gets `OSError`; `/api/health` reports `app: finsim-terminal` and the version; `/api/shutdown` from
loopback stops it within 2 s and removes the lock.

**Stale quote blocking** (`test_terminal_quotes.py`): AAPL quote_time 10:00, clock 10:25 (session OPEN) → `classify` =
`stale`; MARKET submit → `400 BLOCKED QUOTE_STALE`; a resting LIMIT does not fill in `MatchOrders` while stale; at 10:05
→ `delayed` and the marketable limit fills. `fetched_at` 6 min old with a fresh quote_time → `stale`. Session CLOSED →
`close` and non-executable; model source → `computed`; no record → `unavailable`.

**Session blocking** (`test_terminal_sessions.py`): Sat 2026-10-24 → `session_state('US_EQUITY')` CLOSED, next open Mon
09:30; MARKET without `moo` → `400 SESSION_CLOSED`; with `moo` → ACCEPTED, fills at the first OPEN+fresh pass; LIMIT rests;
2026-11-27 → early close 13:00 (DAY orders expire at 13:01); 2026-11-26 → holiday CLOSED; CME group BREAK at 17:30 ET;
`settlement_date(Fri 10-23, 1)` = Mon 10-26; `settlement_date(Wed 11-25, 1)` = Fri 11-27 (Thanksgiving skipped).

**Ledger/NAV reconciliation** (`test_terminal_accounting.py`): after S1–S8 in one DB: every `integrity()` check ok;
consolidated NAV = Σ account NAV − advances = capital + Σ(realised + unrealised + income − fees − financing) to the cent;
balance sheet ALL: assets − liabilities = NAV; per-account 3100 totals = Treasury 1600 totals; `pnl(ITD)` decomposition
ties; `transactions()` for an account lists every journal entry of that account once.

Also: `test_terminal_ledger.py` (unbalanced entry refused; idem_key duplicate refused; average-rate relief; base
rounding), `test_terminal_orders.py` (every block code; partial fill cap with bar_ts; limit/stop/stop-limit paths;
hedge leg resize on a 60 % partial; cancel; DAY/GTC expiry), `test_terminal_hedges.py` (strike/expiry selection from a
fake chain; sizing), `test_terminal_api.py` (routes via `http.client` against a server on a temp port: error shapes,
preview/submit/409 flow, 404s, loopback-only shutdown), `test_terminal_app.py` (`launcher_files` content: VBS is ASCII,
contains the pythonw path, port and the terminal marker check; install/uninstall path lists are pure functions).

---

## 19. Build order — work packages for parallel agents

Each package: files it owns, interfaces it consumes, acceptance. **WP0 is serial and first; WP1–WP3 and WP8 can start the
moment WP0's interfaces land; WP4–WP7 after WP1.** Contracts between packages are the signatures in §2.1, the DDL in §3,
the posting templates in §4.3 and the API shapes in §14; an agent that must change a contract edits this file first.

| WP | owns | consumes | acceptance |
|---|---|---|---|
| **WP0 Core** | `__init__ config clockcal db ledger bus accounts accounting (cash/nav/treasury/balance_sheet/integrity) quotes (cache, classify, fake provider) tables (sessions, chart names) server (lock, bind, health, shutdown, workers skeleton) api (health/clock/accounts/treasury/transfers/deposit/settings/accounting) funding (Deposit, Transfer) static/index.html terminal.css core.js components/{sidebar,header,attention,freshness,grid}.js pages/{treasury,accounting(integrity tab),settings}.js app.py (serve/stop/status only)` | `finsim.money`, `finsim.tzfallback`, `finsim.calendar`, `data/nyse-holidays.json` | S1, S2 (transfer part), ledger tests, bus rollback, duplicate server, sessions tests pass; `py -3.12 -m unittest tests.test_terminal_core`; UI shows Treasury 10,000,000 with the PAPER tag and a green integrity dot |
| **WP1 Equities & orders** | `orders.py funding.funding_plan/apply_sweep_in products/__init__ products/equities.py api (preview/orders/cancel/positions/orders/transactions/markets/instruments/quotes/resolve/bars) components/{ticket,preview,chart}.js pages/{markets,instrument,positions,orders,transactions,cash(basic)}.js` | WP0; `instruments.py` (shared with WP2: WP1 owns the master + OCC/futures parsing, WP2 owns `resolve` via Yahoo) | S2 (sweep), S3, S4, order block codes, partial fills, stale/session blocking; preview→submit→409 flow in the UI; positions update after a fill |
| **WP2 Providers & quote worker** | `providers/* quotes.QuoteWorker RatesCache RecordMarks RecordRates close_marks_capture` | WP0 `QuoteCache` interface, bus | with `--offline` the fake provider drives the UI; with network: Yahoo spark/chart + Cboe chain parsed within the memory budget; provider errors show in health and the strip; circuit breaker opens/closes in a unit test with a stubbed `http.get` |
| **WP3 Lifecycle** | `lifecycle.py` (registry, runner, catch-up; settle_due, cash_interest(+pay), day/gtc expiry, sweep_out, nav_snapshot) | WP0 bus/ledger/accounting; WP1 settlements | S3 settlement idempotency; sweep-out numbers; snapshot `partial` flag; catch-up over a 3-day gap posts three keyed accruals |
| **WP4 Shorts & credit & FX** | `products/credit.py products/fx.py` (locate/borrow leg, collateral marks, borrow accruals, loans, FX spot) + lifecycle jobs `borrow_accruals collateral_mark loan_interest borrow_settle_monthly fx settlement` + `pages/cash.js` (loans/borrows/FX forms) + the Credit Instruments markets tab | WP1 order pipeline (borrow leg hook), WP3 registry | S5, S6; integrity 8/10/11; NO_BORROW block; repay-insufficient refusal |
| **WP5 Options & hedges** | `products/options.py pricing.py hedges.py components/{chain,hedge}.js` + `option_expiry` job + exercise command + API `chain`, `hedges/candidates`, `options/exercise` | WP1, WP2 (chain), WP3 | S7; margin formula tests; hedge candidates from a fake chain; collar legs sequence on partial fill; expiry waits without a close |
| **WP6 Futures** | `products/futures.py tables.FUTURES` + `futures_vm futures_margin futures_expiry` jobs + futures tab/session chips | WP1, WP3 | S8; group sessions/breaks; settle proxy capture |
| **WP7 Portfolio/Risk/Activity** | `risk.py pages/{portfolio,risk,activity,watchlists}.js api (portfolio/risk/activity/watchlists)` | WP0 read models, WP5 greeks | numbers tie to `accounting.py` (test renders each page's figures against the module); compliance notices table renders with "verify" wording; scenarios reprice through `pricing.py` |
| **WP8 Launcher** | `app.py (install/open/uninstall/legacy, VBS, shortcuts) finsim/finsim/__main__.py` | WP0 server | `test_terminal_app`; manual: `py -3.12 -m finsim install --no-open` replaces the Startup entry, stops the old 0.11.0 server, `status` shows terminal 1.0.0; `open` opens the window; `legacy` serves a copy on 8766 and `finsim.db` hash is unchanged |
| **WP9 (Build 2)** | `products/bonds.py` (par-curve Treasuries), foreign listings, FX forwards, repo, dividends, PB OTC, recall notices, SEC shares outstanding | all | hand-worked tests per §18 style before any UI |

Definition of done for Build 1: all `test_terminal_*` pass on `py -3.12 -m unittest` and `py -3.14 -m pytest`; the
original FinSim subset and the FinSim2 suites match the captured baseline; browser check of every page with no console
or API errors; `~/.finsim/finsim.db` byte-identical; `docs/TERMINAL_EXECUTION.md` written from §§7–13 of this document.

---

## 20. Assumptions (explicit) and open questions

Assumptions made here (each disclosed in the UI/glossary where it applies):
1. FX spot dealt at mid ± 0.1 pip (full 0.2 pip spread), pip 0.0001 (JPY 0.01).
2. Exercise/assignment are commission-free; the premium rolls into the stock's cost basis/proceeds (broker convention).
3. Short-sale collateral convention of §11.2 (102 % posted at borrow, sale proceeds settle as ordinary cash).
4. Borrow fees and loan interest accrue from the trade/draw date inclusive, ACT/360 for every currency, rounded to the cent per day; rebate = SOFR − 0.50 % floor 0.
5. Futures initial margins and the 90 % maintenance ratio are a static table marked "assumed; verify against CME"; observed prices at the group's settle time are a settlement proxy; NYSE holidays are treated as CME closures.
6. Per-contract Yahoo futures symbols beyond `CLX26.NYM` follow the `{root}{M}{yy}.{CME|CBT|NYM|CMX}` form; unverified symbols are marked UNSUPPORTED at resolve time.
7. Cboe underlying NBBO is used as the execution price only while a fresh chain for that underlying is cached; otherwise Yahoo's last trade. Yahoo quotes are labelled "≤15 min delayed" and never `live`.
8. Option margin is a reserve that sweep-in keeps as settled cash, not a ledger movement; futures IM is a real deposit (1300).
9. Treasury idle-cash interest on USD only; foreign Treasury cash earns nothing in Build 1 (labelled).
10. Borrow availability is a static table in Build 1 (GC for everything in the universe except a short HARD/NONE list); no invented recalls.
11. PB fee schedules are modelled numbers.
12. Hedge index overlay assumes beta 1.0 (editable); candidates are sequential, not all-or-none.
13. Dividends are not modelled in Build 1 (positions show "dividends: not in this build"); Build 2 uses Yahoo chart dividend events.

Open questions for the owner (defaults chosen so the build does not wait):
- MARKET orders outside the session: default is "refuse unless MOO is ticked"; alternative is "always accept and work at the open".
- Should a Treasury sweep-in require a per-trade confirmation above a threshold (e.g. USD 5 M), or stay fully automatic (default: automatic, shown in the preview)?
- Should sweep-out run on weekends/holidays as well (default: 16:30 every calendar day, which is a no-op when nothing changed)?
- Is a static futures IM table acceptable until a CME margin file is wired (default: yes, labelled)?
- Index options: are SPX/SPXW wanted in Build 1 given the 13 MB chain (default: yes, reduced at parse, on demand only), or SPY only?
