# FinSim Live Terminal — architecture proposal (execution-and-data-first lens)

Author: Claude (Fable 5.1), 2026-10-20. Status: PROPOSAL for the build team. Builds on `docs/TERMINAL_DECISIONS.md`
(not re-litigated), `docs/HANDOFF_2026-10-20.md` (newest, wins on conflict), `docs/DIRECTIVE_2026-10-20.md` and
`docs/PROBE_2026-10-20.txt`. Code maps consulted: `docs/map_parts/*.md`. Reusable code read: `finsim/calendar.py`,
`clock.py`, `tzfallback.py`, `money.py`, `engines/ledger.py`, `engines/options_pricing.py`, `engines/realfeed.py`,
`engines/live.py`, `app.py`, `api/server.py`, `finsim2/data/{__init__,cboe,yahoo}.py`, `finsim2/install/*`,
`data/nyse-holidays.json`, `finsim/data/universe.json`.

The document is written so that parallel coding agents can build from it without further design. Every number in the
test plan (section 15) is hand-worked with the arithmetic shown. Where a choice had to be made, it is made here and
marked **Decision**; where something must be checked against the live world during the build, it is marked **Verify**.

---

## 0. Scope, non-goals and standing assumptions

**What is built**: `finsim.terminal`, a standard-library Python 3.12/3.14 package: one SQLite ledger-backed paper book,
a background quote layer (Yahoo v8 chart, Cboe delayed quotes, Treasury.gov, FRED, Frankfurter, FINRA), a session
calendar per market, an order book with a matcher, lifecycle jobs keyed to the real clock, computed marks labelled
honestly, a JSON API, a vanilla-JS terminal UI, and a Windows launcher that replaces the old Startup entry.

**Non-goals in this build** (shown as *unsupported* in the UI, never fabricated): private credit/equity, IB, clients,
careers, MBS/structured, exotic OTC, commodity physicals, crypto, real brokerage connectivity. OTC IRS/TRS, repo and PB
OTC are *limited* slices (section 9.9) and may be deferred to a follow-up without breaking anything else.

**Machine facts the design respects**: Python 3.12 has no tzdata, so every zone goes through
`finsim.tzfallback.get_zone`; stdlib only; about 1.5 GB free RAM, so the Cboe 13 MB SPX payload is parsed and discarded,
never kept; 16 CPUs but a single SQLite writer, so one writer lock and many readers; the UI must stay responsive, so
reads never wait for the network.

**Standing assumptions (Decisions)**:

| # | Decision |
|---|---|
| A1 | Base currency USD. Every ledger line carries its own-currency amount **and** a USD base amount at the rate used when posted. |
| A2 | Amounts are stored as **integer cents** (`*_c` columns, `INTEGER`); quantities as integers (shares, contracts, face units; FX amounts in cents); prices/rates as canonical `Decimal` strings (`TEXT`). Python uses `Decimal` end to end (`finsim.money.D/money/price`) and converts at the storage boundary. SQL `SUM` over integer cents is exact. No floats in accounting code. |
| A3 | The ledger is **at cost**. Unrealised P&L, FX translation and forward PVs are an overlay computed by `accounting.py` from the ledger + current marks; they are never posted (no MTM journal on every quote). Realised P&L, fees, accruals, variation margin, collateral movements and settlements **are** posted. |
| A4 | Every quantity change of a position is a `fills` row (origin ORDER, EXERCISE, ASSIGNMENT, EXPIRY, FUTURES_FINAL, BUY_IN, RETURN, HEDGE). Positions and lots are derived from fills; integrity checks that the two derivations agree. |
| A5 | Time: instants are UTC epoch seconds (`INTEGER`); business dates are ISO `YYYY-MM-DD` strings in **New York** time unless a column says otherwise. "Today" means the New York calendar date. |
| A6 | Every mutating request carries a client-generated `request_id` (UUID4, at most 64 chars). Missing: HTTP 400. A repeat returns the stored result. Background jobs use deterministic ids (`job:<name>:<key>`). |
| A7 | Cost basis = quantity × price × multiplier (gross). Commissions are expensed (GL 5000) on trade date; realised P&L is gross proceeds minus gross cost (FIFO). Exercise/assignment rolls the option premium into the stock basis (no realised on the option leg). |
| A8 | A short sale's borrow is booked **in the shorting account**; the Credit Instruments page aggregates all borrows and hosts cash loans and repo. FX conversion ("Convert") is available on every account's cash panel; FX *positions* (forwards, speculative spot) live in Foreign Markets & FX. |
| A9 | Yahoo gives last-trade prices only. Equities/ETFs/futures/FX fill at the **last observed trade** (bid/ask unknown) and the preview says so. Listed options fill at the Cboe NBBO (buy at ask, sell at bid). FX adds the disclosed 0.2-pip spread cost. |
| A10 | No provider in this build is labelled `live`; Yahoo and Cboe records are `delayed` (stated delay 15 min). The `live` quality value exists for a future provider and is never produced now. |
| A11 | Futures settle daily at the **observed close** (last Yahoo trade at or before the group's daily settlement time), labelled "observed close, not the exchange's official settlement". Expiry = final cash settlement at that price; physical delivery is not modelled. |
| A12 | Options expire from the underlying's observed regular-session close on expiry day (OCC threshold: ITM by at least 0.01 exercises/assigns). Early exercise only by user action. No random assignment. |

---

## 1. Package layout `finsim/finsim/terminal/`

One module per concern. Public surface listed with signatures; private helpers are the implementer's choice. Imports
only point towards the core (section 16); no module imports `api` or `server`.

```
finsim/finsim/terminal/
  __init__.py        VERSION = "1.0.0"; SCHEMA_VERSION = 1
  config.py          paths, env, account catalogue, FEES, RATES, PB_SCHEDULES, FUTURES_SPECS, SESSION_GROUPS, limits
  units.py           cents <-> Decimal, quantize helpers
  clock.py           real clock (UTC/NY), injectable for tests
  sessions.py        session table per market/product group, holidays/early closes, settlement dates
  db.py              Database: connections, schema DDL + migrations, writer lock, transactions, backup
  instruments.py     instrument master, seeding, symbol mapping (Yahoo/Cboe), futures/option/bond builders
  ledger.py          chart of accounts, posting API (Entry builder), lots/FIFO, cash movement helper
  accounting.py      every read model (cash, buying power, positions, NAV, P&L periods, treasury, balance sheet) + integrity()
  commands.py        Command/Result, CommandBus (request_id idempotency, transaction boundary, error surfacing)
  quotes/
    __init__.py      QuoteRecord dataclass, QUALITY values, freshness()
    providers.py     http plumbing + YahooChart, CboeChain, TreasuryCurve, FredSeries, Frankfurter, FinraShortVolume
    cache.py         QuoteCache (thread-safe), subscriptions with TTL, persistence to quote_cache table
    worker.py        QuoteWorker: scheduling, rate limits, retries, provider health, matcher hook
  marks.py           mark selection per instrument; computed marks (bonds from par curve, FX forwards by CIP, option model fallback)
  orders.py          validation, preview, state machine, pure matching rules, partial-fill sizing
  execution.py       applying a fill: postings per product, settlements, lots, sweeps-in, borrow linkage, hedge packages
  lending.py         locate/borrow/return, hard-to-borrow tiers (FINRA heuristic), collateral 102% marks
  credit.py          currency loans, repo/reverse repo (thin), PB financing draw/repay
  fx.py              FX spot/forward tickets and settlement, CIP forward pricing
  lifecycle.py       jobs (settlement, accruals, coupons, option expiry, futures VM, collateral marks, sweeps-out, EOD snapshot, order expiry, integrity) + LifecycleScheduler thread
  hedges.py          hedge candidates from the live chain / futures table
  compliance.py      inform-only rule notices
  risk.py            exposures, greeks, concentration, DV01, FX exposure; warnings only
  api.py             route table, request parsing, response shaping, error mapping
  server.py          TerminalServer (threading model, exclusive bind, single-instance lock, graceful shutdown)
  app.py             CLI/launcher: serve/stop/status/install/open/uninstall/legacy; Windows VBS + shortcuts
  static/            index.html, terminal.css, core.js, store.js, quotes.js, ticket.js, pages/*.js (one per page)
```

### 1.1 `config.py`

```python
HOME_ENV = "FINSIM_HOME"                       # default ~/.finsim
DB_ENV = "FINSIM_TERMINAL_DB"                  # default <home>/terminal.db
PORT_ENV = "FINSIM_PORT"; DEFAULT_PORT = 8765; LEGACY_PORT = 8766
BASE_CCY = "USD"
INITIAL_CAPITAL_C = 10_000_000_00              # posted once, request_id "init:capital:v1"
ACCOUNT_CATALOGUE = [                           # created empty at first run (Treasury receives the initial capital)
  ("TRS", "Treasury", "TREASURY"), ("USX", "US Exchange", "US_EXCHANGE"), ("DEBT", "Debt Instruments", "DEBT"),
  ("CRED", "Credit Instruments", "CREDIT"), ("FXM", "Foreign Markets & FX", "FOREIGN_FX")]
PRIME_BROKERS = {"GS": "Goldman Sachs", "JPM": "JPMorgan", "MS": "Morgan Stanley", "DB": "Deutsche Bank",
                 "BARC": "Barclays", "C": "Citi", "UBS": "UBS", "BNP": "BNP Paribas"}
FEES = {"EQUITY": {"per_share": "0.005", "min": "1.00"}, "OPTION": {"per_contract": "0.65"},
        "FUTURE": {"per_contract_side": "2.25"}, "BOND": {"per_100_face": "0.02", "min": "5.00"},
        "FX": {"spread_pips": "0.2"}}
RATES = {"TREASURY_CASH": ("SOFR", "-0.50"), "PB_FINANCING": ("SOFR", "+1.50"), "BORROW_GC": "0.30",
         "BORROW_HTB": {"T1": "1.00", "T2": "5.00", "T3": "25.00"}, "BORROW_REBATE": ("SOFR", "-0.50"),
         "REPO": {"UST": ("SOFR", "+0.05"), "CORP_IG": ("SOFR", "+0.35")},
         "CCY_LOAN_SPREAD": "0.75",
         "POLICY_INDEX": {"USD": "SOFR", "EUR": "ECBDFR", "GBP": "IUDSOIA", "JPY": "IRSTCI01JPM156N",
                          "CHF": "IRSTCI01CHM156N", "CAD": "IRSTCI01CAM156N", "AUD": "IRSTCI01AUM156N"},
         "DAYCOUNT_CASH": {"GBP": "ACT/365", "*": "ACT/360"}, "DAYCOUNT_BOND": "ACT/ACT"}
PB_SCHEDULES = {"GS": {"equity_per_share": "0.010", "equity_min": "5.00", "option_per_contract": "1.00",
                       "financing": ("SOFR", "+1.25"), "borrow_gc": "0.35", "otc_fee_bps": "2.0"}, ...}  # one per broker
PARTICIPATION_RATE = Decimal("0.10"); GTC_DAYS = 90; STALE_GRACE_S = 300; FETCH_GRACE_S = 300
SWEEP_BUFFER_C = 10_000_00; COLLATERAL_PCT = Decimal("1.02"); OCC_THRESHOLD = Decimal("0.01")
QUOTE_INTERVALS = {"ORDER": 15, "HELD": 30, "WATCH": 60, "CLOSED": 300, "CHAIN": 90, "CURVE": 86400, "FRED": 21600}
YAHOO_RPS = 2.0; CBOE_RPS = 1.0; GOV_UA = "LoganTerminal/1.0 (loganshaffer87@gmail.com)"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
FUTURES_SPECS = {...}      # section 5.4
SESSION_GROUPS = {...}     # section 7
def home() -> str; def db_path() -> str; def log_path() -> str; def port() -> int
```

### 1.2 `units.py`

```python
def to_c(x: Decimal | str | int) -> int          # quantize 0.01 HALF_EVEN -> cents (ValueError on non-finite)
def from_c(c: int) -> Decimal                     # cents -> Decimal with 2 dp
def qprice(x) -> Decimal                          # canonical price/rate, at most 8 dp, str() round-trips
def mul_c(qty: int, px: Decimal, mult: Decimal, scale: Decimal = Decimal(1)) -> int   # to_c(qty*px*mult*scale)
def fx_c(amount_c: int, rate: Decimal) -> int     # base conversion, HALF_EVEN
```

### 1.3 `clock.py`

```python
NY = get_zone("America/New_York")                 # finsim.tzfallback
class Clock:                                      # injectable; tests pass FakeClock(at)
    def now(self) -> datetime                     # aware UTC
    def epoch(self) -> int
    def ny(self) -> datetime                      # aware New York
    def today(self) -> date                       # NY calendar date
def ny_of(epoch: int) -> datetime
def epoch_of(d: date, hhmm: str, tz=NY) -> int
def iso(d: date) -> str
```

### 1.4 `db.py`

```python
class Database:
    def __init__(self, path: str, clock: Clock, log)
    def open(self) -> None                        # creates/migrates schema, PRAGMAs, loads meta
    def close(self) -> None
    def reader(self) -> sqlite3.Connection        # per-thread connection (threading.local), WAL, read-only use
    @contextmanager
    def writer(self) -> Iterator["Tx"]            # acquires the process-wide RLock, BEGIN IMMEDIATE, COMMIT/ROLLBACK
    def backup(self, dest_dir: str, keep: int = 10) -> str       # sqlite online backup at startup
    def state_version(self) -> int                # meta.state_version (incremented by every committed command)
class Tx:                                          # thin wrapper handed to handlers
    conn: sqlite3.Connection; now: int; today: str
    def q(self, sql, params=()) -> list[sqlite3.Row]
    def one(self, sql, params=()) -> sqlite3.Row | None
    def x(self, sql, params=()) -> sqlite3.Cursor
SCHEMA: list[str]                                  # the DDL in section 2, applied in order; meta.schema_version gate
```

PRAGMAs on every connection: `journal_mode=WAL`, `synchronous=FULL`, `foreign_keys=ON`, `busy_timeout=5000`,
`temp_store=MEMORY`. The writer lock is a `threading.RLock`; readers never take it. Only `writer()` ever writes.

### 1.5 Where the rest is specified

`instruments.py` section 5; `ledger.py`/`accounting.py` sections 3-4; `commands.py` section 3; `quotes/*` and
`marks.py` section 6; `sessions.py` section 7; `orders.py`, `execution.py`, `lending.py` section 8; `lifecycle.py`,
`credit.py`, `fx.py` section 9; `api.py`, `server.py` sections 10-11; `static/` section 12; `app.py` section 13;
`hedges.py`, `compliance.py`, `risk.py` section 9.10.

---

## 2. SQLite schema (DDL)

Plain tables (no `STRICT`, so an older SQLite can still open the file). Types: `INTEGER` for cents/quantities/epochs,
`TEXT` for ids/dates/decimals/JSON.

```sql
-- 2.1 bookkeeping
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- keys: schema_version, state_version, created_ts, base_ccy, integrity_json, integrity_ts, instance_id

CREATE TABLE requests (
  request_id   TEXT PRIMARY KEY,
  kind         TEXT NOT NULL,            -- command kind
  actor        TEXT NOT NULL,            -- ui | matcher | lifecycle | cli | init
  received_ts  INTEGER NOT NULL,
  status       TEXT NOT NULL,            -- DONE | FAILED
  http_status  INTEGER NOT NULL,
  result_json  TEXT,                     -- stored response body (DONE) or error body (FAILED)
  entry_ids    TEXT                      -- JSON list of journal entry ids created (DONE)
);

CREATE TABLE jobs_done (
  job TEXT NOT NULL, key TEXT NOT NULL, run_ts INTEGER NOT NULL, result_json TEXT,
  PRIMARY KEY (job, key)
);

CREATE TABLE attention (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, level TEXT NOT NULL,   -- INFO | WARN | ERROR
  source TEXT NOT NULL, code TEXT NOT NULL, message TEXT NOT NULL, details_json TEXT,
  resolved_ts INTEGER
);
CREATE INDEX ix_attention_open ON attention(resolved_ts) WHERE resolved_ts IS NULL;

CREATE TABLE provider_health (
  provider TEXT PRIMARY KEY, last_ok_ts INTEGER, last_err_ts INTEGER, last_error TEXT,
  consecutive_errors INTEGER NOT NULL DEFAULT 0, requests_today INTEGER NOT NULL DEFAULT 0, day TEXT,
  down_until_ts INTEGER
);

CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);

-- 2.2 accounts and instruments
CREATE TABLE accounts (
  account_id   TEXT PRIMARY KEY,         -- TRS, USX, DEBT, CRED, FXM, PB:GS ...
  name         TEXT NOT NULL, kind TEXT NOT NULL,   -- TREASURY|US_EXCHANGE|DEBT|CREDIT|FOREIGN_FX|PRIME
  broker       TEXT, status TEXT NOT NULL DEFAULT 'OPEN', opened_ts INTEGER NOT NULL,
  sweep_out    INTEGER NOT NULL DEFAULT 1, buffer_c INTEGER NOT NULL DEFAULT 1000000,
  pb_financing INTEGER NOT NULL DEFAULT 0, settings_json TEXT
);

CREATE TABLE instruments (
  instrument_id TEXT PRIMARY KEY,        -- section 5.1 id rules
  kind TEXT NOT NULL,                    -- EQUITY|ETF|ADR|PREFERRED|OPTION|FUTURE|BOND|FX_PAIR|INDEX|YIELD
  symbol TEXT NOT NULL, name TEXT NOT NULL, currency TEXT NOT NULL,
  multiplier TEXT NOT NULL DEFAULT '1', tick TEXT NOT NULL DEFAULT '0.01', lot INTEGER NOT NULL DEFAULT 1,
  price_scale TEXT NOT NULL DEFAULT '1',  -- quoted price -> currency units (0.01 for pence; grains quoted in cents)
  session_group TEXT NOT NULL, settle_market TEXT NOT NULL,     -- settle_market = finsim.calendar SettlementConfig key
  account_kinds TEXT NOT NULL,            -- JSON list of account kinds allowed to trade it
  yahoo_symbol TEXT, cboe_symbol TEXT, underlying_id TEXT,
  expiry TEXT, strike TEXT, right TEXT, style TEXT, exercise TEXT,       -- options
  spec_json TEXT,                          -- futures/bond/option specs (section 5)
  status TEXT NOT NULL DEFAULT 'ACTIVE',   -- ACTIVE|EXPIRED|DELISTED|LIMITED|UNSUPPORTED
  created_ts INTEGER NOT NULL, updated_ts INTEGER NOT NULL
);
CREATE INDEX ix_instruments_sym ON instruments(symbol);
CREATE INDEX ix_instruments_under ON instruments(underlying_id, expiry);

CREATE TABLE exchange_sessions (          -- foreign listings: Yahoo currentTradingPeriod per exchange per day
  exchange TEXT NOT NULL, day TEXT NOT NULL, tz TEXT NOT NULL, open_ts INTEGER NOT NULL, close_ts INTEGER NOT NULL,
  fetched_ts INTEGER NOT NULL, PRIMARY KEY (exchange, day)
);

-- 2.3 the ledger (single source of truth)
CREATE TABLE journal_entries (
  entry_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id  TEXT NOT NULL REFERENCES requests(request_id),
  ts          INTEGER NOT NULL, business_date TEXT NOT NULL,
  kind        TEXT NOT NULL,              -- CAPITAL|TRANSFER|SWEEP_IN|SWEEP_OUT|FILL|SETTLE|FEE|ACCRUAL|PAYMENT|VM|IM|COLLATERAL|BORROW|LOAN|FX|COUPON|EXPIRY|EXERCISE|ASSIGN|ADJUST
  memo        TEXT NOT NULL,
  ref_type    TEXT, ref_id TEXT,          -- fill|order|settlement|borrow|loan|transfer|job
  idem_key    TEXT NOT NULL UNIQUE        -- "fill:<fill_id>", "settle:<settle_id>", "accrual:BORROW_FEE:<borrow_id>:<date>" ...
);
CREATE TABLE journal_lines (
  line_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id    INTEGER NOT NULL REFERENCES journal_entries(entry_id),
  account_id  TEXT NOT NULL REFERENCES accounts(account_id),
  gl          TEXT NOT NULL,              -- chart code, section 3.1
  currency    TEXT NOT NULL,
  amount_c    INTEGER NOT NULL,           -- signed: debit > 0, credit < 0, in `currency`
  base_c      INTEGER NOT NULL,           -- signed USD equivalent at the posting rate
  instrument_id TEXT, lot_id INTEGER, memo TEXT
);
CREATE INDEX ix_lines_acct ON journal_lines(account_id, gl, currency);
CREATE INDEX ix_lines_inst ON journal_lines(account_id, instrument_id, gl);
CREATE INDEX ix_lines_entry ON journal_lines(entry_id);

-- 2.4 flows
CREATE TABLE transfers (
  transfer_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, ts INTEGER NOT NULL, business_date TEXT NOT NULL,
  kind TEXT NOT NULL,                     -- INITIAL_CAPITAL|DEPOSIT|WITHDRAWAL|TRANSFER|SWEEP_IN|SWEEP_OUT
  from_account TEXT, to_account TEXT, currency TEXT NOT NULL, amount_c INTEGER NOT NULL,
  linked_type TEXT, linked_id TEXT,       -- the fill / settlement / collateral call a sweep paid for
  entry_id INTEGER NOT NULL REFERENCES journal_entries(entry_id),
  idem_key TEXT NOT NULL UNIQUE
);

-- 2.5 orders, fills, lots, settlements
CREATE TABLE orders (
  order_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, account_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
  side TEXT NOT NULL,                     -- BUY | SELL
  qty INTEGER NOT NULL, order_type TEXT NOT NULL,   -- MARKET|LIMIT|STOP|STOP_LIMIT
  limit_px TEXT, stop_px TEXT, tif TEXT NOT NULL,    -- DAY | GTC
  status TEXT NOT NULL,                   -- WORKING|PARTIALLY_FILLED|FILLED|CANCELLED|REJECTED|EXPIRED
  filled_qty INTEGER NOT NULL DEFAULT 0, avg_px TEXT,
  created_ts INTEGER NOT NULL, updated_ts INTEGER NOT NULL, expires_ts INTEGER NOT NULL,
  session_group TEXT NOT NULL, triggered INTEGER NOT NULL DEFAULT 0, last_bar_ts INTEGER,
  first_eval_done INTEGER NOT NULL DEFAULT 0,       -- limit: first evaluation fills at the observed price, later at the limit
  opens_short INTEGER NOT NULL DEFAULT 0, borrow_id TEXT, package_id TEXT, parent_order_id TEXT,
  reason TEXT, preview_json TEXT NOT NULL
);
CREATE INDEX ix_orders_open ON orders(status, instrument_id) WHERE status IN ('WORKING','PARTIALLY_FILLED');
CREATE INDEX ix_orders_acct ON orders(account_id, created_ts);

CREATE TABLE order_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL REFERENCES orders(order_id), ts INTEGER NOT NULL,
  from_status TEXT, to_status TEXT NOT NULL, note TEXT NOT NULL, quote_json TEXT
);

CREATE TABLE fills (
  fill_id TEXT PRIMARY KEY, order_id TEXT REFERENCES orders(order_id), account_id TEXT NOT NULL,
  instrument_id TEXT NOT NULL, side TEXT NOT NULL, qty INTEGER NOT NULL, price TEXT NOT NULL,
  gross_c INTEGER NOT NULL, fee_c INTEGER NOT NULL, currency TEXT NOT NULL, fx_rate TEXT NOT NULL,
  ts INTEGER NOT NULL, trade_date TEXT NOT NULL, settle_date TEXT,
  origin TEXT NOT NULL,                   -- ORDER|EXERCISE|ASSIGNMENT|EXPIRY|FUTURES_FINAL|BUY_IN|RETURN|HEDGE
  quote_json TEXT NOT NULL,               -- the full QuoteRecord used (provenance; replay needs no network)
  entry_id INTEGER NOT NULL REFERENCES journal_entries(entry_id),
  idem_key TEXT NOT NULL UNIQUE           -- "match:<order_id>:<quote_time>:<bar_ts>" | "job:<name>:<key>"
);
CREATE INDEX ix_fills_pos ON fills(account_id, instrument_id, ts);

CREATE TABLE lots (
  lot_id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
  side TEXT NOT NULL,                     -- LONG | SHORT
  open_fill_id TEXT NOT NULL REFERENCES fills(fill_id), open_ts INTEGER NOT NULL,
  qty_open INTEGER NOT NULL, qty_remaining INTEGER NOT NULL, unit_cost TEXT NOT NULL,
  cost_c INTEGER NOT NULL,                -- remaining cost (LONG) or remaining proceeds (SHORT), instrument currency
  base_c INTEGER NOT NULL, currency TEXT NOT NULL
);
CREATE INDEX ix_lots_open ON lots(account_id, instrument_id, side) WHERE qty_remaining > 0;

CREATE TABLE lot_relief (
  id INTEGER PRIMARY KEY AUTOINCREMENT, close_fill_id TEXT NOT NULL REFERENCES fills(fill_id),
  lot_id INTEGER NOT NULL REFERENCES lots(lot_id), qty INTEGER NOT NULL,
  cost_relieved_c INTEGER NOT NULL, proceeds_c INTEGER NOT NULL, realised_c INTEGER NOT NULL
);

CREATE TABLE settlements (
  settle_id TEXT PRIMARY KEY, fill_id TEXT REFERENCES fills(fill_id), account_id TEXT NOT NULL,
  currency TEXT NOT NULL, amount_c INTEGER NOT NULL,      -- + receivable (we receive cash), - payable
  gl TEXT NOT NULL,                        -- 1200 | 2100 | 1250 | 2350
  due_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',   -- PENDING | SETTLED
  settled_ts INTEGER, entry_id INTEGER,
  UNIQUE (fill_id, gl)
);
CREATE INDEX ix_settle_due ON settlements(status, due_date);

CREATE VIEW v_positions AS
  SELECT account_id, instrument_id,
         SUM(CASE WHEN side='LONG' THEN qty_remaining ELSE -qty_remaining END) AS qty,
         SUM(CASE WHEN side='LONG' THEN cost_c ELSE -cost_c END) AS cost_c,
         SUM(CASE WHEN side='LONG' THEN base_c ELSE -base_c END) AS base_c,
         MIN(currency) AS currency
  FROM lots WHERE qty_remaining > 0 GROUP BY account_id, instrument_id;

-- 2.6 financing and derivatives state
CREATE TABLE borrows (
  borrow_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, account_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
  qty INTEGER NOT NULL, qty_returned INTEGER NOT NULL DEFAULT 0,
  fee_rate TEXT NOT NULL, rebate_rate TEXT NOT NULL, tier TEXT NOT NULL, lender TEXT NOT NULL,
  collateral_c INTEGER NOT NULL, currency TEXT NOT NULL, opened_ts INTEGER NOT NULL, opened_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'OPEN', closed_ts INTEGER, order_id TEXT, last_accrual_date TEXT, recall_notice TEXT
);
CREATE TABLE loans (
  loan_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, account_id TEXT NOT NULL, currency TEXT NOT NULL,
  principal_c INTEGER NOT NULL, index_name TEXT NOT NULL, spread TEXT NOT NULL, daycount TEXT NOT NULL,
  opened_date TEXT NOT NULL, maturity_date TEXT, status TEXT NOT NULL DEFAULT 'OPEN', last_accrual_date TEXT,
  lender TEXT NOT NULL
);
CREATE TABLE repos (                      -- thin slice, section 9.9
  repo_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, account_id TEXT NOT NULL, side TEXT NOT NULL,  -- REPO|REVERSE
  instrument_id TEXT NOT NULL, face INTEGER NOT NULL, price TEXT NOT NULL, haircut TEXT NOT NULL,
  principal_c INTEGER NOT NULL, rate TEXT NOT NULL, opened_date TEXT NOT NULL, maturity_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'OPEN', last_accrual_date TEXT
);
CREATE TABLE fx_forwards (
  fwd_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, account_id TEXT NOT NULL, buy_ccy TEXT NOT NULL,
  sell_ccy TEXT NOT NULL, buy_c INTEGER NOT NULL, sell_c INTEGER NOT NULL, rate TEXT NOT NULL,
  value_date TEXT NOT NULL, opened_ts INTEGER NOT NULL, inputs_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN'
);
CREATE TABLE futures_settle (            -- daily variation-margin state per position
  account_id TEXT NOT NULL, instrument_id TEXT NOT NULL, settle_date TEXT NOT NULL,
  settle_px TEXT NOT NULL, qty_after INTEGER NOT NULL, vm_c INTEGER NOT NULL, quote_json TEXT NOT NULL,
  PRIMARY KEY (account_id, instrument_id, settle_date)
);
CREATE TABLE hedge_packages (
  package_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, account_id TEXT NOT NULL, primary_order_id TEXT NOT NULL,
  legs_json TEXT NOT NULL, status TEXT NOT NULL,   -- PENDING|PARTIAL|DONE|CANCELLED
  created_ts INTEGER NOT NULL
);

-- 2.7 marks, snapshots, quotes
CREATE TABLE marks (                      -- end-of-day mark per held instrument (provenance kept)
  as_of_date TEXT NOT NULL, instrument_id TEXT NOT NULL, mark TEXT NOT NULL, quality TEXT NOT NULL,
  source TEXT NOT NULL, method TEXT NOT NULL, quote_time INTEGER, fetched_at INTEGER, delay_label TEXT NOT NULL,
  inputs_json TEXT, PRIMARY KEY (as_of_date, instrument_id)
);
CREATE TABLE nav_snapshots (
  account_id TEXT NOT NULL, as_of_date TEXT NOT NULL, ts INTEGER NOT NULL,
  nav_c INTEGER NOT NULL, settled_c INTEGER NOT NULL, pending_c INTEGER NOT NULL, mv_long_c INTEGER NOT NULL,
  mv_short_c INTEGER NOT NULL, unrealised_c INTEGER NOT NULL, realised_cum_c INTEGER NOT NULL,
  flows_day_c INTEGER NOT NULL,           -- external + Treasury flows that day (signed, into the account)
  pnl_day_c INTEGER NOT NULL, degraded INTEGER NOT NULL DEFAULT 0, detail_json TEXT NOT NULL,
  PRIMARY KEY (account_id, as_of_date)
);                                         -- account_id 'ALL' = consolidated row
CREATE TABLE quote_cache (                -- persisted copy of the in-memory cache (last valid mark survives restart)
  instrument_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, updated_ts INTEGER NOT NULL
);
CREATE TABLE curve_points (               -- Treasury par curve by day; FRED series by day
  series TEXT NOT NULL, as_of TEXT NOT NULL, tenor TEXT NOT NULL, value TEXT NOT NULL, fetched_ts INTEGER NOT NULL,
  PRIMARY KEY (series, as_of, tenor)
);
CREATE TABLE watchlists (
  id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL, name TEXT NOT NULL, instrument_id TEXT NOT NULL,
  position INTEGER NOT NULL, UNIQUE (account_id, name, instrument_id)
);
```

**Idempotency enforced by the schema**: `requests.request_id` (command replay), `journal_entries.idem_key` (no
duplicate postings), `fills.idem_key` (no duplicate fill for the same order+quote+bar), `transfers.idem_key` (no
duplicate sweep/deposit), `jobs_done(job,key)` (no duplicate lifecycle run), `futures_settle` PK (one VM per position
per day), `marks` / `nav_snapshots` PKs (one per day), `settlements UNIQUE(fill_id, gl)` (one settlement per fill leg).

**Migrations**: `meta.schema_version`; `db.py` holds `MIGRATIONS: list[tuple[int, list[str]]]`, applied inside one
transaction at open. A file newer than the code refuses to open (exit code 4) rather than guess.

**Backups**: `Database.backup()` runs at every `serve` start into `<home>/backups/terminal-YYYYmmdd-HHMMSS.db` (newest
10 kept), exactly like the old `EventStore.snapshot`. `finsim.db` is never opened by the terminal.

---

## 3. Ledger and the CommandBus

### 3.1 Chart of accounts (`ledger.CHART`)

Per-account ledger (the `account_id` dimension), one chart. Codes keep the old numbering where the meaning is the same so
the owner's glossary still reads.

| GL | Name | Type | Dimensions used |
|---|---|---|---|
| 1010 | Cash | ASSET | currency |
| 1100 | Securities at cost (equities, ETFs, ADRs, preferreds) | ASSET | currency, instrument, lot |
| 1110 | Bonds at cost | ASSET | currency, instrument, lot |
| 1120 | Accrued interest receivable - bonds | ASSET | currency, instrument |
| 1200 | Receivable - securities sold (pending settlement) | ASSET | currency |
| 1210 | Dividends receivable | ASSET | currency, instrument |
| 1230 | Accrued interest receivable - cash | ASSET | currency |
| 1250 | FX settlement receivable | ASSET | currency |
| 1300 | Futures initial margin deposit | ASSET | currency, instrument |
| 1400 | Cash collateral posted - securities borrowed | ASSET | currency, instrument (borrow in memo/ref) |
| 1410 | Accrued rebate receivable | ASSET | currency |
| 1500 | Reverse repo receivable | ASSET | currency |
| 1700 | Options purchased at cost | ASSET | currency, instrument, lot |
| 2100 | Payable - securities purchased (pending settlement) | LIABILITY | currency |
| 2300 | Accrued interest payable - PB financing | LIABILITY | currency |
| 2310 | Accrued borrow fees payable | LIABILITY | currency |
| 2320 | Payments in lieu payable | LIABILITY | currency, instrument |
| 2350 | FX settlement payable | LIABILITY | currency |
| 2500 | Repo borrowing | LIABILITY | currency |
| 2600 | Securities sold short (at proceeds) | LIABILITY | currency, instrument, lot |
| 2700 | Prime-broker financing | LIABILITY | currency |
| 2750 | Currency loans - principal | LIABILITY | currency |
| 2755 | Accrued interest payable - currency loans | LIABILITY | currency |
| 2900 | Options written (at premium received) | LIABILITY | currency, instrument, lot |
| 3000 | Contributed capital | EQUITY | Treasury only; currency |
| 3100 | Treasury funding (advances: debit in Treasury, credit in the funded account) | EQUITY | currency |
| 4000 | Realised gain/loss - securities and options | INCOME | currency, instrument |
| 4200 | Dividend income | INCOME | currency, instrument |
| 4300 | Interest income (cash, coupons) | INCOME | currency, instrument |
| 4350 | Rebate income - cash collateral | INCOME | currency |
| 4400 | Futures variation margin | INCOME | currency, instrument |
| 4500 | Reverse repo interest income | INCOME | currency |
| 4650 | Realised FX gain/loss | INCOME | currency |
| 5000 | Commissions and fees | EXPENSE | currency, instrument |
| 5100 | PB financing interest | EXPENSE | currency |
| 5150 | Currency loan interest | EXPENSE | currency |
| 5300 | Securities borrow fees | EXPENSE | currency, instrument |
| 5400 | Payments in lieu (dividends on shorts) | EXPENSE | currency, instrument |
| 5500 | Repo interest | EXPENSE | currency |
| 5700 | Buy-in penalties and other charges | EXPENSE | currency |

Not in the chart on purpose: unrealised accounts (4100/1150), FX translation (4600), forward MTM (1600/2800), OTC PV
(1800) - these are overlays computed from marks (A3). `3100` nets to zero across all accounts; consolidated NAV simply
sums account NAVs.

### 3.2 `ledger.py` API

```python
@dataclass(frozen=True)
class Line:
    account_id: str; gl: str; currency: str; amount_c: int; base_c: int
    instrument_id: str | None = None; lot_id: int | None = None; memo: str = ""

def dr(account_id, gl, currency, amount_c, base_c=None, *, instrument_id=None, lot_id=None, memo="") -> Line
def cr(account_id, gl, currency, amount_c, base_c=None, *, instrument_id=None, lot_id=None, memo="") -> Line
    # amount_c is the absolute amount; a negative amount flips the side (as the old dr/cr did). base_c defaults to
    # amount_c when currency == USD; for a foreign currency the caller passes base_c or an fx_rate.

@dataclass
class Entry:
    kind: str; memo: str; lines: list[Line]; idem_key: str; ref_type: str | None = None; ref_id: str | None = None

class LedgerError(Exception): ...
class DuplicatePosting(LedgerError): ...        # idem_key already used (surfaced as 409 DUPLICATE)

def post(tx: Tx, request_id: str, entry: Entry, business_date: str | None = None) -> int
    # validation: lines non-empty; every gl in CHART; sum(base_c) == 0; for an entry whose lines share one currency,
    # sum(amount_c) == 0 as well; account_id exists. Inserts journal_entries + journal_lines; returns entry_id.
    # Records entry_id in tx.touched_entries and account ids in tx.touched_accounts for the quick integrity check.

def balance(conn, account_id: str, gl: str, currency: str | None = None, instrument_id: str | None = None) -> int
    # natural sign: ASSET/EXPENSE debit-positive, LIABILITY/EQUITY/INCOME credit-positive; in `currency` units (cents)
def base_balance(conn, account_id, gl, currency=None, instrument_id=None) -> int      # same, USD cents
def balances_by(conn, account_id: str, gl_prefix: str = "") -> dict[tuple[str, str, str | None], int]   # (gl, ccy, inst) -> natural-sign cents
def avg_base_rate(conn, account_id: str, currency: str) -> Decimal | None   # base_c / amount_c of the 1010 balance

def cash_lines(conn, account_id: str, currency: str, amount_c: int, *, rate: Decimal | None, memo: str = "") -> list[Line]
    # the 1010 line for a cash movement (+ inflow / - outflow). USD: base = amount. Foreign inflow: base at `rate`.
    # Foreign outflow: base relieved at avg_base_rate(); the difference to amount*rate is a 4650 line (realised FX).

# lots
@dataclass
class Relief: lot_id: int; qty: int; cost_relieved_c: int; proceeds_c: int; realised_c: int
def open_lot(tx, account_id, instrument_id, side, fill_id, qty, unit_cost: Decimal, cost_c: int, base_c: int, currency) -> int
def relieve_fifo(tx, account_id, instrument_id, side, qty, close_fill_id, proceeds_per_unit_c: int, mult: Decimal) -> list[Relief]
    # oldest open lot first; writes lot_relief rows and reduces qty_remaining/cost_c/base_c pro rata; raises InsufficientLots
def position_qty(conn, account_id, instrument_id) -> tuple[int, int, int]   # (qty, cost_c, base_c) from v_positions
```

### 3.3 Standard postings (every product, so parallel builders post identically)

Notation: `Dr gl` / `Cr gl`; amounts in the instrument currency with base at the fill's `fx_rate` (USD = 1).

| Event | Lines |
|---|---|
| Initial capital / deposit (Treasury) | Dr 1010 / Cr 3000 |
| Withdrawal | Dr 3000 / Cr 1010 (refused if Treasury settled cash < amount) |
| Transfer Treasury -> account (incl. SWEEP_IN) | Treasury: Dr 3100 / Cr 1010; account: Dr 1010 / Cr 3100 (one entry, four lines) |
| Transfer account -> Treasury (incl. SWEEP_OUT) | account: Dr 3100 / Cr 1010; Treasury: Dr 1010 / Cr 3100 |
| Equity/ETF/ADR/bond BUY fill | Dr 1100 (1110) gross / Dr 5000 fee / Cr 2100 gross+fee; settlement row (-gross-fee, due T+n) |
| Equity SELL long (FIFO) | Dr 1200 gross-fee / Dr 5000 fee / Cr 1100 cost relieved / Cr(or Dr) 4000 realised |
| Equity SELL short (opens short lot) | Dr 1200 gross-fee / Dr 5000 fee / Cr 2600 gross |
| Equity BUY to cover (FIFO on short lots) | Dr 2600 proceeds relieved / Dr 5000 fee / Cr 2100 gross+fee / Cr(or Dr) 4000 realised |
| Settlement of a receivable | Dr 1010 / Cr 1200 (or 1250) |
| Settlement of a payable | Dr 2100 (or 2350) / Cr 1010 (sweep-in first if 1010 would go below zero) |
| Bond accrued interest bought/sold | buy: Dr 1120 accrued / Cr 2100; sell: Dr 1200 / Cr 1120 (accrued relieved) |
| Bond daily accrual | Dr 1120 / Cr 4300 (per instrument per day) |
| Coupon paid | Dr 1010 coupon / Cr 1120 accrued (true-up delta to 4300) |
| Option BUY to open | Dr 1700 premium / Dr 5000 fee / Cr 2100 (T+1) |
| Option SELL to open | Dr 1200 / Dr 5000 fee / Cr 2900 premium |
| Option close (either side) | FIFO as for equities against 1700 or 2900, realised to 4000 |
| Option expiry OTM | long: Dr 4000 / Cr 1700; short: Dr 2900 / Cr 4000 |
| Exercise (long call) | Dr 1100 strike*qty*100 + premium / Cr 1700 premium / Cr 2100 strike*qty*100 |
| Exercise (long put) | Dr 1200 strike*qty*100 / Cr 1700 premium / Cr 1100 cost relieved (FIFO) / Cr or Dr 4000 realised = strike proceeds - premium - cost relieved (no long shares: Cr 2600 instead of 1100 opens a short, borrow required - section 9.4) |
| Assignment (short put) | Dr 1100 strike*qty*100 - premium / Dr 2900 premium / Cr 2100 strike*qty*100 |
| Assignment (short call) | Dr 1200 strike*qty*100 / Dr 2900 premium / Cr 1100 cost relieved / 4000 realised = strike + premium - cost (short stock if no long: Cr 2600 instead of 1100, requires borrow - see 9.4) |
| Cash-settled index option at expiry | long: Dr 1200 intrinsic / Cr 1700 premium / 4000 diff; short: Dr 2900 premium / Cr 2100 intrinsic / 4000 diff |
| Futures fill | Dr 5000 fee / Cr 1010 fee (same day); IM: Dr 1300 / Cr 1010 (per contract spec); release on close: Dr 1010 / Cr 1300 |
| Futures variation margin | gain: Dr 1010 / Cr 4400; loss: Dr 4400 / Cr 1010 |
| Borrow open | Dr 1400 collateral (102% * qty * mark) / Cr 1010 |
| Collateral mark (daily) | call: Dr 1400 / Cr 1010; return: Dr 1010 / Cr 1400 |
| Borrow fee accrual | Dr 5300 / Cr 2310 (daily); rebate: Dr 1410 / Cr 4350 (daily) |
| Monthly fee/rebate settlement | Dr 2310 / Cr 1010; Dr 1010 / Cr 1410 |
| Borrow return | Dr 1010 collateral / Cr 1400; fees settled as above |
| Currency loan draw | Dr 1010:ccy / Cr 2750:ccy |
| Currency loan accrual | Dr 5150 / Cr 2755 (daily, ccy, base at the day's rate) |
| Currency loan repay | Dr 2750 principal / Dr 2755 accrued / Cr 1010:ccy |
| FX spot (trade date) | Dr 1250:buy_ccy / Cr 2350:sell_ccy (base of both legs = USD value at the dealt rate; balanced) |
| FX spot settlement (T+2) | Dr 1010:buy / Cr 1250:buy; Dr 2350:sell / Cr 1010:sell (+ 4650 vs the sell ccy's average rate) |
| Dividend ex-date (long) | Dr 1210 / Cr 4200; pay: Dr 1010 / Cr 1210. Short: Dr 5400 / Cr 2320; pay: Dr 2320 / Cr 1010 |
| Treasury cash interest | daily Dr 1230 / Cr 4300; monthly Dr 1010 / Cr 1230 |
| PB financing | draw: Dr 1010 / Cr 2700; daily Dr 5100 / Cr 2300; monthly Dr 2300 / Cr 1010; repay Dr 2700 / Cr 1010 |

### 3.4 `commands.py` - the CommandBus contract

```python
@dataclass(frozen=True)
class Command:
    request_id: str            # client UUID4 or "job:<name>:<key>" / "match:<order_id>:<quote_time>:<bar_ts>"
    kind: str                  # see catalogue below
    payload: dict              # JSON-safe, validated by the handler
    actor: str                 # ui | matcher | lifecycle | cli | init
    account_id: str | None = None

@dataclass
class Result:
    ok: bool; http: int; body: dict; replayed: bool = False; entry_ids: list[int] = field(default_factory=list)

class CommandError(Exception):          # a rejection the caller can act on; stored against the request_id
    def __init__(self, code: str, message: str, http: int = 400, details: dict | None = None)
class Conflict(CommandError): http = 409 # QUOTE_STALE, SESSION_CLOSED_MARKET, INSUFFICIENT_FUNDS, NO_BORROW, DUPLICATE ...
class IntegrityViolation(Exception): ... # the quick check failed: transaction rolled back, 500, attention row

Handler = Callable[[Tx, Command, Services], dict]     # returns the response body; raises CommandError to reject

class CommandBus:
    def __init__(self, db: Database, services: Services, clock: Clock, log)
    def register(self, kind: str, handler: Handler) -> None
    def execute(self, cmd: Command) -> Result
    def on_commit(self, listener: Callable[[Command, Result], None]) -> None   # run after COMMIT, outside the lock
```

`execute` - the exact algorithm:

1. `request_id` must match `^[A-Za-z0-9:_.-]{8,64}$`, else `Result(False, 400, {error: BAD_REQUEST_ID})` (not stored).
2. `with db.writer() as tx` (RLock; `BEGIN IMMEDIATE`):
   1. `row = requests[request_id]`. If present and `row.kind == cmd.kind`: return the stored body with the stored
      http status, `replayed=True`. If present with another kind: 409 `REQUEST_ID_REUSED` (not stored).
   2. Unknown kind: 404 `UNKNOWN_COMMAND` (not stored).
   3. `body = handler(tx, cmd, services)`.
   4. Quick integrity on `tx.touched_entries` / `tx.touched_accounts` (section 4.6, checks Q1-Q4). Failure raises
      `IntegrityViolation`.
   5. `INSERT requests (DONE, 200, body, entry_ids)`; `meta.state_version += 1`; COMMIT.
3. On `CommandError`: ROLLBACK everything the handler did, then in a **new** short transaction
   `INSERT requests (FAILED, e.http, error body)`. The same request_id replays the same rejection; a retry after fixing
   the input needs a new id. The order of operations makes a rejection free of side effects.
4. On `sqlite3.OperationalError` (busy/locked): ROLLBACK, nothing stored, `Result(False, 503, {error: RETRY})`; the
   HTTP layer adds `Retry-After: 1`. The UI retries the same request_id up to 3 times.
5. On any other exception or `IntegrityViolation`: ROLLBACK, `log.exception`, attention row (`ERROR`, source
   `bus`, code `COMMAND_FAILED` or `INTEGRITY`), `Result(False, 500, {...})`. Nothing stored: a retry is allowed.
6. After COMMIT: listeners run outside the lock (quote subscriptions for new instruments, matcher wake-up, UI counter).

Error body shape everywhere: `{"error": {"code": "QUOTE_STALE", "message": "…human…", "details": {...}}}`.

**How background jobs call it**: the lifecycle scheduler and the matcher construct `Command`s with deterministic
request ids and `actor` lifecycle/matcher and call `bus.execute` exactly like the API does. They never touch `Tx`
outside a handler. A job that is replayed (same id) gets the stored result and does nothing - that is the restart
safety. The matcher additionally relies on `fills.idem_key` so that two matcher passes on the same observed bar can
never fill twice.

**Command catalogue** (kind -> payload -> module owning the handler):

| kind | payload | module |
|---|---|---|
| INIT_BOOK | {} | accounting (first run: accounts, initial capital, request_id `init:capital:v1`) |
| DEPOSIT_CAPITAL / WITHDRAW_CAPITAL | currency, amount_c, memo | accounting |
| TRANSFER | from_account, to_account, currency, amount_c, memo | accounting |
| OPEN_ACCOUNT | kind=PRIME, broker | accounting |
| UPDATE_ACCOUNT | account_id, sweep_out?, buffer_c?, pb_financing? | accounting |
| SUBMIT_ORDER | account_id, instrument_id, side, qty, order_type, limit_px?, stop_px?, tif, borrow?: bool, hedge?: HedgeSpec, snapshot_id | orders + execution |
| CANCEL_ORDER | order_id | orders |
| FILL_ORDER | order_id, qty, price, quote (QuoteRecord), bar_ts, first_eval | execution (matcher only) |
| EXPIRE_ORDER | order_id, reason | orders (lifecycle only) |
| BORROW | account_id, instrument_id, qty, locate (LocateQuote) | lending |
| RETURN_BORROW | borrow_id, qty | lending |
| OPEN_LOAN / REPAY_LOAN | account_id, currency, amount_c, term_days / loan_id, amount_c | credit |
| PB_DRAW / PB_REPAY | account_id, amount_c | credit |
| FX_SPOT / FX_FORWARD | account_id, buy_ccy, sell_ccy, amount_c, amount_ccy, quote, value_date? | fx |
| EXERCISE_OPTION | account_id, instrument_id, qty | execution |
| SETTLE_DUE, ACCRUE_DAILY, PAY_MONTHLY, COUPON, DIVIDEND, OPTION_EXPIRY, FUTURES_VM, FUTURES_FINAL, COLLATERAL_MARK, SWEEP_OUT, EOD_SNAPSHOT, EXPIRE_ORDERS, HOUSEKEEPING | job-specific key fields | lifecycle |
| RESOLVE_ATTENTION | id | accounting |

---

## 4. Accounting read models (`accounting.py`) and `integrity()`

One module computes every figure the UI shows. All functions take an `account_id` or `'ALL'` and read through
`db.reader()` (no writer lock). Marks come from `marks.MarkProvider` (section 6.5). All results are dataclasses whose
cents fields end in `_c` and whose JSON form converts them to decimal strings.

```python
class Accounting:
    def __init__(self, db: Database, instruments: InstrumentMaster, marks: MarkProvider, clock: Clock)
    def cash(self, account_id: str) -> dict[str, CashView]
    def available(self, account_id: str, currency: str) -> int
    def buying_power(self, account_id: str, currency: str) -> BuyingPower
    def positions(self, account_id: str | None = None) -> list[PositionView]
    def position(self, account_id: str, instrument_id: str) -> PositionView | None
    def balance_sheet(self, scope: str) -> BalanceSheet
    def nav(self, scope: str) -> NavView
    def pnl(self, scope: str, period: str) -> PnlView                 # TODAY | MTD | YTD | ITD
    def treasury(self) -> TreasuryView
    def transactions(self, scope: str, *, from_date=None, to_date=None, kinds=None, instrument_id=None, limit=500, offset=0) -> list[TxnRow]
    def obligations(self, account_id: str) -> ObligationsView
    def flows(self, scope: str, from_date: str, to_date: str) -> int      # external + treasury flows, signed into scope
    def integrity(self, scope: str = "full", touched: Touched | None = None) -> IntegrityReport
```

### 4.1 Cash per (account, currency) - exact definitions

```
settled_c      = balance(1010)
receivable_c   = balance(1200) + balance(1250)                      # cash we will receive (pending in)
payable_c      = balance(2100) + balance(2350)                      # cash we will pay (pending out)
pending_c      = receivable_c - payable_c
restricted_c   = balance(2600)                                      # short-sale proceeds, restricted until covered
reserved_c     = sum over WORKING/PARTIALLY_FILLED BUY orders in this currency of
                 remaining_qty * ref_px * multiplier * price_scale + est_fee,
                 ref_px = limit_px (LIMIT/STOP_LIMIT) else last_mark * 1.02 (MARKET/STOP)
option_req_c   = short option margin requirement (section 8.7), USD only
available_c    = settled_c + pending_c - restricted_c - reserved_c - option_req_c
```
`available_c` is what a new trade can spend before any sweep. Negative `available_c` is shown in red and means a
margin/collateral call is outstanding (section 9.7 raises an attention row).

### 4.2 Buying power

```
own_c       = max(0, available_c(account, ccy))
treasury_c  = max(0, available_c("TRS", ccy))                      # what a sweep-in could bring (0 for the Treasury itself)
pb_c        = pb_headroom_c(account) if account.pb_financing else 0 # PRIME accounts: min(limit, 2*NAV) - balance(2700)
total_c     = own_c + treasury_c + pb_c
```
The ticket shows the three lines separately and the formula text ("own settled + pending - restricted - reserved").

### 4.3 Positions

From `v_positions` joined to lots (for lot detail) and `marks`:

```
qty, cost_c, base_cost_c           from v_positions (qty > 0 long, < 0 short)
avg_cost                            = cost_c / (|qty| * multiplier * price_scale)   (price units)
mark                                = marks.mark_for(instrument).mark   (None -> unavailable)
mv_c (securities, options)          = qty * mark * multiplier * price_scale   (signed; bonds: qty*mark/100 + accrued 1120)
mv_c (futures)                      = 0   (margined daily; exposure shown as notional = qty*mark*mult)
unrealised_c (long)                 = mv_c - cost_c
unrealised_c (short)                = cost_c(=proceeds) - |mv_c|
unrealised_c (futures)              = qty * (mark - last_settle_px) * mult   (unsettled VM since the last settlement)
realised_itd_c                      = balance(4000, instrument) + balance(4400, instrument)
income_itd_c                        = balance(4200, instrument) + balance(4300, instrument)
financing_itd_c                     = -(balance(5300, instrument) + balance(5400, instrument))
fees_itd_c                          = -balance(5000, instrument)
weight                              = mv_c(base) / nav_c(account)
days_held                           = today - oldest open lot date
```
Each `PositionView` carries the full `MarkView` (quality, source, quote_time, delay_label, method, inputs) so the UI
can show freshness on every row.

### 4.4 NAV, equity, balance sheet

```
equity_at_cost_c(account) = sum over ASSET gl of base_balance - sum over LIABILITY gl of base_balance
                            (every gl in the chart; cash, receivables, cost of positions, margin, collateral, accruals,
                             payables, short proceeds, loans, written options)
overlay_c(account)        = sum unrealised_c(positions, in base)
                          + fx_translation_c   (for each foreign ccy: settled+pending amount * spot_now - its base carrying)
                          + sum fx_forward_pv_c (section 6.5.2)
nav_c(account)            = equity_at_cost_c + overlay_c
equity                    = nav (the terminal uses the two words for the same figure; shown as "Equity (NAV)")
nav_c('ALL')              = sum over accounts (3100 nets out; nothing else is intercompany)
```
`balance_sheet(scope)` lists assets and liabilities at cost from the ledger plus the overlay lines ("Unrealised on
positions", "FX translation", "FX forwards at CIP") and a NAV row; `assets_c - liabilities_c == nav_c` by
construction and integrity check I5 asserts it.

### 4.5 P&L periods and Treasury

```
baseline(scope, period):  TODAY -> nav_snapshots row for the last trading day before today
                          MTD   -> last trading day of the previous month; YTD -> previous year; ITD -> none
pnl_c(scope, period)      = nav_c(scope, now) - baseline.nav_c - flows_c(scope, baseline_date+1, today)
                            (ITD: nav_c - cumulative flows; for 'ALL' the flows are deposits/withdrawals only; for an
                             account they also include Treasury transfers and sweeps - an account's P&L is never changed
                             by funding)
explain (same period)     = { realised: delta(4000+4400+4650), income: delta(4200+4300+4350+4500),
                              fees: -delta(5000), financing: -delta(5100+5150+5300+5400+5500+5700),
                              unrealised: pnl_c - sum(the above) }   # the residual is the change in the overlay
```
If no snapshot exists for the baseline (first day, or the EOD job missed) the view says `baseline: inception` and
flags `degraded=True`; it never invents a baseline.

```
TreasuryView: cash by ccy (settled/pending), contributed_capital_c = balance(3000), advances_c = balance(3100 in TRS),
              retained_c = net income of TRS, accrued_interest_c = balance(1230),
              identity: settled_c(USD) == contributed_c - advances_c + retained_c - accrued_interest_c(not yet paid)
              + withdrawals already netted in 3000  (I7 below); per-account advances table from transfers.
```

### 4.6 `integrity()` - the checks

Quick checks run inside every command (on the touched entries/accounts); the full set runs at startup, hourly, after
every lifecycle batch and on demand (`GET /api/integrity?full=1`). Every failure becomes an attention row and is shown
in the UI strip. The report is `{ok, checked_at, scope, problems: [{check, scope, expected, actual, detail}]}`.

| Id | Check | SQL/logic |
|---|---|---|
| Q1/I1 | Every entry balances in base: `SUM(base_c) = 0` per entry; single-currency entries also `SUM(amount_c)=0`. | `SELECT entry_id, SUM(base_c) FROM journal_lines GROUP BY entry_id HAVING SUM(base_c) <> 0` |
| Q2/I2 | Pending cash equals open settlements: per (account, ccy) `balance(1200)+balance(1250) == SUM(amount_c of PENDING settlements with amount>0)` and `balance(2100)+balance(2350) == -SUM(... amount<0)`. | join settlements vs lines |
| Q3/I3 | Positions: for every (account, instrument) `v_positions.qty == SUM(signed fills qty)` where sign = +qty for BUY, -qty for SELL (all origins). | fills vs lots |
| Q4/I4 | Lots: for every lot `qty_open*unit_cost*mult - SUM(cost_relieved) == cost_c` (to the cent, HALF_EVEN) and `qty_open - SUM(relief qty) == qty_remaining`. | lots vs lot_relief |
| I5 | NAV ties: `balance_sheet(scope).assets_c - liabilities_c == nav(scope).nav_c` for every account and ALL. | read models |
| I6 | No duplicate postings: `idem_key` unique (schema) **and** no two ACCRUAL/VM/COLLATERAL/COUPON entries share (ref_type, ref_id, business_date, kind). | `GROUP BY ... HAVING COUNT(*)>1` |
| I7 | Treasury identity: `balance(1010,TRS,USD) == balance(3000) - balance(3100,TRS) + net_income(TRS) - balance(1230,TRS) - balance(1410,TRS)` (Treasury holds no positions in this build). | ledger |
| I8 | Every short equity position (`qty<0`, kind in EQUITY/ETF/ADR/PREFERRED) has `SUM(open borrows qty - qty_returned) >= |qty|`. | borrows vs v_positions |
| I9 | Futures margin: `balance(1300, instrument) == IM_per_contract * |qty|` for every open futures position; zero for closed ones. | ledger vs specs |
| I10 | Collateral: for every OPEN borrow `balance(1400, memo=borrow_id) == borrows.collateral_c`. | ledger vs borrows |
| I11 | Sweep linkage: every SWEEP_IN transfer has `linked_type/linked_id` pointing at an existing fill/settlement/borrow/collateral entry; every SWEEP_* amount equals the shortfall recorded in `details_json` of that entry. | transfers |
| I12 | Snapshot continuity: for consecutive snapshots `nav_t - nav_{t-1} - flows_day == pnl_day` (recorded at snapshot time; a mismatch means the ledger changed retroactively). | nav_snapshots |
| I13 | Cash floors: `balance(1010) >= 0` for every (account, ccy) except PRIME accounts with `pb_financing=1`, where `balance(2700) <= limit`. | ledger |
| I14 | Request/entry linkage: every fill/transfer/settlement `entry_id` exists; every entry has a `requests` row; no `requests` row DONE with `entry_ids` that do not exist. | joins |

Quick set = Q1-Q4 on the touched entries/instruments + I13 on touched accounts. The full set runs in a reader
connection (no lock); its result is written to `meta.integrity_json` by a tiny writer transaction.

---

## 5. Instrument master and symbol mapping (`instruments.py`)

### 5.1 Identifiers (Decision)

| Kind | `instrument_id` | `yahoo_symbol` | `cboe_symbol` | Notes |
|---|---|---|---|---|
| EQUITY/ETF/ADR/PREFERRED (US) | Yahoo ticker as spelled (`AAPL`, `BRK-B`, `BAC-PL`) | same | ticker with `-` -> `.` (`BRK.B`) | seeded from `finsim/data/universe.json` equities (EQUITY, ETF, ADR, REIT, PREFERRED; CRYPTO skipped) |
| Foreign listing | Yahoo symbol with suffix (`VOD.L`, `SAP.DE`, `7203.T`, `0700.HK`) | same | none | `price_scale` 0.01 for `.L` pence; session from `exchange_sessions`; account kind FOREIGN_FX |
| OPTION | Cboe/OCC form `AAPL261120C00330000` (root, yymmdd, C/P, strike x 1000 as 8 digits) | none (not quoted by Yahoo without a crumb) | the `option` field as Cboe prints it | materialised on demand from a chain fetch; `underlying_id`, `expiry`, `strike`, `right`, `style` AMERICAN (equity/ETF) or EUROPEAN (index), `exercise` PHYSICAL or CASH, multiplier 100 |
| FUTURE | `<code><M><yy>` (`ESZ26`, `CLX26`) | `<id>.<suffix>` with suffix CME / CBT / NYM / CMX from `FUTURES_SPECS` (`ESZ26.CME`, `CLX26.NYM`) | none | **Verify** at WP1 smoke test that `.CME` per-contract symbols resolve (probe verified `CLX26.NYM`); fallback: the continuous `<code>=F` is used only while the instrument is the front month and the record carries `method: front_month_continuous` |
| BOND (Treasury benchmark) | `UST-<coupon>-<yyyymmdd>` (`UST-4.250-20360815`); bills `UST-B-<yyyymmdd>` | none | none | model benchmarks (section 5.5), priced `computed` |
| BOND (curated corporate) | universe.json bond id (`JPM-29`) | none | none | priced `computed` from curve + FRED OAS |
| FX_PAIR | market convention (`EURUSD`, `USDJPY`, `GBPUSD`) | `EURUSD=X`, `JPY=X` (quoted as USD per unit after inversion where `invert=True`) | none | 25 currencies from the old `FX_SYMBOLS` table; `price_scale` 1 |
| INDEX | `SPX`, `NDX`, `RUT`, `VIX`, `DXY` | `^GSPC`, `^NDX`, `^RUT`, `^VIX`, `DX-Y.NYB` | `_SPX`, `_NDX`, `_RUT`, `_VIX` | reference only, not tradeable; underlying of index options |
| YIELD | `^IRX`, `^FVX`, `^TNX`, `^TYX` | same | none | reference only (intraday curve shifts) |

### 5.2 API

```python
@dataclass(frozen=True)
class Instrument:
    instrument_id: str; kind: str; symbol: str; name: str; currency: str; multiplier: Decimal; tick: Decimal
    lot: int; price_scale: Decimal; session_group: str; settle_market: str; account_kinds: tuple[str, ...]
    yahoo_symbol: str | None; cboe_symbol: str | None; underlying_id: str | None
    expiry: str | None; strike: Decimal | None; right: str | None; style: str | None; exercise: str | None
    spec: dict; status: str
    # derived helpers
    def is_option(self) -> bool; def is_future(self) -> bool; def is_bond(self) -> bool; def is_fx(self) -> bool
    def contract_value_c(self, px: Decimal, qty: int) -> int        # qty * px * multiplier * price_scale (bonds: /100)
    def fee_c(self, qty: int, account: Account) -> int             # FEES / PB schedule, section 8.6

class InstrumentMaster:
    def __init__(self, db: Database, log)
    def load(self) -> None                                          # all ACTIVE/LIMITED rows into memory (dict); ~1.5k rows
    def get(self, instrument_id: str) -> Instrument | None
    def search(self, q: str, kinds: tuple[str, ...] = (), limit: int = 20) -> list[Instrument]   # prefix on symbol/name
    def by_underlying(self, underlying_id: str, expiry: str | None = None) -> list[Instrument]
    def seed(self, tx: Tx) -> int                                   # first run: universe.json equities, FX pairs, indices, yields, Treasury benchmarks, curated corporates, futures front 4 months
    def ensure_option(self, tx: Tx, occ: str, underlying: Instrument, chain_row: dict) -> Instrument   # materialise from a Cboe row
    def ensure_futures_listings(self, tx: Tx, today: date) -> int   # list the next N contract months per code (spec rule), mark expired
    def ensure_foreign_listing(self, tx: Tx, yahoo_symbol: str, meta: dict) -> Instrument   # from Yahoo meta (currency, exchangeName, tz)
    def tradeable_in(self, inst: Instrument, account: Account) -> bool

# pure helpers
def parse_occ(s: str) -> tuple[str, date, str, Decimal] | None   # root, expiry, right, strike
def futures_symbol(code: str, year: int, month: int) -> str        # "ESZ26"
def futures_yahoo(code: str, year: int, month: int) -> str         # "ESZ26.CME"
def last_trade_date(code: str, year: int, month: int, cal: BusinessCalendar) -> date   # spec expiry_rule
def fx_yahoo(pair: str) -> tuple[str, bool]                        # ("JPY=X", invert=True)
```

### 5.3 Tradeability by account kind (`account_kinds`)

| Account kind | Instruments |
|---|---|
| US_EXCHANGE | US EQUITY/ETF/ADR/PREFERRED, OPTION on them and on SPX/NDX/RUT, FUTURE |
| DEBT | BOND (UST benchmarks, curated corporates) |
| CREDIT | cash loans, repo (BOND collateral), securities lending view (no direct instrument trading) |
| FOREIGN_FX | foreign listings, FX_PAIR spot/forward |
| PRIME | everything the PB schedule lists (US_EXCHANGE set + FX + OTC slice) at PB fees |
| TREASURY | nothing (cash only; FX convert allowed) |

### 5.4 Futures spec table (`config.FUTURES_SPECS`) - static, disclosed, **Verify** IM against CME before deploy

| code | group | yahoo suffix | multiplier | tick | IM / MM (USD/contract) | listed months | last-trade rule |
|---|---|---|---|---|---|---|---|
| ES | CME_EQUITY_INDEX | CME | 50 | 0.25 | 17,500 / 15,900 | H M U Z | 3rd Friday 09:30 ET |
| NQ | CME_EQUITY_INDEX | CME | 20 | 0.25 | 26,000 / 23,600 | H M U Z | 3rd Friday |
| YM | CME_EQUITY_INDEX | CBT | 5 | 1.00 | 11,000 / 10,000 | H M U Z | 3rd Friday |
| RTY | CME_EQUITY_INDEX | CME | 50 | 0.10 | 9,000 / 8,200 | H M U Z | 3rd Friday |
| CL | NYMEX_ENERGY | NYM | 1,000 | 0.01 | 6,500 / 5,900 | monthly | 3 business days before the 25th of the month preceding delivery |
| BZ | NYMEX_ENERGY | NYM | 1,000 | 0.01 | 6,500 / 5,900 | monthly | last business day of the 2nd month preceding delivery |
| NG | NYMEX_ENERGY | NYM | 10,000 | 0.001 | 4,500 / 4,100 | monthly | 3 business days before the 1st of delivery month |
| GC | COMEX_METALS | CMX | 100 | 0.10 | 12,000 / 10,900 | G J M Q V Z | 3rd last business day of delivery month |
| SI | COMEX_METALS | CMX | 5,000 | 0.005 | 11,000 / 10,000 | H K N U Z | 3rd last business day |
| HG | COMEX_METALS | CMX | 25,000 | 0.0005 | 6,000 / 5,500 | H K N U Z | 3rd last business day |
| ZB | CME_RATES | CBT | 1,000 | 0.03125 | 5,000 / 4,500 | H M U Z | 7th business day before the last business day of delivery month |
| ZN | CME_RATES | CBT | 1,000 | 0.015625 | 2,600 / 2,400 | H M U Z | same |
| ZF | CME_RATES | CBT | 1,000 | 0.0078125 | 1,500 / 1,400 | H M U Z | last business day of delivery month |
| ZT | CME_RATES | CBT | 2,000 | 0.0078125 | 1,100 / 1,000 | H M U Z | last business day |
| 6E | CME_FX | CME | 125,000 | 0.00005 | 2,800 / 2,500 | H M U Z | 2nd business day before the 3rd Wednesday |
| 6J | CME_FX | CME | 12,500,000 | 0.0000005 | 3,000 / 2,700 | H M U Z | same |
| 6B | CME_FX | CME | 62,500 | 0.0001 | 2,400 / 2,200 | H M U Z | same |
| ZC | CBOT_GRAINS | CBT | 50 (price in cents/bu x 5,000 bu) | 0.25 | 1,500 / 1,400 | H K N U Z | business day before the 15th of contract month |
| ZW | CBOT_GRAINS | CBT | 50 | 0.25 | 2,200 / 2,000 | H K N U Z | same |
| ZS | CBOT_GRAINS | CBT | 50 | 0.25 | 2,800 / 2,500 | F H K N Q U X | same |

Yahoo quotes grains in cents per bushel (ZC=F 525.25), so the multiplier is 50 USD per point and the price is left as
quoted. Rates futures are quoted in points of 32nds already converted to decimals by Yahoo (e.g. 112.515625). The
spec also carries `settle_time_et` (16:00 for equity index and rates, 14:30 energy/metals, 14:20 grains, 16:00 FX).

### 5.5 Treasury benchmarks and curated corporates

At first run and on the first business day of every month `HOUSEKEEPING` lists a benchmark per tenor
(1M, 3M, 6M, 1Y bills; 2Y, 3Y, 5Y, 7Y, 10Y, 20Y, 30Y notes/bonds): issue date = listing date, maturity = issue + tenor,
coupon = par yield at listing rounded to 1/8 % (bills: zero coupon), semi-annual, ACT/ACT, `settle_market` US_TREASURY.
Name: "US Treasury 4.250% 15-Aug-2036 (model benchmark)". The UI tags them *model benchmark, not a specific CUSIP*.
Curated corporates come from `universe.json["bonds"]` (issuer, coupon, years, rating, spread) with maturity =
2026-09-18 + years, status LIMITED, label "corporate, computed price".

---

## 6. Quote layer: providers, cache record, freshness, worker, computed marks

### 6.1 Record (`quotes/__init__.py`)

```python
QUALITY = ("live", "delayed", "close", "computed", "stale", "unavailable")

@dataclass
class Bar1m: t: int; o: Decimal; h: Decimal; l: Decimal; c: Decimal; v: int

@dataclass
class QuoteRecord:
    instrument_id: str
    provider: str                 # yahoo | cboe | treasury | fred | frankfurter | computed
    source: str                   # human label: "Yahoo Finance (up to 15 min delayed)", "Cboe delayed quotes (15 min)", "computed: par curve + ^TNX shift"
    method: str                   # direct | chain | front_month_continuous | curve | cip | model | daily_close
    last: Decimal | None; bid: Decimal | None; ask: Decimal | None; bid_size: int | None; ask_size: int | None
    mid: Decimal | None
    prev_close: Decimal | None; day_high: Decimal | None; day_low: Decimal | None; day_volume: int | None
    quote_time: int | None        # provider timestamp (regularMarketTime / last_trade_time / curve date 16:00 ET)
    fetched_at: int               # our clock when the fetch returned
    delay_s: int                  # stated delay: 900 for yahoo/cboe, 0 for computed inputs' own
    delay_label: str              # "up to 15 min delayed", "daily close", "computed"
    currency: str
    session_group: str; exchange_tz: str | None; provider_session: tuple[int, int] | None   # Yahoo currentTradingPeriod.regular
    bar: Bar1m | None             # the latest COMPLETED 1-minute bar (t <= now-60s), for participation caps
    bars_tail: list[Bar1m]        # last 5 bars (sparkline / cap); never more
    error: str | None             # provider error text when the last fetch failed (the previous values are kept)
    inputs: dict | None           # for computed marks: every input with its own quote_time/source
    quality: str                  # recomputed by freshness() at read time, stored for persistence

def freshness(rec: QuoteRecord | None, session: SessionState, now: int) -> str
def is_executable(rec: QuoteRecord | None, session: SessionState, now: int) -> tuple[bool, str]   # (ok, reason code)
```

### 6.2 Freshness rules (`freshness()`), exactly

```
if rec is None or rec.last is None (and rec.mid is None):            -> "unavailable"
if rec.method in ("curve", "cip", "model"):
    worst input quality (recursive) in {"stale","unavailable"}       -> "stale"
    else                                                             -> "computed"
if not session.open:                                                 -> "close"      (valuation ok, execution blocked by the session)
# session open:
if rec.quote_time is None                                            -> "stale"
if now - rec.quote_time > rec.delay_s + STALE_GRACE_S (300)          -> "stale"
if now - rec.fetched_at > FETCH_GRACE_S (300)                        -> "stale"
if rec.error is not None and now - rec.fetched_at > 60               -> "stale"      (a failing provider ages the record quickly)
if rec.delay_s == 0                                                  -> "live"       (never produced in this build, A10)
else                                                                 -> "delayed"
```
`is_executable` = `quality in ("live", "delayed") and session.open` and, for a quote whose `provider_session` says the
provider thinks the regular session is over for today (`now > provider_session.end`), `False` with reason
`PROVIDER_SESSION_ENDED` (guards an early close the local table missed). Reasons: `NO_QUOTE`, `STALE`, `SESSION_CLOSED`,
`COMPUTED_NOT_EXECUTABLE`, `PROVIDER_SESSION_ENDED`.

Valuation (marks) never blocks: a `stale`/`close` record is still the "last valid mark" and is shown with its timestamp;
`unavailable` shows a dash and the position's MV is reported as unavailable (NAV flagged `degraded`).

### 6.3 Providers (`quotes/providers.py`)

```python
class ProviderError(Exception): status: int | None; retryable: bool; cooldown_s: int
def http_get(url: str, headers: dict, timeout: float = 20.0, max_bytes: int = 16 << 20) -> bytes
    # copied from finsim2.data._http_get without the reqctx dependency: gzip, size cap, FetchError semantics,
    # messages never contain the URL
class RateLimiter: def __init__(self, rps: float); def wait(self) -> None    # token bucket, thread-safe

class YahooChart:                      # UA BROWSER_UA (Yahoo only); hosts query1 then query2; 3 attempts, backoff 1,2,4 s on 429/5xx/timeouts
    def intraday(self, symbol: str) -> YahooIntraday    # /v8/finance/chart/{sym}?range=1d&interval=1m
        # YahooIntraday(meta: dict, bars: list[Bar1m] last 5 only, regular_period: (start,end), tz: str, currency, exchange)
    def daily(self, symbol: str, days: int = 10) -> list[DailyBar]   # ?range=1mo&interval=1d (lifecycle catch-up closes)
class CboeChain:                       # UA: honest "LoganTerminal/1.0 (loganshaffer87@gmail.com)" (not a browser UA; .gov rule extended by owner policy); 1 req/s; timeout 60 s
    def chain(self, cboe_symbol: str) -> ChainSnapshot
        # ChainSnapshot(underlying: {last, bid, ask, bid_size, ask_size, last_trade_time (ET naive -> epoch), prev_close, iv30},
        #               contracts: list[ChainRow(occ, expiry, right, strike, bid, ask, bid_size, ask_size, last, last_time, iv, delta, gamma, theta, vega, oi, volume)],
        #               fetched_at)
        # Parsing keeps only expiries <= 400 days and strikes within +-35% of spot; everything else is dropped before
        # the list is built (json.loads of the whole body is unavoidable; the dict is released immediately).
class TreasuryCurve:                   # UA GOV_UA; timeout 45 s; once a day
    def latest(self) -> ParCurve       # XML feed ...pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value_month=YYYYMM
        # entries d:NEW_DATE, d:BC_1MONTH ... d:BC_30YEAR -> ParCurve(as_of: date, points: {tenor_years: Decimal pct})
class FredSeries:                      # UA GOV_UA; fredgraph.csv?id=...
    def latest(self, series_id: str, lookback_days: int = 45) -> list[tuple[date, Decimal]]   # blank values skipped
class Frankfurter:                     # fallback FX only (ECB reference, daily)
    def latest(self) -> dict[str, Decimal]
class FinraShortVolume:                # cdn.finra.org daily file; hard-to-borrow heuristic input
    def daily(self, d: date) -> dict[str, tuple[int, int]]          # symbol -> (short_volume, total_volume)
```

Provider health: each call updates `provider_health` (via a short writer transaction, not a command): consecutive
errors, last error text (status only, never the URL/key), `down_until_ts` = now + 60 s on 429, + 120 s after 5
consecutive failures (circuit breaker). `/api/health.providers` and the UI attention strip show it.

### 6.4 Cache and worker (`quotes/cache.py`, `quotes/worker.py`)

```python
class QuoteCache:
    def __init__(self, db: Database, sessions: SessionTable, clock: Clock)
    def get(self, instrument_id: str) -> QuoteRecord | None             # quality recomputed at read
    def get_many(self, ids: Iterable[str]) -> dict[str, QuoteRecord]
    def put(self, rec: QuoteRecord) -> None
    def subscribe(self, ids: Iterable[str], ttl_s: int = 90, priority: str = "WATCH") -> None   # UI pages renew every poll
    def pin(self, ids: Iterable[str], priority: str) -> None            # HELD/ORDER pins (renewed by on_commit listeners)
    def unpin(self, ids) -> None
    def due(self, now: int) -> list[tuple[str, str]]                    # [(instrument_id, priority)] whose next_due <= now
    def load_persisted(self) -> int; def persist(self) -> int           # quote_cache table (every 60 s and at shutdown)

class QuoteWorker(threading.Thread):
    def __init__(self, cache, instruments, providers, sessions, bus, matcher, clock, log, fetch_threads: int = 2)
    def run(self) -> None        # loop: every 1 s take due ids, group by provider/symbol, fetch on the pool, cache.put, then matcher.on_quotes(ids)
    def stop(self, timeout: float = 3.0) -> None
    def stats(self) -> dict      # queue depth, fetches/min, last error, per-provider health (for /api/health)
```

Scheduling (`QUOTE_INTERVALS`): an instrument's interval is the smallest of its pins: ORDER 15 s (a working order
exists), HELD 30 s, WATCH 60 s (UI subscription), and CLOSED 300 s whenever its session is closed (one fetch every five
minutes keeps the "last close" and `prev_close` honest, and catches late prints). Chains: CHAIN 90 s while an option
chain is on screen or an option position/order exists for that underlying (one request per underlying; the SPX chain is
only fetched when the user opens it). Curve: once a day after 18:00 ET plus at startup if missing for today; FRED
series every 6 h; Frankfurter daily; FINRA daily after 18:00 ET.

Budget: at most `YAHOO_RPS` = 2 requests/s and `CBOE_RPS` = 1/s; with 60 held/ordered names and 200 watched, steady
state is about 1 request/s. Fetch threads: 2 (network-bound), so a slow provider never blocks the scheduler loop.
A symbol that returns 404/400 is marked `unavailable` with the error and retried hourly. Memory: a `QuoteRecord` is
about 1 KB; 2,000 records = 2 MB; chains are condensed to ChainRow tuples (about 150 B each; SPY ~4k rows after
filtering).

**Matcher hook**: after each batch the worker calls `matcher.on_quotes(updated_ids)` on its own thread. The matcher
reads open orders for those instruments (reader connection), evaluates each with the pure rules in section 8, and for
every FILL decision calls `bus.execute(FILL_ORDER …)`. The writer lock is held only inside `bus.execute`, never during
network I/O.

### 6.5 Computed marks (`marks.py`)

```python
@dataclass
class MarkView:
    instrument_id: str; mark: Decimal | None; quality: str; source: str; method: str
    quote_time: int | None; fetched_at: int | None; delay_label: str; inputs: dict | None; executable: bool; note: str

class MarkProvider:
    def __init__(self, cache: QuoteCache, instruments, curves: CurveStore, rates: RateStore, sessions, clock)
    def mark_for(self, inst: Instrument, at: int | None = None) -> MarkView
    def bond_price(self, inst, curve: ParCurve, shifts: dict, oas_bps: Decimal | None, asof: date) -> tuple[Decimal, dict]
    def fx_forward(self, pair: str, value_date: date, at: int | None = None) -> tuple[Decimal, dict]
    def option_model(self, inst, under_rec: QuoteRecord, chain: ChainSnapshot | None) -> tuple[Decimal, dict]
```

Mark selection per kind (first rule that applies):

| Kind | Mark | quality |
|---|---|---|
| Equity/ETF/ADR/foreign/FX pair/index/future | Yahoo `last` (x price_scale) | freshness() of the record |
| Option | Cboe mid when `bid>0 and ask>=bid` (bid for a long position's liquidation value is also shown); else `option_model` | delayed / computed |
| Treasury benchmark | `bond_price`: y = par yield interpolated linearly in maturity on the latest Treasury.gov curve (tenors 1M..30Y), plus the intraday shift `(^IRX/^FVX/^TNX/^TYX now - their prev_close)` interpolated to the bond's maturity; clean price = PV of remaining semi-annual coupons + principal at y/2 compounding with the street fractional first period; accrued ACT/ACT. Dirty = clean + accrued. | computed (inputs: curve as_of, each yield's quote_time) |
| Corporate (curated) | same with y += OAS of the rating bucket (FRED: BAMLC0A1CAAA, BAMLC0A2CAA, BAMLC0A3CA, BAMLC0A4CBBB, BAMLH0A1HYBB, BAMLH0A2HYB, BAMLH0A3HYC), as-of date shown | computed |
| FX forward | CIP: `F = S * (1 + r_quote*d/B_quote) / (1 + r_base*d/B_base)` with S = Yahoo mid, `d` = calendar days to value date, policy index per currency (`RATES.POLICY_INDEX`, FRED), day basis ACT/360 except GBP ACT/365; PV of an open forward = `(F_now - K) * amount * DF_quote`, DF from the same rate | computed (inputs: S, both rates with as-of dates) |
| Cash loan / PB balance | principal + accrued (ledger) | n/a (not a market mark) |

`option_model`: Black-Scholes-Merton (European) / CRR 60-step (American) from `finsim.engines.options_pricing` with
S = underlying last, K, T = days/365, r = ^IRX/100, q = trailing dividend yield from universe.json, sigma = IV
interpolated across the two nearest quoted strikes of the same expiry (Cboe `iv`), else the chain's `iv30`, else FRED
`VIXCLS`/100 for index/ETF underlyings; labelled `computed`, never executable, shown in italics with the inputs.

### 6.6 Curve and rate stores

```python
class CurveStore:   # curve_points table, series 'UST_PAR'
    def latest(self) -> ParCurve | None; def put(self, curve: ParCurve) -> None; def as_of(self) -> date | None
    def intraday_shifts(self, cache: QuoteCache) -> dict[Decimal, Decimal]   # {0.25: d_irx, 5: d_fvx, 10: d_tnx, 30: d_tyx} in decimal
class RateStore:    # curve_points table, series = FRED id, tenor 'VALUE'
    def latest(self, series_id: str) -> tuple[date, Decimal] | None     # (as_of, pct)
    def policy_rate(self, ccy: str) -> tuple[date, Decimal] | None
    def sofr(self) -> tuple[date, Decimal]
```
A missing rate is `None` and the dependent mark becomes `unavailable` with the note "SOFR unavailable since <date>";
it is never defaulted to a number.

---

## 7. Sessions (`sessions.py`)

```python
@dataclass(frozen=True)
class SessionState:
    group: str; open: bool; phase: str            # OPEN | CLOSED | BREAK | HOLIDAY | EARLY_CLOSED | WEEKEND
    opened_at: int | None; closes_at: int | None; next_open: int | None; trading_date: str | None   # the session's business date (NY)
    note: str                                     # "early close 13:00 ET (Day after Thanksgiving)"

class SessionTable:
    def __init__(self, holidays: HolidayCalendar, exchange_sessions: ExchangeSessionStore, clock: Clock)
    def state(self, group: str, at: int | None = None) -> SessionState
    def states(self, at: int | None = None) -> dict[str, SessionState]
    def settle_date(self, settle_market: str, trade_date: date) -> date   # finsim.calendar.settlement_date with the file-based holiday set
    def close_time(self, group: str, d: date) -> int | None
    def is_trading_day(self, group: str, d: date) -> bool

class HolidayCalendar:         # data/nyse-holidays.json (repo root, read once) + finsim.calendar.us_equity_holidays fallback for years not in the file
    def nyse_closed(self, d: date) -> bool; def nyse_early_close(self, d: date) -> time | None
    def business_calendar(self) -> BusinessCalendar   # a finsim.calendar.BusinessCalendar whose holiday_fn merges the file
```

`SESSION_GROUPS` (times New York; Mon=0):

| group | weekly schedule | holidays | early closes |
|---|---|---|---|
| US_EQUITY | Mon-Fri 09:30-16:00 | NYSE file | 13:00 on file dates |
| US_EQUITY_OPTION | Mon-Fri 09:30-16:00 | NYSE | 13:00 |
| US_INDEX_OPTION | Mon-Fri 09:30-16:15 | NYSE | 13:15 |
| US_BOND | Mon-Fri 08:00-17:00 | NYSE (SIFMA approximation, disclosed) | 14:00 on NYSE early-close dates |
| CME_EQUITY_INDEX | Sun 18:00 -> Fri 17:00, daily break 17:00-18:00 Mon-Thu | closed Good Friday, Christmas, New Year's Day (full); other NYSE holidays: early close 13:00 then reopen 18:00 | per holiday rule |
| CME_RATES | same as CME_EQUITY_INDEX | same | same |
| CME_FX | Sun 18:00 -> Fri 17:00, break 17:00-18:00 | same | same |
| NYMEX_ENERGY | Sun 18:00 -> Fri 17:00, break 17:00-18:00 | same | same |
| COMEX_METALS | same as NYMEX_ENERGY | same | same |
| CBOT_GRAINS | Sun-Thu 20:00-08:45 (next day) and Mon-Fri 09:30-14:20 | same | 13:05 early on NYSE early-close dates (approximation) |
| FX_SPOT | Sun 17:00 -> Fri 17:00 continuous | none (value dates skip weekends and US holidays; other centres' holidays not modelled, disclosed) | none |
| FOREIGN:<exchange> | from `exchange_sessions` (Yahoo `currentTradingPeriod.regular` + `exchangeTimezoneName`), refreshed when the day changes | implied by the provider (no period = closed) | implied |

The CME holiday rules are an approximation of the CME Globex holiday calendar and are labelled so in the UI
("CME schedule approximated from the NYSE calendar - verify"). A per-date override table (`settings` key
`session_overrides`) lets the owner add a closure without a code change.

Order expiry times: DAY orders expire at `closes_at` of the session in which they were eligible (section 8.3).
Lifecycle settlement dates use `settle_date()` with the merged holiday set (T+1 US equities/options/Treasuries, T+2
FX spot and most foreign equities, 0 futures) exactly as `finsim.calendar.SettlementConfig` defines them.

---

## 8. Orders: validation, preview, state machine, matching, fills, funding (`orders.py`, `execution.py`, `lending.py`)

### 8.1 Submit-time validation (`orders.validate`) - rejection codes

| Code | HTTP | Rule |
|---|---|---|
| ACCOUNT_CLOSED | 400 | account missing or not OPEN |
| INSTRUMENT_NOT_TRADEABLE_HERE | 400 | `instruments.account_kinds` excludes the account kind; Treasury never trades |
| INSTRUMENT_INACTIVE | 400 | status EXPIRED/DELISTED/UNSUPPORTED; LIMITED instruments are allowed with a warning |
| INSTRUMENT_EXPIRED | 400 | option/future past its last trade date (on the expiry day itself trading is allowed until the session close) |
| QTY_INVALID / QTY_LOT | 400 | qty <= 0 or not a multiple of `lot` (bonds: 1,000 face) |
| ORDER_TYPE / TIF / PRICE_REQUIRED | 400 | unknown type; LIMIT/STOP_LIMIT need limit_px; STOP/STOP_LIMIT need stop_px |
| PRICE_TICK | 400 | limit/stop not a multiple of `tick` |
| STOP_SIDE | 400 | buy stop below last / sell stop above last (would trigger at once); the preview says "use a limit" |
| NO_BORROW | 409 | SELL would open/increase a short in an equity and `borrow` is not requested or the locate says not available |
| QUOTE_STALE | 409 | MARKET order while the session is open and `is_executable()` is false |
| INSUFFICIENT_FUNDS | 409 | worst-case cash need > own available + Treasury available + PB headroom (section 8.8) |
| OPTION_MARGIN | 409 | sell-to-open option margin requirement exceeds buying power |
| PRICE_MOVED | - | warning only: the quote used by the preview (`snapshot_id`) differs from the current one by > 1 % |

Risk and compliance never reject (they return `warnings`).

### 8.2 Preview (`orders.preview(services, req) -> Preview`), the shape the UI and the submit path share

```json
{
  "ok": true, "blockers": [], "warnings": [{"code": "RISK_CONCENTRATION", "message": "AAPL would be 23 % of NAV"}],
  "account_id": "USX", "instrument": {"id": "AAPL", "name": "Apple Inc.", "kind": "EQUITY", "currency": "USD", "multiplier": "1"},
  "side": "BUY", "qty": 5000, "order_type": "MARKET", "tif": "DAY",
  "quote": {"last": "339.50", "bid": null, "ask": null, "quality": "delayed", "source": "Yahoo Finance (up to 15 min delayed)",
            "quote_time": 1792526401, "age_s": 245, "delay_label": "up to 15 min delayed", "method": "direct"},
  "execution": {"price_basis": "last", "expected_price": "339.50", "fill_now": true, "fill_qty_now": 5000,
                "cap_note": "latest 1-minute bar volume 61,900 shares; 10 % participation caps a pass at 6,190 shares",
                "session": {"group": "US_EQUITY", "open": true, "closes_at": 1792526400}, "rests_until": null,
                "assumption": "fills at the last observed trade price; no bid/ask is available from the free feed"},
  "economics": {"gross_c": 169750000, "fee_c": 2500, "net_c": 169752500, "currency": "USD", "settle_date": "2026-10-21",
                "settle_market": "US_EQUITY"},
  "funding": {"available_c": 100000000, "need_c": 169752500, "shortfall_c": 69752500, "sweep_in_c": 69752500,
              "sweep_source": "TRS", "treasury_available_c": 900000000, "pb_c": 0, "after_available_c": 0,
              "formula": "own settled + pending - restricted - reserved - option margin"},
  "borrow": null,
  "margin": null,
  "hedges": [ {"id": "PUT_7PCT", "label": "Protective put 315 Jan-16-2027", "legs": [...], "cost_c": 487500, "protection": "floor 315.00 (-7.2 %)", "quality": "delayed"} ],
  "compliance": [{"rule": "Reg SHO 203(b) locate", "status": "n/a", "message": "not a short sale"}],
  "risk": [...],
  "snapshot_id": "sha1-of-quote-time-and-price"
}
```
For a short sale `borrow` is `{needed: true, qty, fee_rate, rebate_rate, tier, lender, collateral_c, available, valid_until}`;
for options `margin` is `{requirement_c, formula}`; for futures `margin` is `{initial_c, maintenance_c, per_contract_c}`.
The preview is pure (no writes) and is recomputed by the submit handler from the same quote snapshot; a changed quote
produces the `PRICE_MOVED` warning in the submit response.

### 8.3 Order state machine

```
            submit (validated)                        matcher: full fill
  [preview] ---------------------> WORKING ------------------------------------> FILLED
                 |                   |  \ matcher: partial fill                     ^
                 | validation fail   |   `-------------> PARTIALLY_FILLED ----------'  (remaining fills)
                 v                   |                        |        |
             REJECTED                | user cancel            | cancel | DAY close / GTC 90 d / instrument expiry
                                     v                        v        v
                                 CANCELLED                CANCELLED  EXPIRED
```
`triggered` (0/1) is a flag on STOP/STOP_LIMIT orders, not a status. Terminal statuses: FILLED, CANCELLED, REJECTED,
EXPIRED. Every transition writes an `order_events` row with the quote used (`quote_json`) and a human note.
`expires_ts`: DAY = `closes_at` of the session in which the order is first eligible (now if open, else the next
session); GTC = created_ts + 90 days (also expires when the instrument expires). An order whose remaining quantity
cannot be funded at fill time (section 8.8) ends CANCELLED with reason `FUNDING` (REJECTED if nothing filled).

Who moves what: the API submits/cancels; the matcher (worker thread) fills; the lifecycle expires; nothing else.

### 8.4 Matching rules (`orders.evaluate`, pure)

```python
@dataclass
class Decision: action: str; price: Decimal | None; qty: int; reason: str; bar_ts: int | None   # NONE | FILL | TRIGGER
def evaluate(order: Order, rec: QuoteRecord | None, session: SessionState, now: int) -> Decision
```

1. `session.open` is False -> NONE `AWAITING_SESSION` (the order rests; the UI shows "opens Mon 09:30").
2. `is_executable(rec)` is False -> NONE with the reason (`STALE`, `NO_QUOTE`, ...). Nothing ever fills on a stale or
   computed quote.
3. Executable price `px_exec`: options -> `ask` (BUY) / `bid` (SELL) from the Cboe chain row, requiring `bid > 0` and
   `ask >= bid`; everything else -> `last`. FX: `mid ± 0.2 pip` (BUY adds, SELL subtracts).
4. MARKET -> FILL at `px_exec`.
5. LIMIT -> marketable test `px_exec <= limit` (BUY) / `>= limit` (SELL). On the first evaluation after submit
   (`first_eval_done = 0`) a marketable limit fills at `px_exec` ("at or better"); afterwards a crossed limit fills at
   the limit price. Not marketable -> NONE `NOT_MARKETABLE`.
6. STOP: not triggered and `last >= stop` (BUY) / `last <= stop` (SELL) -> TRIGGER then treated as MARKET in the same
   pass (fills at `px_exec`, i.e. the observed price that triggered it; a gap through the stop fills at the gap price,
   disclosed). STOP_LIMIT: TRIGGER then LIMIT rules.
7. Quantity: `qty = min(remaining, cap)` from section 8.5; `qty == 0` -> NONE `NO_NEW_BAR`.
8. The decision carries `bar_ts` = `rec.bar.t` (None when no bar) so the fill's idem_key
   `match:<order_id>:<quote_time>:<bar_ts>` makes a second evaluation of the same observation a no-op.

The matcher evaluates every open order of an instrument each time the worker refreshes it, and once more at submit
(so a marketable order fills immediately against the cached quote without waiting for the next poll).

### 8.5 Partial fills by observed volume

```
bar = rec.bar (the latest completed 1-minute bar: t <= now - 60 and volume is not None)
if order.last_bar_ts is not None and bar is not None and bar.t <= order.last_bar_ts: cap = 0      # no new observation yet
elif bar is None or bar.v is None or bar.v == 0:  cap = remaining     # no volume information: fills in full (disclosed in the preview)
else:  cap = max(lot, floor(PARTICIPATION_RATE * bar.v / lot) * lot)
options:  cap = max(1, ask_size if BUY else bid_size) contracts; size None/0 -> remaining (disclosed)
futures:  as equities (minute bar volume from Yahoo)
FX:       remaining (no volume concept; disclosed)
```
After a partial fill the order stores `last_bar_ts = bar.t`; the next slice needs a newer bar. The preview's `cap_note`
states the bar volume and the resulting cap. The participation rate is a setting (`settings.participation_rate`, default
0.10) and is printed in every preview.

### 8.6 Fees (`Instrument.fee_c`)

```
EQUITY/ETF/ADR/PREFERRED: max(1.00, 0.005 * qty)           (PB accounts: schedule, e.g. GS max(5.00, 0.010 * qty))
OPTION:  0.65 * contracts                                   (PB: 1.00)
FUTURE:  2.25 * contracts (per side), charged in cash the same day
BOND:    max(5.00, 0.02 * face / 100)
FX:      no commission; the 0.2-pip spread is inside the rate and shown as `spread_cost_c` in the preview
```
All fees are quantized to cents HALF_EVEN after the multiplication.

### 8.7 Margin requirements (reserved, disclosed; only futures IM moves cash)

```
Futures IM per contract from FUTURES_SPECS; posted Dr 1300 / Cr 1010 at the fill (sweep-in if needed); released on close.
Short option (Reg T style, per contract, x 100):
   naked call: max(premium + 0.20*S - max(0, K - S), premium + 0.10*S)
   naked put : max(premium + 0.20*S - max(0, S - K), premium + 0.10*K)
   covered call (long shares >= 100 per contract): 0; cash-secured put: K*100 reserved
   vertical spread (same expiry, opposite long): width * 100 (net of credit)
The option requirement is `option_req_c` in section 4.1; it reduces `available_c` and the sweep-out excess. It is not
moved to 1300 (listed-option margin is a requirement on the account, not a clearing deposit, in this model).
```

### 8.8 Applying a fill (`execution.apply_fill(tx, order, decision, rec, services) -> Fill`)

Inside the FILL_ORDER handler, in this order, all in one transaction:

1. **Economics**: `gross_c = inst.contract_value_c(price, qty)`; `fee_c = inst.fee_c(qty, account)`;
   `fx_rate` = 1 for USD, else the pair's mid from the cache (its record goes into `quote_json.fx`).
2. **Funding before posting** (BUY securities/options, futures IM, bond buys): `need_c = gross_c + fee_c` (futures:
   `IM_c + fee_c`); `shortfall_c = max(0, need_c - available_c(account, ccy))`. If `shortfall_c > 0`:
   Treasury available in that currency >= shortfall -> post the SWEEP_IN transfer entry (idem `sweep:<fill_id>`,
   `linked_type='fill'`) first; else if the account is PRIME with `pb_financing=1` and headroom -> PB_DRAW entry;
   else raise `Conflict(INSUFFICIENT_FUNDS)` -> the bus records it and the order is moved to CANCELLED/REJECTED
   (reason FUNDING) by the matcher in a follow-up CANCEL_ORDER command with id `cancel:funding:<order_id>`.
   A foreign-currency need is met from Treasury cash in that currency; if Treasury has none, the preview already
   offered an FX conversion leg (section 9.9) and the submit carries it; without it the fill is refused with
   `INSUFFICIENT_FUNDS_CCY`.
3. **Fill row** (`fills`, idem `match:...`), `quote_json` = the full `QuoteRecord` used.
4. **Postings** per section 3.3 (one journal entry `fill:<fill_id>`); **lots** opened/relieved FIFO; **settlements**
   rows (`settle:<fill_id>:<gl>`) with `due_date = sessions.settle_date(settle_market, trade_date)`; futures get
   `futures_settle` seed row (settle_px = fill price only for the day's VM formula - see 9.6).
5. **Shorts**: a SELL that opens/increases a short consumes the borrow linked to the order (`borrows.order_id`); a BUY
   that reduces a short returns the now-unneeded borrowed quantity in the same transaction (RETURN entry
   `borrow:return:<fill_id>`; collateral back Dr 1010 / Cr 1400; fees settled Dr 2310 / Cr 1010; rebate Dr 1010 / Cr 1410).
6. **Order update**: filled_qty, avg_px, status (FILLED / PARTIALLY_FILLED), `last_bar_ts`, `first_eval_done = 1`,
   `order_events` row.
7. **Hedge package**: if `orders.package_id` is set and this is the primary leg, create or resize the hedge child
   orders to the filled quantity (section 9.10) - new WORKING orders in the same transaction; they match on their own
   quotes afterwards ("sequential, dependent on the primary fill").
8. **Quick integrity** on the touched entries (bus does it).

### 8.9 Shorts: locate -> borrow -> sale (`lending.py`)

```python
@dataclass
class LocateQuote: instrument_id: str; qty: int; available: bool; qty_available: int; fee_rate: Decimal; rebate_rate: Decimal
                   tier: str; lender: str; collateral_c: int; mark: Decimal; mark_quality: str; valid_until: int; basis: str
def locate(services, account, inst, qty) -> LocateQuote
def open_borrow(tx, services, cmd, account, inst, qty, locate: LocateQuote, order_id: str | None) -> str   # borrow_id
def return_borrow(tx, services, cmd, borrow_id: str, qty: int, fill_id: str | None) -> None
def daily_accrual_lines(tx, borrow, date: str, mark: Decimal, sofr: Decimal) -> list[Line]      # fee + rebate for one day
def collateral_mark_lines(tx, borrow, date: str, mark: Decimal) -> tuple[list[Line], int]        # one movement to 102 %
```
Availability and tier (heuristic from observable data, printed on the ticket as "borrow availability is estimated from
FINRA daily short volume and the name's average volume; a real locate would come from a lender"):

| Data | Tier | fee |
|---|---|---|
| in universe, FINRA short-volume ratio < 0.60 and ADV >= 1 M shares | GC | 0.30 % |
| ratio >= 0.60 or ADV in [250k, 1M) | T1 | 1.00 % |
| ADV < 250k | T2 | 5.00 % |
| no FINRA row for the last 3 sessions, or preferred/ADR with ADV < 100k | T3 | 25.00 % with `qty_available = min(qty, 0.10 * ADV)` |
| not in the universe or no ADV | none | `available = False` -> NO_BORROW |

`rebate_rate = max(0, SOFR - 0.50 %)`; `collateral_c = to_c(1.02 * qty * mark)` (the sale's preview shows it).
The borrow is opened inside the SUBMIT_ORDER transaction (collateral swept from Treasury if needed), linked to the
order; `borrow_cleanup` (section 9) returns whatever an unfilled/cancelled order left unused at the next tick.
Recalls: never generated; a notice (`attention` INFO) appears when the FINRA ratio of a shorted name is >= 0.80 for
three consecutive sessions ("hard-to-borrow tightening: a lender could recall; consider covering"). No automatic buy-in
in this build (open question Q4).

---

## 9. Lifecycle jobs, keyed to the real clock (`lifecycle.py`, `credit.py`, `fx.py`)

### 9.1 Scheduler

```python
class Job(Protocol):
    name: str
    def due(self, conn, clock) -> list[str]          # keys that should run now and are not in jobs_done
    def run(self, tx: Tx, key: str, services) -> dict   # the handler body (called through the bus as kind=name)
class LifecycleScheduler(threading.Thread):
    def __init__(self, bus, db, services, clock, log, interval_s: int = 30)
    def tick(self, at: int | None = None) -> dict    # runs every due job key through bus.execute(Command("job:<name>:<key>", ...)); tests call this with a FakeClock
    def stop(self, timeout=3.0) -> None
```
Order within a tick: SETTLE_DUE, ACCRUE_DAILY, PAY_MONTHLY, COUPON, DIVIDEND, OPTION_EXPIRY, FUTURES_VM,
FUTURES_FINAL, COLLATERAL_MARK, BORROW_CLEANUP, SWEEP_OUT, EOD_SNAPSHOT, EXPIRE_ORDERS, HOUSEKEEPING, INTEGRITY_FULL.
A failing key is logged, raises an attention row, and is retried every tick; after 5 consecutive failures the key backs
off 30 minutes and the attention level becomes ERROR. Catch-up after downtime is automatic because `due()` derives keys
from state (dates since the last accrual, settlements with past due dates, ...), not from "what happened while we ran".

### 9.2 Job table

| Job | When (ET) | Key | What it does | Postings / idem keys |
|---|---|---|---|---|
| SETTLE_DUE | from 09:00 on `due_date`, every tick | `settle_id` | moves one pending settlement to cash; a payable that would take 1010 below zero first sweeps in the shortfall from Treasury (`sweep:settle:<settle_id>`); foreign-currency payables use the account's cash in that currency, else Treasury's, else attention ERROR `SETTLEMENT_UNFUNDED` (settlement stays PENDING, retried) | `settle:<settle_id>` |
| ACCRUE_DAILY | from 00:30 for every calendar date D < today not yet accrued per object | `<object_id>:<D>` | borrow fee `to_c(qty*mark_D*fee/360)` and rebate `to_c(collateral_c*rebate/360)`; currency loan `principal*(index_D+spread)/basis`; PB financing `balance(2700)*(SOFR+1.50)/360`; Treasury cash `settled_USD_end_of_D*(SOFR-0.50)/360` (only when positive); bond accrued = ACT/ACT accrued(D) - accrued(D-1). `mark_D` = the `marks` row of D, else the last valid mark before D (weekends). `index_D` = latest FRED value with date <= D; if none within 10 days -> attention WARN and the day is skipped (never defaulted) | `accrual:<KIND>:<object_id>:<D>` |
| PAY_MONTHLY | 1st calendar day 00:45 | `<account>:<YYYY-MM>` | settles accrued interest/fees of the previous month in cash: 2310 -> 1010, 1410 -> 1010, 1230 -> 1010 (Treasury), 2300 -> 1010 (PB) | `pay:<account>:<YYYY-MM>` |
| COUPON | coupon dates 09:00 (bond spec schedule, rolled following) | `<account>:<bond>:<date>` | Dr 1010 coupon / Cr 1120; maturity: Dr 1010 face / Cr 1110 cost, 4000 difference | `coupon:...`, `maturity:...` |
| DIVIDEND | ex-dates from Yahoo chart `events=div` (held names only; fetched daily) | `<account>:<inst>:<ex_date>` | long: Dr 1210 / Cr 4200 on ex-date, cash 14 calendar days later (`divpay:`); short: Dr 5400 / Cr 2320, paid likewise. The pay-date convention is disclosed ("pay date not in the free feed; T+14 convention") | `div:...`, `divpay:...` |
| OPTION_EXPIRY | expiry date, from 16:20 (index options 16:35), retried every 5 min until 20:00, then with the next morning's `prev_close` | `<account>:<option_id>` | underlying close = cache record with `quote_time` on expiry date and session CLOSED; intrinsic >= 0.01 -> EXERCISE (long) / ASSIGNMENT (short) fills at the strike with origin EXERCISE/ASSIGNMENT; else EXPIRY fill at 0 (OTM). Cash-settled index options settle intrinsic in cash. Assignment of a short call without long shares opens a short that needs a borrow: the job opens a GC borrow automatically (attention WARN) or, if unavailable, closes the resulting short with a BUY_IN fill at the close plus the 5700 penalty 1 % (disclosed) | `expiry:<account>:<option_id>` |
| FUTURES_VM | `settle_time_et + 5 min` on trading days | `<account>:<inst>:<date>` | `S_t` = last record with `quote_time <= settle_time` (else Yahoo `daily()` close for the date), labelled "observed close"; `VM = qty_prev*(S_t - S_{t-1})*mult + sum over today's fills q_i*(S_t - p_i)*mult`; posts 4400 vs 1010 (sweep-in on a loss that overdraws); IM top-up/release to the spec; writes `futures_settle` | `vm:<account>:<inst>:<date>` |
| FUTURES_FINAL | last trade date, after VM | `<account>:<inst>` | FUTURES_FINAL fill closing the position at `S_t`, IM released, instrument EXPIRED | `final:<account>:<inst>` |
| COLLATERAL_MARK | 17:10 on trading days | `<borrow_id>:<date>` | `required = to_c(1.02 * qty_outstanding * mark_close)`; `delta = required - collateral_c`; one movement (call: Dr 1400 / Cr 1010 with sweep-in; return: Dr 1010 / Cr 1400); `borrows.collateral_c = required` | `coll:<borrow_id>:<date>` |
| BORROW_CLEANUP | every tick | `<borrow_id>` | returns borrowed quantity not backing a short (order cancelled/expired/partly filled) | `borrow:return:<borrow_id>:<date>` |
| SWEEP_OUT | 17:30 on trading days, per account with `sweep_out=1` | `<account>:<ccy>:<date>` | `excess = settled - payables_pending - reserved_orders - option_req - buffer_c`; if `excess > 0` transfer to Treasury (`SWEEP_OUT`) | `sweep:out:<account>:<ccy>:<date>` |
| EOD_SNAPSHOT | 17:45 on trading days (after VM, marks, sweeps) | `<account>:<date>` (+ `ALL`) | writes `marks` for every held instrument (provenance) and `nav_snapshots`; `degraded=1` when any held mark is `unavailable` or older than 1 trading day | `snap:<account>:<date>` |
| EXPIRE_ORDERS | at every session close + every tick for GTC age | `<order_id>` | DAY -> EXPIRED at `expires_ts`; GTC after 90 days; orders on expired instruments | `expire:<order_id>` |
| HOUSEKEEPING | 00:15 daily; first business day monthly | `<date>` | list futures months, mark expired options/futures, refresh foreign exchange sessions, monthly Treasury benchmarks, FINRA file, purge order_events older than 180 d | `hk:<date>` |
| INTEGRITY_FULL | hourly at :05 | `<YYYY-MM-DDTHH>` | `accounting.integrity('full')` -> meta + attention | `integrity:<hour>` |

Every accrual uses calendar days (weekends included); fills, settlements, expiries and marks only on trading days of
the instrument's session group. Day-count bases from `RATES.DAYCOUNT_*`.

### 9.3 Settlement T+n

`settle_date = sessions.settle_date(inst.settle_market, trade_date)`: US_EQUITY/US_OPTIONS/US_TREASURY/US_CORP_BOND
T+1, FX_SPOT T+2, foreign equities per `SettlementConfig` (EU/UK/HK/JP T+2, IN T+1), FUTURES none (VM daily). A fill
after the session close on day T is still trade date T (NY date of the fill) - which cannot happen because fills need
an open session; a Sunday-evening futures fill has trade date = the following business day per CME convention (the
spec's `trade_date_rule = 'next_business_day_after_17:00'`).

### 9.4 Options exercise/assignment - the exact rule (A12)

```
S_close = underlying mark with quality 'close' whose quote_time date == expiry (ET); index options use the index record
intrinsic_call = max(0, S_close - K); intrinsic_put = max(0, K - S_close)
if intrinsic >= 0.01: long -> EXERCISE fill: BUY (call) / SELL (put) `qty*100` shares at K, origin EXERCISE, premium rolled
                      into basis (A7); short -> ASSIGNMENT fill: SELL (call) / BUY (put) at K, origin ASSIGNMENT
else: EXPIRY fill at price 0 (OTM): long realises -premium (Dr 4000 / Cr 1700); short realises +premium (Dr 2900 / Cr 4000)
```
User-initiated early exercise = EXERCISE_OPTION command (long American only, during the option session).

### 9.5 Variation margin formula check (two days, from test H)

Long 2 ES at 7,750.00 filled Tuesday; Tuesday observed close 7,762.25; Wednesday 7,740.50; multiplier 50.
Tue: `qty_prev = 0`; fills term `2 * (7,762.25 - 7,750.00) * 50 = 2 * 12.25 * 50 = 1,225.00` -> Dr 1010 / Cr 4400 1,225.00.
Wed: `qty_prev = 2`; `2 * (7,740.50 - 7,762.25) * 50 = 2 * (-21.75) * 50 = -2,175.00` -> Dr 4400 / Cr 1010 2,175.00.
Cumulative = -950.00 = `2 * (7,740.50 - 7,750.00) * 50`.

### 9.6 Treasury interest, loans, PB financing (daily, ACT/360 unless GBP)

```
Treasury cash interest for date D: settled_USD(end of D) * (SOFR_D - 0.50 %) / 360, only if positive -> Dr 1230 / Cr 4300
Currency loan: principal * (index_D + 0.75 %) / basis -> Dr 5150 / Cr 2755 (ccy), base at the day's rate
PB financing: balance(2700) * (SOFR_D + 1.50 %) / 360 -> Dr 5100 / Cr 2300
Borrow: fee qty_out * mark_D * fee_rate / 360 -> Dr 5300 / Cr 2310; rebate collateral_c * rebate / 360 -> Dr 1410 / Cr 4350
```
`settled_USD(end of D)` = `SUM(amount_c)` of 1010 lines with `ts <= epoch(D, 23:59:59 NY)` (the ledger is timestamped,
so historical balances are exact).

### 9.7 Calls and attention

A collateral call, VM loss or settlement that the Treasury cannot fund produces an attention `ERROR` with the
shortfall and the account; no automatic liquidation (decisions: risk only warns). The account's `available_c` goes
negative and the ticket for that account shows a red "funding shortfall" banner until it is resolved (deposit,
transfer, or closing positions).

### 9.8 Sweep-out worked example (from test B)

USX after the AAPL buy (before settlement): settled 1,697,525.00, payables 1,697,525.00, no orders, buffer 10,000.00
-> `excess = 1,697,525 - 1,697,525 - 0 - 0 - 10,000 = -10,000 < 0` -> no sweep-out. After T+1 settlement: settled 0,
payables 0 -> excess negative -> nothing. Had the account held 50,000 settled after settlement, 40,000 would return.

### 9.9 Thin slices: currency loans, FX, repo, PB (`credit.py`, `fx.py`)

```python
# credit.py
def open_loan(tx, services, cmd, account, currency, amount_c, term_days: int | None) -> dict        # Dr 1010:ccy / Cr 2750:ccy; lender "Treasury Desk (model)"; rate = policy index + 0.75 %, floating daily
def repay_loan(tx, services, cmd, loan_id, amount_c) -> dict  # pays accrued first then principal; refused when the account's settled cash in that currency (after a Treasury sweep in that currency) is short -> INSUFFICIENT_FUNDS_CCY with the EUR shortfall and the hint "buy EUR with Convert"
def pb_draw(tx, services, cmd, account, amount_c) -> dict; def pb_repay(...)   # PRIME accounts only, 2700
def repo_open(tx, services, cmd, account, side, bond, face, haircut) -> dict   # thin: price = bond mark (computed), principal = face*px/100*(1-haircut); REPO: Dr 1010 / Cr 2500 and the bond flagged encumbered (orders cannot sell it); REVERSE: Dr 1500 / Cr 1010; daily accrual; maturity closes (section 9.2 ACCRUE_DAILY/SETTLE_DUE handle it with keys repo:<id>:<D>)
# fx.py
def spot_preview(services, account, buy_ccy, sell_ccy, amount_c, amount_ccy) -> dict    # rate = mid +- 0.2 pip, both legs, value date T+2 (US + both currencies' weekends), settled/pending effect per currency
def fx_spot(tx, services, cmd, ...) -> dict          # Dr 1250:buy / Cr 2350:sell; settlements rows (+buy, -sell) due T+2 (`settle:<fill_id>:1250`, `settle:<fill_id>:2350`); the FX trade is a `fills` row on the FX_PAIR instrument (qty = base amount cents, price = rate)
def fx_forward(tx, services, cmd, ..., value_date) -> dict   # fx_forwards row; rate from marks.fx_forward (CIP, computed); no cash until value date; at value date SETTLE_DUE books Dr 1010:buy / Cr 1010:sell at K with the difference to spot in 4650
```
Repo collateral encumbrance: `settings.encumbered` map (bond -> face) checked by `orders.validate` (`BOND_ENCUMBERED`).
OTC IRS/TRS via PB: **deferred** (listed in the UI as "available after the first release"; the `hedge_packages`
table and PB fee schedule already carry what they need).

### 9.10 Hedges, compliance, risk (thin, inform-only)

```python
# hedges.py
def candidates(services, account, inst, side, qty, chain: ChainSnapshot | None) -> list[HedgeCandidate]
   # long equity/ETF: PUT_7PCT (strike nearest to 0.93*S, nearest expiry >= 90 days), COLLAR (that put + call nearest 1.07*S),
   # PUT_SPREAD (0.93/0.85), INDEX_FUTURE_OVERLAY (ES/NQ contracts = round(notional * beta / (S_fut*50)), beta from universe);
   # short equity: CALL_7PCT, CALL_SPREAD, COLLAR; bond: ZN/ZB contracts DV01-matched (DV01 from marks.bond_price bumps);
   # FX: forward to the next quarter end or an FX option (unsupported -> only the forward); futures: option on the future (unsupported in this build -> none)
   # each candidate carries legs priced from the live chain (Cboe ask/bid), cost_c, protection level, and the quote quality
def package(tx, services, cmd, primary_order_id, candidate: HedgeCandidate, ratio: Decimal) -> str   # hedge_packages row + child orders sized to the primary's filled qty, re-sized on partial fills (execution step 7)
# compliance.py  (inform only; each notice: rule, threshold used, data used, message, "verify the current rule text")
def pretrade(services, account, inst, side, qty, preview) -> list[Notice]   # Reg SHO locate 203(b); Rule 201 (10 % drop vs prev_close -> short only on an uptick: notice); Reg T/4210 50 % initial (cash-style account note); PDT (4 day trades in 5 days under 25k - never under 25k here, still listed); 13D/G 5 % and Section 16 10 % of shares outstanding (SEC company_tickers + companyfacts weekly cache; "unavailable" when not cached); 13h-1 large trader (2 M shares or 20 M USD/day); option position limits (static table, 25k/250k contracts classes); futures position limits (CFTC static); self-trading between own accounts (an opposite working order in another account); wash sale (a loss sale with a buy within 30 days, informational); EU/UK short disclosure 0.1 %/0.5 % for foreign names
def holdings(services, scope) -> list[Notice]        # continuous: 13F >= 100 M, 13D/G, 13h-1, Form SHO monthly, option/futures limits
# risk.py (warnings)
def report(services, scope) -> RiskView   # exposure by class/currency, top-10 positions % NAV, delta/gamma/vega/theta by underlying from Cboe greeks, DV01 (bond price +-1 bp), FX exposure by ccy, scenario table (equities +-5/10/20 %, rates +-25/50/100 bp, FX +-5 %) by full revaluation through marks.*, concentration > 20 % NAV -> warning, gross leverage > 2x -> warning
```

---

## 10. HTTP API (`api.py`)

Conventions: JSON in/out, `Content-Type: application/json`, `Cache-Control: no-store`. Loopback only (the server
binds 127.0.0.1; there is no access key in this build). Every mutating route (POST/DELETE/PATCH) requires
`request_id` in the body (or header `X-Request-Id`); missing -> 400 `BAD_REQUEST_ID`. Cents fields are returned as
decimal strings (`"1697525.00"`) under the same name without `_c`; dates ISO; epochs as integers plus `*_ny` strings
for display. Errors: `{"error": {"code", "message", "details"}}` with the HTTP codes of section 3.4 (400 validation,
404 not found, 409 conflict/rejection, 500 internal, 503 retry). A replayed request returns the stored status and body
plus header `X-Replayed: 1`.

| Method | Path | Params | Response | Errors |
|---|---|---|---|---|
| GET | /api/health | - | `{version, schema, pid, uptime_s, started_ts, db_path, state_version, integrity:{ok, checked_at, problems:[...]}, providers:{name:{ok, last_ok_ts, last_error, down_until_ts}}, worker:{queue, fetches_per_min, pinned, subscribed}, lifecycle:{last_tick_ts, due, failing:[...]}, attention_open, clock:{utc, ny, trading_day}}` | - (never blocks on the writer lock) |
| POST | /api/shutdown | loopback only | `{shutting_down: true}` | 403 off-loopback |
| GET | /api/state | - | bootstrap bundle: `{accounts:[...], settings, sessions:{group: SessionState}, clock, attention:[open rows], integrity, version}` | - |
| GET | /api/clock | - | `{utc, ny, trading_day, sessions}` | - |
| GET | /api/accounts | - | `[{account_id, name, kind, broker, status, sweep_out, buffer, pb_financing, nav, available_usd}]` | - |
| POST | /api/accounts | `request_id, kind:'PRIME', broker` | the new account + its fee schedule | 400 UNKNOWN_BROKER, 409 EXISTS |
| PATCH | /api/accounts/{id} | `request_id, sweep_out?, buffer?, pb_financing?` | the account | 404 |
| GET | /api/accounts/{id}/summary | - | `{account, cash:{ccy: CashView}, buying_power:{ccy: BuyingPower}, nav: NavView, pnl:{TODAY, MTD, YTD, ITD}, positions_count, working_orders, pending_settlements:[...], obligations: ObligationsView, attention:[...]}` | 404 |
| GET | /api/accounts/{id}/positions | - | `[PositionView]` each with `mark: MarkView` | 404 |
| GET | /api/accounts/{id}/orders | `status?=open|all, limit?` | `[Order]` with the last event | 404 |
| GET | /api/accounts/{id}/fills | `from?, to?, instrument?` | `[Fill]` with `quote` provenance | 404 |
| GET | /api/accounts/{id}/transactions | `from?, to?, kinds?, instrument?, limit?, offset?` | `[TxnRow]` (every journal entry; lines expanded) | 404 |
| GET | /api/accounts/{id}/cash | - | `{ccy: CashView}` + history of transfers | 404 |
| GET | /api/accounts/{id}/borrows, /loans, /settlements, /forwards | - | rows with accrued-to-date | 404 |
| GET | /api/portfolio | - | consolidated: positions across accounts, nav, pnl, exposures by class | - |
| GET | /api/accounting/{scope}/balance-sheet | scope = account or ALL | `BalanceSheet` (rows at cost + overlay + NAV, ties) | 404 |
| GET | /api/accounting/{scope}/pnl | `period=TODAY|MTD|YTD|ITD` | `PnlView` with explain | 404 |
| GET | /api/accounting/{scope}/trial-balance | - | rows per (gl, ccy) with debit/credit totals and `balanced` | 404 |
| GET | /api/accounting/journal/{entry_id} | - | the entry with lines, request, linked refs | 404 |
| GET | /api/integrity | `full?=1` | `IntegrityReport` (full runs synchronously on a reader, about 100 ms) | - |
| GET | /api/treasury | - | `TreasuryView` + advances per account + capital history | - |
| POST | /api/treasury/deposit | `request_id, currency, amount, memo?` | `{transfer, treasury}` | 400 AMOUNT |
| POST | /api/treasury/withdraw | same | same | 409 INSUFFICIENT_FUNDS |
| POST | /api/transfers | `request_id, from_account, to_account, currency, amount, memo?` | `{transfer, from_cash, to_cash}` | 409 INSUFFICIENT_FUNDS |
| GET | /api/instruments | `q, kinds?, account?, limit?` | `[Instrument summary + quote]` (search) | - |
| GET | /api/instruments/{id} | - | `Instrument` + `quote` + `mark` + `position` in each account + sessions | 404 |
| GET | /api/instruments/{id}/chain | `expiry?` | `{underlying: QuoteRecord, expiries:[...], rows:[ChainRow + instrument_id + freshness]}` or 202 `{status:'fetching', retry_ms: 1500}` | 404, 409 NO_CHAIN (not optionable) |
| GET | /api/instruments/{id}/bars | `range=1d` | `{bars: bars_tail, prev_close, session}` (what the cache has; no history service in this build) | 404 |
| GET | /api/quotes | `ids=a,b,c` (<= 200) | `{quotes:{id: QuoteRecord+quality+age_s}, sessions, server_time}`; side effect: `cache.subscribe(ids, 90)` | 400 TOO_MANY |
| GET | /api/markets/{account_kind} | - | the account page lists: US_EXCHANGE -> `{equities: watchlist+held, futures: front contracts by group, options_underlyings}`; DEBT -> `{treasuries, corporates, curve}`; FOREIGN_FX -> `{listings, fx_pairs, forwards}`; CREDIT -> `{borrows, loans, repos, rates}` | 404 |
| GET | /api/curve | - | `{par: ParCurve, as_of, shifts:{tenor: bps, quote_time}, quality}` | - |
| GET | /api/rates | - | `{SOFR:{value, as_of}, policy:{ccy:{value, as_of, series}}, oas:{bucket:{value, as_of}}}` | - |
| POST | /api/orders/preview | preview fields (section 8.2) | `Preview` | 400 (same validation codes, as `blockers` not errors) |
| POST | /api/orders | `request_id` + preview fields + `borrow?: true, hedge?: {candidate_id, ratio?}, snapshot_id` | `{order, fills:[...], borrow?, hedge_package?, warnings:[...]}` | 409 QUOTE_STALE / NO_BORROW / INSUFFICIENT_FUNDS / OPTION_MARGIN; 400 validation |
| GET | /api/orders/{id} | - | `Order` + events + fills | 404 |
| DELETE | /api/orders/{id} | `request_id` | `{order}` (CANCELLED) | 409 NOT_OPEN |
| POST | /api/lending/locate | `account_id, instrument_id, qty` | `LocateQuote` | 404 |
| POST | /api/lending/borrow | `request_id, account_id, instrument_id, qty, locate` | `{borrow, collateral_transfer?}` | 409 NO_BORROW / INSUFFICIENT_FUNDS |
| POST | /api/lending/return | `request_id, borrow_id, qty` | `{borrow}` | 409 BACKS_SHORT (cannot return what still backs a short) |
| POST | /api/loans | `request_id, account_id, currency, amount, term_days?` | `{loan, cash}` | 400 CURRENCY (no policy index) |
| POST | /api/loans/{id}/repay | `request_id, amount` | `{loan, cash}` | 409 INSUFFICIENT_FUNDS_CCY |
| POST | /api/fx/preview | `account_id, buy_ccy, sell_ccy, amount, amount_ccy, value_date?` | `{rate, spread_cost, legs, value_date, quality, forward_inputs?}` | 409 NO_QUOTE |
| POST | /api/fx/spot | `request_id` + preview fields | `{fill, settlements, cash}` | 409 QUOTE_STALE / INSUFFICIENT_FUNDS_CCY |
| POST | /api/fx/forward | `request_id` + preview fields + `value_date` | `{forward}` | 409 |
| POST | /api/options/exercise | `request_id, account_id, instrument_id, qty` | `{fills, position}` | 409 NOT_LONG / SESSION_CLOSED |
| POST | /api/pb/{id}/draw, /repay | `request_id, amount` | `{account, cash}` | 409 |
| GET | /api/hedges | `account_id, instrument_id, side, qty` | `[HedgeCandidate]` (202 while the chain is fetching) | - |
| GET | /api/risk/{scope} | - | `RiskView` | - |
| GET | /api/compliance/{scope} | - | `[Notice]` (holdings) | - |
| GET | /api/attention | `all?=1` | `[attention rows]` | - |
| POST | /api/attention/{id}/resolve | `request_id` | row | 404 |
| GET | /api/activity | `limit?, account?` | merged, newest first: order events, fills, transfers, job results, attention | - |
| GET | /api/jobs | - | `{jobs:[{name, last_run_ts, last_key, next_due, failing}]}` | - |
| GET/PATCH | /api/settings | PATCH: `request_id, {key: value}` | settings map (participation_rate, session_overrides, theme, polling) | 400 |
| GET | /api/watchlists | `account?` | lists | - |
| POST/DELETE | /api/watchlists | `request_id, account_id, name, instrument_id` | list | - |
| GET | /api/export/transactions.csv | `scope, from?, to?` | CSV (text/csv) | - |
| GET | /api/export/positions.csv | `scope` | CSV | - |

`api.Router`:

```python
class Router:
    def __init__(self, services: Services, bus: CommandBus, log)
    def handle(self, method: str, path: str, query: dict, body: dict, client_ip: str) -> tuple[int, dict | bytes, dict]   # (status, body, headers)
ROUTES: list[tuple[str, re.Pattern, Callable]]   # compiled once; path params by named groups
```
Mutating handlers build a `Command` and return `bus.execute(...)` mapped to HTTP. Read handlers call `Accounting`,
`QuoteCache`, `MarkProvider`, `InstrumentMaster`, `SessionTable` directly on the request thread with a reader
connection - never the writer lock, never the network. The only network-triggering reads (`/chain`, `/hedges`) enqueue a
fetch and answer 202 until the cache has it.

---

## 11. Server threading model and operations (`server.py`)

```python
class TerminalServer(ThreadingHTTPServer):
    allow_reuse_address = False; daemon_threads = True; request_queue_size = 64
    def server_bind(self):              # Windows: setsockopt(SOL_SOCKET, SO_EXCLUSIVEADDRUSE, 1) before bind -> a second bind fails (WinError 10048)
class Runtime:                          # everything the process owns
    db, clock, instruments, sessions, cache, providers, worker, marks, accounting, bus, matcher, lifecycle, router, httpd, log, lock_file
def serve(port: int = DEFAULT_PORT, home: str | None = None, db: str | None = None) -> int    # returns the exit code
```

Startup sequence (exit codes in brackets):
1. Acquire the single-instance lock `<home>/terminal.lock` (`msvcrt.locking(LK_NBLCK)` on Windows, `fcntl.flock`
   elsewhere), write `{pid, port, started_ts, version}`; held for the process lifetime [3: "another terminal server
   (PID n, started …) owns <home>"].
2. Rotating log `<home>/terminal.log` (5 MB x 3) via `finsim.log.get_logger("terminal", path, "INFO")`.
3. Open the DB: backup -> migrate [4: newer schema] -> `INIT_BOOK` (idempotent) -> `integrity('full')` recorded.
4. Load `InstrumentMaster`, `QuoteCache.load_persisted()` (records come back as `close`/`stale` until refetched; the
   UI shows "last valid mark 16:00 yesterday" rather than nothing).
5. Bind the port exclusively [3: port in use - the message names the PID from the lock file if any].
6. Start `QuoteWorker` (scheduler + 2 fetch threads) and `LifecycleScheduler`.
7. `serve_forever()`. Health answers only from this point, so the launcher's probe cannot see a half-started server.

Threads: HTTP request threads (short-lived, daemon), quote scheduler (1), fetch pool (2), lifecycle (1). The writer
`RLock` is the only lock that serialises anything; a command normally holds it for 1-10 ms. Reads are lock-free
(WAL, per-thread connections). The worker never holds the lock during network I/O.

Provider errors: every failure is counted in `provider_health` and surfaced in `/api/health.providers` and in the
attention strip (one open WARN row per provider, auto-resolved on the next success). Retries: 3 attempts (1, 2, 4 s);
429 -> 60 s cooldown; 5 consecutive failures -> 120 s circuit breaker; a disconnected machine therefore costs one
request per provider every two minutes and the UI shows "offline since 10:12, last valid marks shown".

Graceful shutdown (`POST /api/shutdown`, Ctrl-C, or `stop`): set the stop event -> lifecycle and worker finish their
current unit (<= 3 s) -> `cache.persist()` -> `httpd.shutdown()` from a helper thread -> `server_close()` ->
`db.close()` -> release the lock file -> exit 0. A command in flight completes or rolls back; nothing is half-written.

Memory budget: instruments ~1.5k x 1 KB, cache 2k x 1 KB, chains (<= 10 underlyings on screen) ~600 KB each after
condensing, Python runtime ~40 MB: comfortably under 150 MB. The SPX JSON parse peaks at ~120 MB for a second; the worker
serialises chain fetches (one at a time) so two never overlap.

---

## 12. UI (`static/`, vanilla JS, no build step)

Files: `index.html` (shell), `terminal.css` (dark tokens on `:root`, light under `[data-theme=light]`, mono numbers),
`core.js` (fetch wrapper with request ids and retry on 503, formatting, tables, modal/toast), `store.js` (state +
polling), `quotes.js` (quote cells, freshness dots, sparkline canvas), `ticket.js` (ticket + preview + hedge panel),
`pages/treasury.js, account.js (shared by all trading accounts), portfolio.js, accounting.js, risk.js, activity.js,
settings.js`. Routing by hash: `#/treasury`, `#/account/USX`, `#/account/USX/i/AAPL`, `#/portfolio`, `#/accounting`,
`#/risk`, `#/activity`, `#/settings`.

Shell: left sidebar = **accounts** (Treasury; US Exchange; Debt Instruments; Credit Instruments; Foreign Markets & FX;
each opened PB; "+ Open prime broker") then Portfolio, Accounting, Risk, Activity, Settings. Header: active account
badge with its available cash and NAV, New York clock, session pills (US eq / options / CME / FX with open/closed and
next open), provider status dot, global instrument search, and the **Attention strip** (open attention rows: integrity,
provider down, funding shortfall, job failures; click to open Activity). A permanent footer tag: "PAPER TRADING -
fictional capital; prices from free delayed feeds (Yahoo Finance, Cboe); computed marks are models".

Account page (`account.js`) layout (3 columns, collapsible on narrow widths):
- **Markets** (left): tabs per account kind (US Exchange: Equities & ETFs / Futures / Options; Debt: Treasuries /
  Corporates / Curve; Foreign & FX: Listings / FX; Credit: Borrows / Loans / Repo / Rates). Each row: symbol, name,
  last, change, bid/ask (options), freshness cell, session state, position qty. Watchlist editing inline.
- **Workspace** (centre): the selected instrument: big quote with freshness, source, delay and quote time; 1-minute
  sparkline from `bars_tail`; session and next open; for optionables the chain (expiry tabs, strikes, bid/ask/size/IV/
  delta/OI with freshness per row); the position in this account (qty, avg, mark, unrealised, realised); working orders.
- **Ticket** (right): instrument, side, qty, type, limit/stop, TIF, "borrow shares" (auto-checked for a short),
  "convert currency" leg when needed; the live **Preview** panel re-renders 250 ms after any change (POST
  /api/orders/preview): expected price and its basis, quality chip, cap note, fees, settlement date, cash need,
  available/sweep/Treasury lines, borrow leg, margin, warnings, blockers (Submit disabled while any blocker exists);
  the **Hedge panel** appears under the preview as soon as an instrument is entered (GET /api/hedges, 202 -> spinner):
  candidate cards with legs, cost, protection, quality; one can be ticked and its ratio edited; Submit then sends
  `hedge: {candidate_id, ratio}`. A submit toast shows the fills (or "resting until Mon 09:30") and errors verbatim.
- **Bottom tabs**: Positions | Orders | Transactions | Cash & Financing | Obligations (pending settlements, margin,
  collateral, borrows, loans with accrued-to-date). Every table has Copy and CSV.

Treasury page: cash by currency (settled/pending), capital history, advances per account, interest accrued, Deposit /
Withdraw / Transfer forms (amount + account, done), sweep settings per account. Portfolio: all positions across accounts
grouped by class with subtotals, NAV/P&L tiles (TODAY/MTD/YTD/ITD) from `/api/portfolio`. Accounting: scope selector
(account or All) with tabs P&L / Balance sheet / Trial balance / Transactions / Journal entry drill-down / Integrity.
Risk: exposures, greeks, scenarios, concentration, compliance notices (all labelled "warnings only"). Activity: the
merged feed. Settings: participation rate, buffer per account, sweep-out switches, theme, polling, session overrides.

State and polling (`store.js`):
- `state = {accounts, selected, instruments(lru), quotes(map), positions(map by account), orders, summary, health, sessions, attention}`.
- `/api/health` every 15 s; if `state_version` changed -> refresh summary/positions/orders of the visible account and
  the attention strip. `/api/quotes?ids=` every 5 s for the ids on screen (markets table + workspace + positions);
  every 2 s while a MARKET order of the visible account is working. `/api/accounts/{id}/summary` every 10 s.
  Orders every 5 s while any is working. Chain every 30 s while visible. Nothing polls when the tab is hidden
  (`document.visibilityState`).
- Request ids: `crypto.randomUUID()` per submit, kept in the pending-submit map; a 503 retries the same id 3 times;
  a network error after submit re-GETs `/api/orders?status=all` before enabling the button again (so a lost response
  never produces a duplicate order).

Freshness and attention display: every price cell has a dot and a tooltip "16:45:12 ET - Yahoo Finance - up to 15 min
delayed - age 1m 05s": green `delayed`, blue `close` (text "close 16:00"), amber `stale` (struck-through price, "stale
since 14:02"), grey `unavailable` (dash), italic with a "model" chip for `computed` (tooltip lists the inputs and their
times). Executable state is a separate chip on the ticket ("executable" / "not executable: session closed"). The
attention strip is red for ERROR (integrity, unfunded settlement), amber for WARN (provider down, degraded snapshot),
blue for INFO (recall notice, compliance). It is never hidden by a page change.

---

## 13. Launcher and CLI (`terminal/app.py`), `finsim/__main__.py` re-pointed

`python -m finsim <cmd>` (from `C:\Users\logan\intelligence-terminal\finsim`) dispatches to `finsim.terminal.app.main`:

| Command | What it does | Exit |
|---|---|---|
| `serve [--port 8765] [--home DIR] [--db FILE]` | foreground server (section 11) | 0 ok, 3 another instance/port, 4 schema newer, 5 DB error |
| `stop [--timeout 10]` | `POST /api/shutdown`; waits for `/api/health` to stop answering; then reads the lock file's PID: if that PID is still alive and its command line contains `finsim` (`wmic`/`tasklist /V` check), prints it and exits 2 (never kills blindly) | 0 stopped, 1 nothing running, 2 still alive |
| `status` | prints version, home, db, port, health (served code version, state_version, integrity ok/failed, providers, open attention), lock-file PID, login entry present, backups count | 0 running, 1 not running |
| `install [--no-open]` | writes `<home>\FinSim Server.vbs`, `<home>\finsim-open.vbs`, `<home>\finsim.ico`; copies the server VBS into the Startup folder as `FinSim Server.vbs` (REPLACING the old file of the same name); deletes the old scheduled task `FinSim Server` and the old `finsim-server.vbs`/`finsim-open.vbs`/`make-shortcuts.vbs`; creates Desktop + Start Menu `FinSim.lnk` via a generated `make-shortcuts.vbs`; stops any old server on 8765 (`/api/shutdown` on the old API works too) and starts the new one; opens the window unless `--no-open` | 0 |
| `open` | `status`-style probe; starts `serve` detached (pythonw) if nothing answers; waits up to 60 s; opens the Chrome/Edge/Brave app window (`--app=http://127.0.0.1:8765/ --user-data-dir=<home>\window-profile`) else the default browser | 0/1 |
| `uninstall [--purge]` | removes Startup entry, shortcuts, VBS files, window profile; stops the server; keeps `terminal.db` unless `--purge` (never touches `finsim.db`) | 0 |
| `legacy serve [--port 8766]` | runs the OLD simulation server `finsim.api.server.serve(db=<home>/finsim.db, port=8766)` for reading old saves. Documented read-only intent: the old code may still append events on catch-up, so `legacy` first takes a backup of `finsim.db` into `<home>/backups/legacy-<ts>.db` and prints it | 0 |

The Startup VBS (`FinSim Server.vbs`, ASCII only), modelled on `finsim2/install/ShafferDeskTerminal-Server.vbs`:

```vbscript
' FinSim Live Terminal - starts the local paper-trading server at login, only if nothing answers on 127.0.0.1:8765.
Option Explicit
Dim sh, fso, repo, home, py, http, up
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
repo = "<PACKAGE_ROOT written at install>"                     ' C:\Users\logan\intelligence-terminal\finsim
home = sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\.finsim"
py = "<pythonw.exe of the interpreter found by `py -3.12`, written at install>"
If Not fso.FileExists(py) Then WScript.Quit 1
up = False
On Error Resume Next
Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
http.setTimeouts 1000, 1000, 2000, 2000
http.Open "GET", "http://127.0.0.1:8765/api/health", False
http.Send
If Err.Number = 0 Then
  If http.Status = 200 Then up = True
End If
On Error GoTo 0
If up Then WScript.Quit 0
sh.Environment("Process")("PYTHONPATH") = repo
sh.Environment("Process")("FINSIM_HOME") = home
sh.CurrentDirectory = repo
sh.Run """" & py & """ -m finsim serve --port 8765", 0, False
```

The single-instance lock and exclusive bind make the VBS's health probe a courtesy, not the guard: even two VBS
launches at login end with exactly one server. `install` uses `py -3.12 -c "import sys;print(sys.executable)"` to
locate Python 3.12 (the app runs on 3.12; tests run on both).

Final PowerShell sequence for the owner (printed by `install`):

```powershell
cd C:\Users\logan\intelligence-terminal\finsim
py -3.12 -m finsim stop            # stops the OLD 0.11.0 server if it is up (same /api/shutdown route)
py -3.12 -m finsim install --no-open
py -3.12 -m finsim status          # must show version 1.0.0, integrity ok
py -3.12 -m finsim open
```

---

## 14. Execution disclosure (`docs/TERMINAL_EXECUTION.md`, written by WP2)

One page, in the owner's words, repeated in the ticket's "assumptions" line: fills at the last observed trade (Yahoo,
up to 15 min delayed) or the Cboe NBBO for options (15 min delayed); 10 % of the latest completed minute's volume per
pass; no fills on stale or computed quotes or outside the session; limit fills at the observed price on the first look
and at the limit afterwards; stops trigger on the observed last and fill at that print; futures settle at the observed
close; options exercise/assign at the observed close with the OCC 0.01 threshold; accruals ACT/360 (GBP ACT/365,
bonds ACT/ACT) on calendar days; fees as in section 8.6; Treasury sweeps as in section 8.8; borrow availability is a
heuristic; computed marks (bonds, forwards, unquoted options) are models with their inputs shown and are never
executable.

---

## 15. Test plan (`finsim/tests/test_terminal_*.py`, unittest, no network, temp SQLite, fake clock and fake quotes)

Fixtures (`tests/terminal_helpers.py`): `make_runtime(tmpdir, at="2026-10-20T14:00:00-04:00")` -> a `Runtime` with
`FakeClock`, `FakeProviders` (quotes set by the test, e.g. `fq.set("AAPL", last="339.50", quote_time=now-120,
bar=(t, v=61900))`), the real `SessionTable` with the repo's holiday file, and the real `CommandBus`. Every expected
number below is worked by hand; the tests assert ledger balances per GL, the read models, and `integrity('full').ok`.

### 15.1 Scenario A - initial capital and a deposit (`test_terminal_treasury.py`)

Start: `INIT_BOOK` posts `Dr 1010:USD TRS 10,000,000.00 / Cr 3000 10,000,000.00` (idem `init:capital:v1`).
`DEPOSIT_CAPITAL 250,000.00` (request `r1`): `Dr 1010 / Cr 3000 250,000.00`.
Expected: Treasury settled USD **10,250,000.00**; `balance(3000)` 10,250,000.00; NAV(ALL) 10,250,000.00; P&L ITD
0.00 (NAV - cumulative flows 10,250,000). Replaying `r1` returns `X-Replayed: 1`, cash unchanged. Restarting the
runtime (reopen the file) -> same balances. `integrity` ok (I7: 10,250,000 = 10,250,000 - 0 + 0 - 0).

### 15.2 Scenario B - Treasury transfer and sweep-in (`test_terminal_sweeps.py`)

Transfer 1,000,000.00 TRS -> USX: TRS `Dr 3100 / Cr 1010 1,000,000`; USX `Dr 1010 / Cr 3100 1,000,000`.
Expected: TRS settled 9,000,000.00; USX settled 1,000,000.00; NAV(ALL) 10,000,000.00 (3100 nets).
Market BUY 5,000 AAPL, quote last 339.50 (delayed, bar volume 61,900 -> cap 6,190 >= 5,000: full fill):
gross `5,000 x 339.50 = 1,697,500.00`; fee `max(1.00, 5,000 x 0.005) = 25.00`; need 1,697,525.00;
available USX = 1,000,000.00; shortfall = **697,525.00** -> SWEEP_IN entry (`sweep:<fill>`): TRS `Dr 3100 / Cr 1010
697,525.00`, USX `Dr 1010 / Cr 3100 697,525.00`; fill entry: `Dr 1100 1,697,500.00 / Dr 5000 25.00 / Cr 2100
1,697,525.00`; settlement row -1,697,525.00 due 2026-10-21.
Expected after fill: TRS settled **8,302,475.00** (= 10,000,000 - 1,000,000 - 697,525); USX settled 1,697,525.00,
payable 1,697,525.00, available 0.00; position AAPL 5,000 @ 339.50 cost 1,697,500.00; NAV(USX) at mark 339.50 =
1,697,525 - 1,697,525 + 1,697,500 = **1,697,500.00**; NAV(ALL) = 8,302,475 + 1,697,500 = **9,999,975.00** =
10,000,000 - 25.00 fee. Transfers table has the SWEEP_IN linked to the fill (I11). Sweep-out that evening: excess =
1,697,525 - 1,697,525 - 10,000 < 0 -> no transfer.

### 15.3 Scenario C - equity buy with fee and T+1 settlement (`test_terminal_equities.py`)

USX funded 2,000,000.00. Tue 2026-10-20 14:00 ET: BUY 1,000 MSFT, last 500.00. gross 500,000.00; fee `1,000 x 0.005 =
5.00` (>= 1.00); net 500,005.00. Entry: `Dr 1100 500,000.00 / Dr 5000 5.00 / Cr 2100 500,005.00`; settlement due
**2026-10-21** (T+1, Wednesday). Expected at 14:01: settled 2,000,000.00, payable 500,005.00, pending -500,005.00,
available **1,499,995.00**; NAV(USX) = 2,000,000 - 500,005 + 500,000 = **1,999,995.00** (mark 500.00).
Advance the fake clock to Wed 09:05 ET, `lifecycle.tick()`: SETTLE_DUE posts `Dr 2100 / Cr 1010 500,005.00`
(idem `settle:<id>`): settled **1,499,995.00**, payable 0, NAV unchanged 1,999,995.00. A second tick posts nothing
(jobs_done + idem_key). Mark 510.00 -> unrealised **+10,000.00**, NAV 2,009,995.00, P&L TODAY needs a baseline: with
the EOD snapshot of Tue at NAV 1,999,995.00 -> P&L TODAY **+10,000.00**, explain unrealised 10,000.00.

### 15.4 Scenario D - sell with realised P&L, FIFO (`test_terminal_equities.py`)

Lots: 1,000 MSFT @ 500.00 (10-20), then BUY 500 @ 510.00 (10-21; fee 2.50; cost 255,000.00). SELL 1,200 @ 520.00 on
10-22: gross `1,200 x 520 = 624,000.00`; fee `1,200 x 0.005 = 6.00`; FIFO relief: 1,000 @ 500 = 500,000.00 and
200 @ 510 = 102,000.00 -> cost relieved **602,000.00**; realised = 624,000 - 602,000 = **+22,000.00**.
Entry: `Dr 1200 623,994.00 / Dr 5000 6.00 / Cr 1100 602,000.00 / Cr 4000 22,000.00` (debits 624,000.00 =
credits 624,000.00). Remaining lot: 300 @ 510.00, cost **153,000.00**; `v_positions` qty 300. `lot_relief` rows:
(lot1, 1,000, 500,000, 520,000, +20,000) and (lot2, 200, 102,000, 104,000, +2,000). Position realised ITD = 22,000.00;
fees ITD = 5.00 + 2.50 + 6.00 = 13.50. Integrity Q3/Q4 ok.

### 15.5 Scenario E - short sale: borrow, 102 % collateral, fee/rebate across a weekend (`test_terminal_shorts.py`)

USX funded 2,000,000.00; FakeProviders: SOFR 3.87 (FRED 2026-10-19), FINRA ratio 0.40, ADV 5 M -> tier GC 0.30 %;
rebate = 3.87 - 0.50 = **3.37 %**. Fri 2026-10-23 10:00 ET: SELL 2,000 TSLA with `borrow: true`, last 400.00.
Borrow: collateral `1.02 x 2,000 x 400.00 = 816,000.00` -> `Dr 1400 816,000.00 / Cr 1010 816,000.00` (no sweep: available
2,000,000). Short sale: gross 800,000.00; fee 10.00; `Dr 1200 799,990.00 / Dr 5000 10.00 / Cr 2600 800,000.00`.
Expected Fri: settled 1,184,000.00 (2,000,000 - 816,000); receivable 799,990.00; restricted 800,000.00; available =
1,184,000 + 799,990 - 800,000 = **1,183,990.00**; position -2,000 TSLA; unrealised 0.00 at 400.00; NAV(USX) =
1,184,000 + 799,990 + 816,000 (1400) - 800,000 (2600 at mark 400) = **1,999,990.00** = 2,000,000 - 10 fee.
Mon 2026-10-26 00:35 ET `tick()`: ACCRUE_DAILY for D = 10-23, 10-24, 10-25 (weekend, mark carried at 400.00):
fee per day = `800,000 x 0.0030 / 360 = 6.6667 -> 6.67`; rebate per day = `816,000 x 0.0337 / 360 = 76.3867 -> 76.39`.
Three entries each (`accrual:BORROW_FEE:<b>:2026-10-23` ...): 2310 = **20.01**, 5300 = 20.01, 1410 = **229.17**,
4350 = 229.17. Settlement Mon 09:05: `Dr 1010 799,990.00 / Cr 1200`. Mon mark 410.00, COLLATERAL_MARK 17:10:
required `1.02 x 2,000 x 410 = 836,400.00`; call **20,400.00** -> `Dr 1400 / Cr 1010 20,400.00`; `borrows.collateral_c`
836,400.00; unrealised = 800,000 - 820,000 = **-20,000.00**. With mark 390.00 instead: required 795,600.00, return
20,400.00 (`Dr 1010 / Cr 1400`). Cover BUY 2,000 @ 410.00 on Tue: `Dr 2600 800,000.00 / Dr 5000 10.00 / Dr 4000
20,000.00 / Cr 2100 820,010.00`; realised **-20,000.00**; the borrow returns in the same transaction:
`Dr 1010 836,400.00 / Cr 1400`, fees settle `Dr 2310 20.01 + (Tue accrual) / Cr 1010`, rebate `Dr 1010 / Cr 1410`.
I8 holds before the cover (borrow 2,000 >= short 2,000) and I10 at each step. A SELL without `borrow` -> 409
`NO_BORROW`; with FINRA ratio 0.95 and ADV 50k -> tier T3, availability 5k -> fee 25 % in the preview.

### 15.6 Scenario F - currency loan and FX spot T+2 (`test_terminal_fx.py`)

CRED account. FakeProviders: ECBDFR 2.00 -> loan rate 2.75 %; EURUSD mid 1.1238. Tue 2026-10-20 11:00 ET:
`OPEN_LOAN EUR 1,000,000.00`: `Dr 1010:EUR 1,000,000.00 / Cr 2750:EUR 1,000,000.00` (base 1,123,800.00 at 1.1238).
Same minute `FX_SPOT sell EUR 1,000,000 buy USD`: rate = `1.1238 - 0.00002 = 1.12378`; USD = **1,123,780.00**;
spread cost shown `1,000,000 x 0.00002 = 20.00`. Entry: `Dr 1250:USD 1,123,780.00 / Cr 2350:EUR 1,000,000.00`
(base 1,123,780 both sides). Settlements: +1,123,780.00 USD and -1,000,000.00 EUR due **2026-10-22** (T+2).
Expected Tue: EUR settled 1,000,000.00, EUR pending -1,000,000.00, EUR available 0.00; USD settled 0.00, USD pending
+1,123,780.00, USD available 1,123,780.00; loan liability EUR 1,000,000.00.
Daily accrual D = 10-20 (posted Wed 00:35): `1,000,000 x 0.0275 / 360 = 76.3889 -> EUR 76.39` `Dr 5150 / Cr 2755:EUR`.
Thu 09:05 settlement: `Dr 1010:USD 1,123,780.00 / Cr 1250:USD`; `Dr 2350:EUR 1,000,000.00 / Cr 1010:EUR 1,000,000.00`
(EUR cash relieved at its average rate 1.1238 -> base 1,123,800 vs payable base 1,123,780 -> **4650 realised FX
-20.00**, the spread cost). Expected Thu: USD settled 1,123,780.00; EUR settled 0.00; accrued 2755 = 2 days = 152.78
(10-20, 10-21). After 10 accrual days 2755 = **763.90**. `REPAY_LOAN` with EUR cash 0 -> 409 `INSUFFICIENT_FUNDS_CCY`
("buy EUR 1,000,763.90 with Convert"). Buy EUR spot at mid 1.1300 (rate 1.13002) then repay -> `Dr 2750 1,000,000 /
Dr 2755 763.90 / Cr 1010:EUR 1,000,763.90`; the EUR cash bought at 1.13002 relieves at that rate, 4650 0.00 on this
leg; the loan's base retranslation effect appears in NAV as the difference 1,123,800 - 1,130,020 = -6,220.00 (FX loss
on the liability shown in the overlay before repayment, realised through the USD paid at repayment).

### 15.7 Scenario G - option expiry: ITM exercise and assignment (`test_terminal_options.py`)

USX. Long 10 AAPL 2026-11-20 C 330 bought 15.00 (premium 15,000.00, fee 6.50, settled T+1); short 10 AAPL
2026-11-20 P 350 sold 18.00 (premium 18,000.00, fee 6.50). Before expiry ledger: 1700 15,000.00; 2900 18,000.00;
5000 13.00. Fri 2026-11-20 16:25 ET, AAPL observed close **341.00** (record quality `close`, quote_time 16:00 ET):
Call intrinsic 11.00 >= 0.01 -> EXERCISE fill BUY 1,000 AAPL @ 330: `Dr 1100 345,000.00 / Cr 1700 15,000.00 /
Cr 2100 330,000.00` (basis = 330,000 + 15,000). Put intrinsic 9.00 -> ASSIGNMENT fill BUY 1,000 AAPL @ 350:
`Dr 1100 332,000.00 / Dr 2900 18,000.00 / Cr 2100 350,000.00` (basis = 350,000 - 18,000). Settlement of both
payables **2026-11-23** (Mon, T+1), sweep-in if USX cannot fund 680,000.00. Expected: position 2,000 AAPL, cost
**677,000.00**, avg 338.50; at mark 341.00 MV 682,000.00, unrealised **+5,000.00**; 1700 and 2900 zero; realised 4000
on the options 0.00 (A7); option instruments EXPIRED; `jobs_done` has both keys; a second run posts nothing.
OTM control: long 10 AAPL C 360 cost 5,000.00 -> EXPIRY fill at 0: `Dr 4000 5,000.00 / Cr 1700 5,000.00`, realised
-5,000.00. Missing close (record `unavailable`) -> no fill, attention WARN `EXPIRY_PENDING_CLOSE`, retry next tick.

### 15.8 Scenario H - futures variation margin, two days (`test_terminal_futures.py`)

USX funded 100,000.00. Tue 2026-10-20 10:00 ET: BUY 2 ESZ26 @ 7,750.00 (multiplier 50, IM 17,500/contract).
Fee `2 x 2.25 = 4.50`: `Dr 5000 4.50 / Cr 1010 4.50`; IM `Dr 1300 35,000.00 / Cr 1010 35,000.00`; no payable.
Expected: settled **64,995.50**; position 2 ESZ26; `futures_settle` seed (settle_px 7,750.00 for the fill term).
Tue 16:05 ET FUTURES_VM (observed close 7,762.25): `VM = 0 x (...) + 2 x (7,762.25 - 7,750.00) x 50 = 2 x 12.25 x 50 =
1,225.00` -> `Dr 1010 1,225.00 / Cr 4400 1,225.00`; settled 66,220.50; `futures_settle(2026-10-20)` = 7,762.25.
Wed 16:05 (close 7,740.50): `VM = 2 x (7,740.50 - 7,762.25) x 50 = -2,175.00` -> `Dr 4400 2,175.00 / Cr 1010
2,175.00`; settled **64,045.50**; 4400 cumulative **-950.00** = `2 x (7,740.50 - 7,750.00) x 50`; MV 0; unrealised
intraday at mark 7,745.00 after Wed settle = `2 x (7,745.00 - 7,740.50) x 50 = 450.00`. Re-running either key posts
nothing (PK on futures_settle + idem). NAV(USX) Wed close = 64,045.50 + 35,000.00 (1300) = 99,045.50 = 100,000 - 4.50
- 950.00. SELL 2 @ 7,760.00 Thu 10:00 then Thu close 7,755.00: `VM = 2 x (7,755 - 7,740.50) x 50 + (-2) x (7,755 -
7,760) x 50 = 1,450.00 + 500.00 = 1,950.00`; IM released `Dr 1010 / Cr 1300 35,000.00`; fee 4.50.

### 15.9 Application and integrity tests

| Test | File | Assertion |
|---|---|---|
| Restart persistence | `test_terminal_persistence.py` | run A-D, close the Database, reopen a new Runtime on the same file: identical positions, cash, orders, fills, NAV, `state_version`; `integrity('full').ok` |
| Rollback on a failing write | `test_terminal_bus.py` | a handler that posts one entry then raises `RuntimeError`: no `journal_entries`, no `requests` row, 500 returned, attention row present; a handler raising `CommandError` after posting: rollback + FAILED request row; same request_id replays the same 409 |
| Idempotent replay | `test_terminal_bus.py` | two `SUBMIT_ORDER` with one request_id -> one order; `FILL_ORDER` twice with the same idem key -> one fill (second returns stored result) |
| Duplicate server prevention | `test_terminal_server.py` | start `Runtime.serve` on a temp home in a thread; a second `serve` on the same home returns exit 3 before touching the DB (lock), and a bind on the same port with `allow_reuse_address=False` raises `OSError` (Windows 10048) |
| Stale quote blocking | `test_terminal_orders.py` | session open (Tue 14:00 ET), AAPL record `quote_time = now - 1,500 s` (> 900 + 300) -> MARKET submit 409 `QUOTE_STALE`; a LIMIT submits and rests; a fresh record arrives -> matcher fills it; a `computed` mark never fills |
| Session blocking | `test_terminal_sessions.py` | Sat 2026-10-24 12:00 ET: MARKET BUY accepted WORKING `AWAITING_SESSION`, `expires_ts` = Mon 16:00 ET; `tick()` at Mon 09:31 with a fresh quote -> fill; 2026-11-27 (early close) DAY order expires 13:00; CME group open at Sun 19:00 ET, closed 17:30 Mon; Good Friday 2026-04-03 closed for US_EQUITY and CME; `settle_date(US_EQUITY, 2026-11-25)` = 2026-11-27 (Thanksgiving skipped) |
| Partial fills | `test_terminal_orders.py` | BUY 20,000 with bar volume 61,900: first pass 6,190; same bar again -> no fill; new bar v 50,000 -> 5,000; status PARTIALLY_FILLED; avg price correct; cancel the rest -> CANCELLED with filled 11,190 |
| Funding refusal | `test_terminal_sweeps.py` | Treasury 100,000, USX 0, BUY 1,000 AAPL at 339.50 -> preview blocker `INSUFFICIENT_FUNDS` (need 339,505 > 100,000); submit 409; nothing posted |
| Ledger/NAV reconciliation | `test_terminal_integrity.py` | after A-H in one book: every I1-I14 passes; `balance_sheet(ALL).assets - liabilities == nav(ALL)`; trial balance debits == credits per currency and in base; NAV(ALL) == 10,000,000 + deposits - fees - realised/unrealised changes as summed from the scenarios |
| Freshness rules | `test_terminal_quotes.py` | table-driven: (quote_time age, fetch age, session open, error) -> expected quality; `is_executable` reasons |
| Bond mark | `test_terminal_marks.py` | par curve {2Y 4.00 %, 10Y 5.00 %}, a 4.500 % 2036-08-15 benchmark exactly 10 years out priced on a coupon date at y = 5.00 %: annuity factor `(1 - 1.025^-20) / 0.025 = (1 - 0.610271) / 0.025 = 15.58917`; coupons `2.25 x 15.58917 = 35.0756`; principal `100 x 0.610271 = 61.0271`; price **96.1027 -> 96.10** per 100 - the test pins the exact Decimal from the formula with the arithmetic in comments; intraday ^TNX +0.10 -> y 5.10 % -> price moves by about -0.75 (modified duration about 7.8); accrued after 45 days of a 184-day period = `2.25 x 45/184 = 0.5503` per 100 |
| FX forward CIP | `test_terminal_marks.py` | S 1.1238, SOFR 3.87, ECBDFR 2.00, 90 days: `F = 1.1238 x (1 + 0.0387 x 90/360) / (1 + 0.0200 x 90/360) = 1.1238 x 1.009675 / 1.005 = 1.134673 / 1.005 = 1.129028`, labelled computed with both as-of dates |
| API round trip | `test_terminal_api.py` | `Router.handle` directly (no sockets): health shape, preview -> submit -> order -> fills -> transactions; error bodies; `X-Replayed`; 400 without request_id |
| Launcher files | `test_terminal_app.py` | `launcher_files()` is pure: the VBS is ASCII, names `FinSim Server.vbs`, contains the health probe and `-m finsim serve --port 8765`; `install` plan lists the old files to remove |
| Compatibility | existing suites | `py -3.12 -m unittest` for `tests/test_terminal_*`; `py -3.14 -m pytest tests/test_terminal_*`; then the old FinSim subset and the `test_fs2_*`/`test_desk_*` suites against the baseline captured before the change (differences reported, not hidden) |

Browser check (manual, scripted in `docs/TERMINAL_CHECKLIST.md`): open the app, see the Treasury at 10,000,000, open
US Exchange, enter AAPL, see the preview with freshness, submit a 1-share market order during the session, see the fill
in Positions and Transactions, refresh the page and restart the server (`stop`/`open`), see the position still there,
console and network tabs free of errors.

---

## 16. Build order - work packages

Dependency rule: WP0 first (serial, one agent); WP1-WP4 in parallel against the WP0 interfaces (each with fakes for
the others); WP5-WP6 against this document's API/CLI contracts; WP7 integrates. Each package owns its files and must
not edit another package's files - interface gaps are raised as a note in `docs/design_proposals/BUILD_NOTES.md`.

| WP | Owns | Consumes | Acceptance |
|---|---|---|---|
| **WP0 core** | `__init__.py`, `config.py`, `units.py`, `clock.py`, `db.py`, `sessions.py`, `instruments.py`, `ledger.py`, `accounting.py`, `commands.py` (+ INIT_BOOK, DEPOSIT/WITHDRAW/TRANSFER/OPEN_ACCOUNT/UPDATE_ACCOUNT handlers), `tests/terminal_helpers.py`, `test_terminal_treasury.py`, `test_terminal_bus.py`, `test_terminal_sessions.py`, `test_terminal_integrity.py` (ledger-level parts) | `finsim.money`, `finsim.calendar`, `finsim.tzfallback`, `data/nyse-holidays.json`, `finsim/data/universe.json` | schema creates on 3.12 and 3.14; INIT_BOOK idempotent; scenario A; transfer part of B; rollback/replay tests; sessions table tests; `integrity('full')` on an empty and a seeded book; `InstrumentMaster.seed()` loads >= 800 equities, 25 FX pairs, 20 futures codes x 4 months, benchmarks; `accounting` read models return the documented shapes with Decimal strings |
| **WP1 quotes** | `quotes/*`, `marks.py`, `test_terminal_quotes.py`, `test_terminal_marks.py` | WP0 (`Instrument`, `SessionTable`, `Database`, `Clock`), `finsim.engines.options_pricing`, `finsim2.data` plumbing (copied, not imported, to keep the terminal free of `reqctx`) | providers parse the probe's real payload shapes from fixtures (`tests/fixtures/terminal/yahoo_spy_1m.json`, `cboe_aapl.json` trimmed, `treasury_curve.xml`, `fred_sofr.csv`); freshness table test; worker scheduling test with a fake provider and a fake clock (intervals, pins, circuit breaker, no network in tests); bond/CIP/option-model tests; `cache.persist/load_persisted` round trip; a `--smoke` script (manual, network) that prints 10 quotes with provenance and verifies `ESZ26.CME` (**Verify**) |
| **WP2 orders and execution** | `orders.py`, `execution.py`, `lending.py`, the matcher (`orders.Matcher`), `docs/TERMINAL_EXECUTION.md`, `test_terminal_orders.py`, `test_terminal_equities.py`, `test_terminal_shorts.py`, `test_terminal_sweeps.py`, `test_terminal_options.py` (fill parts) | WP0; `QuoteRecord`/`QuoteCache`/`MarkProvider` interfaces (fakes in tests); `SessionTable` | scenarios B (fill part), C, D, E (sale, borrow, cover), partial fills, stale/session blocking, funding refusal, preview shape frozen as JSON fixture; matcher idempotency with the same observation; `evaluate()` is pure and table-tested for every (type, side, marketability) cell |
| **WP3 lifecycle, credit, fx** | `lifecycle.py`, `credit.py`, `fx.py`, `test_terminal_lifecycle.py`, `test_terminal_fx.py`, `test_terminal_futures.py`, `test_terminal_options.py` (expiry parts), `test_terminal_persistence.py` | WP0; `MarkProvider` fake; `execution.apply_fill` for EXERCISE/ASSIGNMENT/EXPIRY/FUTURES_FINAL fills (WP2 interface: `apply_system_fill(tx, account, inst, side, qty, price, origin, quote, idem)`); `RateStore` | scenarios E (accruals, collateral marks), F, G, H; settlement T+1/T+2 with the holiday file; catch-up after a 7-day clock jump posts exactly 7 accruals per object; EOD snapshot and P&L TODAY/MTD; sweep-out example; every job replays to a no-op |
| **WP4 api and server** | `api.py`, `server.py`, `test_terminal_api.py`, `test_terminal_server.py` | WP0-WP3 public classes (built against stubs where needed) | every route in section 10 answers with the documented shape (fixture-compared); 400/404/409/500/503 mapping; health lock-free (a test holds the writer lock for 2 s while GET /api/health answers in < 50 ms); exclusive bind and lock-file tests; graceful shutdown closes threads within 3 s |
| **WP5 UI** | `static/*` | section 10 contract (a recorded fixture server `tests/terminal_fixture_server.py` serving canned JSON) | pages render from fixtures; ticket preview debounce; freshness chips per quality; attention strip; request-id and 503 retry logic; no console errors; works at 1100 px and 1440 px widths |
| **WP6 launcher** | `terminal/app.py`, `finsim/__main__.py` (re-point + `legacy`), `test_terminal_app.py` | `server.serve`, `finsim.app` helpers copied where useful (`_browser_candidates`, `_known_desktop`, `_ico_from_png`) | `launcher_files()` pure test; `install --no-open` on this machine replaces `Startup\FinSim Server.vbs`, removes the scheduled task and old VBS files, starts 1.0.0 on 8765 (the old 0.11.0 stopped first via `/api/shutdown`); `status` prints the served version; `stop` leaves no python process holding `terminal.lock`; `legacy serve` opens the old UI on 8766 after a backup |
| **WP7 integration** | `docs/TERMINAL_CHECKLIST.md`, `docs/TERMINAL_RUNBOOK.md`, fixes across packages (with the owner of the file) | everything | full test run on 3.12 and 3.14; compatibility suites vs baseline; browser checklist; `finsim.db` sha256 before == after; reconciliation report (section 15.9) attached to the final report |

Thin-first rule: WP2 and WP3 implement equities, options, futures, FX spot, currency loans and borrows completely
before touching bonds, forwards, repo and PB financing; those four are "limited" in the UI until their tests pass.

---

## 17. Open questions (defaults chosen so the build does not wait)

1. **Q1 Yahoo per-contract futures symbols** (`ESZ26.CME`, `ZNZ26.CBT`, `6EZ26.CME`): the probe verified only `CLX26.NYM`.
   Default: WP1's smoke test confirms; any code that fails resolves to "front-month continuous only" with the
   instrument marked LIMITED (no deferred months).
2. **Q2 Cboe User-Agent**: the Desk sends a browser UA to cdn.cboe.com; the owner rule allows a browser UA only for
   Yahoo. Default: honest `LoganTerminal/1.0` UA; if Cboe refuses it (403), options become `computed`-only and the
   owner decides (never a bypass).
3. **Q3 Dividend pay dates**: not in the free feed. Default: receivable on ex-date, cash T+14 calendar days, disclosed.
4. **Q4 Recalls / buy-ins**: notice only in this build (no forced buy-in without observable lender data). Owner may
   want a manual "simulate recall" action; not built.
5. **Q5 Option margin**: reserved requirement (reduces available cash) rather than cash moved to 1300. Alternative:
   post it to 1300 like futures IM; one-line change in `execution.apply_fill` + sweep logic.
6. **Q6 CME holiday calendar**: approximated from the NYSE file (early close 13:00, full closures Good Friday /
   Christmas / New Year). Owner may supply the CME list as `settings.session_overrides`.
7. **Q7 Foreign listings and bonds**: listings are quoted and tradeable (T+2) in Foreign Markets & FX; foreign bonds and
   FX options are UNSUPPORTED in this build (listed as such).
8. **Q8 Legacy mode read-only**: the old server cannot be made read-only without editing old code (not allowed);
   `legacy` takes a backup first and prints the warning. If the owner wants hard read-only, the old DB can be copied to
   `<home>/legacy-readonly.db` and served from there (one flag).
9. **Q9 Yields data for GBP/JPY loans**: FRED OECD monthly series are proxies (as-of month shown). Accept or restrict
   currency loans to USD/EUR/GBP.
10. **Q10 Day-count for Treasury cash interest on non-USD Treasury balances**: not paid (0 %) in this build, disclosed.
