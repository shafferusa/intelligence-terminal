-- The schema of FinSim Terminal 1.0.3.1 (FINSIM1_PLAN P1 task 7; the chart-sync tests of P1, P4 and P7 read it).
-- Source: a read-only sqlite3 backup of the owner's terminal.db taken 2026-10-27 14:54 EDT (state_version 3208), last opened by
-- release 1.0.3.1: the CREATE statements of sqlite_master as stored (SQLite drops IF NOT EXISTS), then meta (schema_version 1,
-- app_version_last_opened 1.0.3.1), the 52 chart rows and the 3 books.  No owner rows: no orders, fills, journal, positions,
-- transfers or owner settings.  SQL text, because finsim/.gitignore ignores *.db: tests build the database with executescript.
PRAGMA foreign_keys=OFF;
BEGIN;
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE commands (
  id            INTEGER PRIMARY KEY,
  request_id    TEXT NOT NULL UNIQUE,
  kind          TEXT NOT NULL, actor TEXT NOT NULL,
  payload_json  TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
  result_json   TEXT NOT NULL, http_status INTEGER NOT NULL DEFAULT 200,
  started_ts    INTEGER NOT NULL, finished_ts INTEGER NOT NULL, duration_ms INTEGER NOT NULL,
  app_version   TEXT NOT NULL, state_version INTEGER NOT NULL
);
CREATE TABLE events (
  id INTEGER PRIMARY KEY, command_id INTEGER NOT NULL REFERENCES commands(id),
  ts INTEGER NOT NULL, type TEXT NOT NULL,
  account_id TEXT, instrument_id TEXT, ref_type TEXT, ref_id TEXT, payload_json TEXT NOT NULL
);
CREATE INDEX ix_events_acct ON events(account_id, id);
CREATE INDEX ix_events_type ON events(type, id);
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
CREATE TABLE sequences (name TEXT PRIMARY KEY, next INTEGER NOT NULL);
CREATE TABLE books (book_code TEXT PRIMARY KEY, name TEXT NOT NULL, profile TEXT NOT NULL CHECK (profile IN ('INDIVIDUAL','FUND','INSTITUTION')),
  starting_capital_c INTEGER NOT NULL, sort_order INTEGER NOT NULL, created_ts INTEGER NOT NULL);
CREATE TABLE lifetimes (
  lifetime_id TEXT PRIMARY KEY, book_code TEXT NOT NULL REFERENCES books(book_code), seq INTEGER NOT NULL, name TEXT NOT NULL,
  started_ts INTEGER NOT NULL, ended_ts INTEGER, starting_capital_c INTEGER NOT NULL, status TEXT NOT NULL CHECK (status IN ('OPEN','CLOSED')),
  final_nav_c INTEGER, final_realised_c INTEGER, final_pnl_c INTEGER, final_quality TEXT, reason TEXT, integrity_json TEXT,
  UNIQUE (book_code, seq)
);
CREATE TABLE agreements (id INTEGER PRIMARY KEY, lifetime_id TEXT NOT NULL REFERENCES lifetimes(lifetime_id), broker TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('ISDA_MASTER','CSA','PBA')), signed_date TEXT NOT NULL, command_id INTEGER NOT NULL, note TEXT,
  UNIQUE (lifetime_id, broker, kind));
CREATE TABLE accounts (
  account_id TEXT PRIMARY KEY,
  book_code TEXT NOT NULL REFERENCES books(book_code), lifetime_id TEXT NOT NULL REFERENCES lifetimes(lifetime_id),
  role TEXT NOT NULL,
  name TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('TREASURY','TRADING','PB')),
  broker TEXT, fee_schedule TEXT NOT NULL DEFAULT 'DEFAULT',
  status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED')),
  sweep_out INTEGER NOT NULL DEFAULT 1, sweep_buffer_c INTEGER NOT NULL DEFAULT 1000000,
  pb_financing INTEGER NOT NULL DEFAULT 0, pb_limit_c INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 100, opened_ts INTEGER NOT NULL, closed_ts INTEGER, settings_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX ix_accounts_lifetime ON accounts(lifetime_id, sort_order);
CREATE TABLE pb_schedules (account_id TEXT PRIMARY KEY REFERENCES accounts(account_id), schedule_json TEXT NOT NULL, updated_ts INTEGER NOT NULL);
CREATE TABLE chart (gl TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('ASSET','LIABILITY','EQUITY','INCOME','EXPENSE')),
  per_instrument INTEGER NOT NULL DEFAULT 0, bucket TEXT NOT NULL);
CREATE TABLE instruments (
  instrument_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('EQUITY','ETF','ADR','REIT','PREFERRED','OPTION','FUTURE','BOND','FX','INDEX','YIELD','PRIVATE','SWAP')),
  symbol TEXT NOT NULL, name TEXT NOT NULL, currency TEXT NOT NULL,
  multiplier TEXT NOT NULL DEFAULT '1', tick TEXT NOT NULL DEFAULT '0.01', lot INTEGER NOT NULL DEFAULT 1,
  price_scale TEXT NOT NULL DEFAULT '1',
  session_group TEXT NOT NULL, settle_days INTEGER NOT NULL DEFAULT 1,
  account_kinds TEXT NOT NULL,
  yahoo_symbol TEXT, yahoo_invert INTEGER NOT NULL DEFAULT 0, cboe_symbol TEXT, underlying_id TEXT,
  expiry TEXT, strike TEXT, right TEXT CHECK (right IN ('C','P')), style TEXT, exercise TEXT,
  spec_json TEXT NOT NULL DEFAULT '{}',
  support TEXT NOT NULL DEFAULT 'FULL' CHECK (support IN ('FULL','LIMITED','COMPUTED','UNSUPPORTED')),
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','EXPIRED','DELISTED')),
  created_ts INTEGER NOT NULL, updated_ts INTEGER NOT NULL
);
CREATE INDEX ix_instruments_sym ON instruments(symbol);
CREATE INDEX ix_instruments_under ON instruments(underlying_id, expiry);
CREATE UNIQUE INDEX ux_instruments_yahoo ON instruments(yahoo_symbol) WHERE yahoo_symbol IS NOT NULL AND kind <> 'OPTION';
CREATE TABLE exchange_sessions (exchange TEXT NOT NULL, day TEXT NOT NULL, tz TEXT NOT NULL, open_ts INTEGER NOT NULL,
  close_ts INTEGER NOT NULL, fetched_ts INTEGER NOT NULL, PRIMARY KEY (exchange, day));
CREATE TABLE journal_entries (
  entry_id INTEGER PRIMARY KEY, command_id INTEGER NOT NULL REFERENCES commands(id), event_id INTEGER,
  ts INTEGER NOT NULL, business_date TEXT NOT NULL,
  kind TEXT NOT NULL,
  memo TEXT NOT NULL, ref_type TEXT, ref_id TEXT,
  idem_key TEXT NOT NULL UNIQUE
);
CREATE TABLE journal_lines (
  line_id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL REFERENCES journal_entries(entry_id), line_no INTEGER NOT NULL,
  account_id TEXT NOT NULL REFERENCES accounts(account_id), gl TEXT NOT NULL REFERENCES chart(gl), currency TEXT NOT NULL,
  amount_c INTEGER NOT NULL CHECK (amount_c <> 0),
  base_c INTEGER NOT NULL,
  fx_rate TEXT NOT NULL, fx_source TEXT NOT NULL,
  instrument_id TEXT, lot_id INTEGER, counterparty TEXT, memo TEXT,
  UNIQUE (entry_id, line_no)
);
CREATE INDEX ix_lines_acct ON journal_lines(account_id, gl, currency);
CREATE INDEX ix_lines_inst ON journal_lines(account_id, instrument_id, gl);
CREATE INDEX ix_lines_entry ON journal_lines(entry_id);
CREATE TABLE gl_balances (
  account_id TEXT NOT NULL, gl TEXT NOT NULL, currency TEXT NOT NULL, instrument_id TEXT NOT NULL DEFAULT '',
  balance_c INTEGER NOT NULL, base_c INTEGER NOT NULL, PRIMARY KEY (account_id, gl, currency, instrument_id)
);
CREATE TABLE transfers (
  transfer_id TEXT PRIMARY KEY, command_id INTEGER NOT NULL, ts INTEGER NOT NULL, business_date TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('INITIAL_CAPITAL','DEPOSIT','WITHDRAWAL','TRANSFER','SWEEP_IN','SWEEP_OUT')),
  from_account TEXT, to_account TEXT, currency TEXT NOT NULL, amount_c INTEGER NOT NULL CHECK (amount_c > 0),
  linked_type TEXT, linked_id TEXT, shortfall_c INTEGER,
  entry_id INTEGER NOT NULL REFERENCES journal_entries(entry_id), idem_key TEXT NOT NULL UNIQUE
);
CREATE TABLE orders (
  order_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, account_id TEXT NOT NULL REFERENCES accounts(account_id),
  instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
  side TEXT NOT NULL CHECK (side IN ('BUY','SELL','SELL_SHORT','BUY_TO_COVER','BTO','STO','BTC','STC')),
  qty INTEGER NOT NULL CHECK (qty > 0), filled_qty INTEGER NOT NULL DEFAULT 0, avg_px TEXT,
  order_type TEXT NOT NULL CHECK (order_type IN ('MARKET','LIMIT','STOP','STOP_LIMIT','TRAILING_STOP')), limit_px TEXT, stop_px TEXT,
  trail_amt TEXT, trail_pct TEXT, trail_ref_px TEXT,
  fill_mode TEXT NOT NULL DEFAULT 'SEQUENTIAL' CHECK (fill_mode IN ('SEQUENTIAL','AON')),
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
CREATE TABLE order_rejections (
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, request_id TEXT NOT NULL, account_id TEXT, instrument_id TEXT,
  side TEXT, qty INTEGER, order_type TEXT, code TEXT NOT NULL, message TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE fills (
  fill_id TEXT PRIMARY KEY, order_id TEXT REFERENCES orders(order_id), account_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
  qty INTEGER NOT NULL CHECK (qty > 0), price TEXT NOT NULL, gross_c INTEGER NOT NULL, fee_c INTEGER NOT NULL DEFAULT 0,
  currency TEXT NOT NULL, fx_rate TEXT NOT NULL, ts INTEGER NOT NULL, trade_date TEXT NOT NULL, settle_date TEXT,
  origin TEXT NOT NULL CHECK (origin IN ('ORDER','EXERCISE','ASSIGNMENT','EXPIRY','FUTURES_FINAL','MATURITY','NEGOTIATED')),
  price_basis TEXT NOT NULL,
  quote_json TEXT NOT NULL,
  bar_ts INTEGER, cap_qty INTEGER, cap_note TEXT, entry_id INTEGER NOT NULL REFERENCES journal_entries(entry_id),
  idem_key TEXT NOT NULL UNIQUE
);
CREATE INDEX ix_fills_pos ON fills(account_id, instrument_id, ts);
CREATE TABLE lots (
  lot_id INTEGER PRIMARY KEY, account_id TEXT NOT NULL, instrument_id TEXT NOT NULL, side TEXT NOT NULL CHECK (side IN ('LONG','SHORT')),
  open_fill_id TEXT NOT NULL REFERENCES fills(fill_id), open_ts INTEGER NOT NULL, qty_open INTEGER NOT NULL, qty_remaining INTEGER NOT NULL CHECK (qty_remaining >= 0),
  unit_cost TEXT NOT NULL, cost_c INTEGER NOT NULL,
  currency TEXT NOT NULL, lock_until TEXT
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
  leg TEXT NOT NULL,
  currency TEXT NOT NULL, amount_c INTEGER NOT NULL,
  gl TEXT NOT NULL,
  due_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','SETTLED')),
  settled_ts INTEGER, entry_id INTEGER, UNIQUE (fill_id, leg), UNIQUE (fx_deal_id, leg)
);
CREATE INDEX ix_settle_due ON settlements(status, due_date);
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
  rate TEXT NOT NULL, opened_date TEXT NOT NULL, maturity_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN');
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
  options_detail_json TEXT NOT NULL, note TEXT);
CREATE TABLE accrual_state (
  kind TEXT NOT NULL, ref_id TEXT NOT NULL, segment_start TEXT NOT NULL, base_c INTEGER NOT NULL, rate TEXT NOT NULL,
  day_basis INTEGER NOT NULL, accrued_through TEXT, cum_c INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (kind, ref_id)
);
CREATE TABLE hedge_packages (package_id TEXT PRIMARY KEY, command_id INTEGER NOT NULL, account_id TEXT NOT NULL, primary_order_id TEXT,
  candidate_id TEXT NOT NULL, strategy_key TEXT, legs_json TEXT NOT NULL, fill_mode TEXT NOT NULL DEFAULT 'SEQUENTIAL',
  status TEXT NOT NULL, created_ts INTEGER NOT NULL);
CREATE TABLE otc_contracts (
  contract_id TEXT PRIMARY KEY,
  command_id INTEGER NOT NULL, account_id TEXT NOT NULL REFERENCES accounts(account_id), broker TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('IRS','TRS','XCCY')), terms_json TEXT NOT NULL,
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
CREATE TABLE private_companies (company_id TEXT PRIMARY KEY, name TEXT NOT NULL, sector TEXT, country TEXT,
  status TEXT NOT NULL CHECK (status IN ('PRIVATE','FILED_IPO','PUBLIC')), ticker TEXT, instrument_id TEXT NOT NULL,
  notes TEXT, updated_ts INTEGER NOT NULL);
CREATE TABLE private_rounds (id INTEGER PRIMARY KEY, company_id TEXT NOT NULL REFERENCES private_companies(company_id), round_date TEXT NOT NULL,
  round_type TEXT NOT NULL,
  post_money_c INTEGER, price_per_share TEXT, lead_investors TEXT, source_url TEXT NOT NULL, as_of TEXT NOT NULL,
  recorded_by TEXT NOT NULL CHECK (recorded_by IN ('seed','owner')), command_id INTEGER, UNIQUE (company_id, round_date, round_type));
CREATE TABLE private_loans (
  loan_id TEXT PRIMARY KEY, command_id INTEGER NOT NULL, account_id TEXT NOT NULL REFERENCES accounts(account_id), instrument_id TEXT NOT NULL,
  company_id TEXT, borrower TEXT NOT NULL, currency TEXT NOT NULL, principal_c INTEGER NOT NULL, issue_price TEXT NOT NULL,
  index_name TEXT NOT NULL, spread TEXT NOT NULL, day_basis INTEGER NOT NULL DEFAULT 360, payment_months INTEGER NOT NULL DEFAULT 3,
  start_date TEXT NOT NULL, maturity_date TEXT NOT NULL, next_payment_date TEXT NOT NULL, oas_issue TEXT,
  status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','REPAID')), closed_ts INTEGER
);
CREATE TABLE dividends (instrument_id TEXT NOT NULL, ex_date TEXT NOT NULL, amount TEXT NOT NULL, pay_date TEXT, source TEXT NOT NULL,
  fetched_ts INTEGER NOT NULL, PRIMARY KEY (instrument_id, ex_date));
CREATE TABLE news_items (id INTEGER PRIMARY KEY, source TEXT NOT NULL CHECK (source IN ('yahoo','fed','newspaper')), title TEXT NOT NULL,
  publisher TEXT NOT NULL, link TEXT NOT NULL, time_ts INTEGER NOT NULL, tickers_json TEXT NOT NULL DEFAULT '[]', category TEXT,
  fetched_ts INTEGER NOT NULL, UNIQUE (source, link));
CREATE INDEX ix_news_time ON news_items(time_ts);
CREATE TABLE marks (
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
  detail_json TEXT NOT NULL, PRIMARY KEY (account_id, snap_date)
);
CREATE TABLE quote_cache (instrument_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, updated_ts INTEGER NOT NULL);
CREATE TABLE curve_points (series TEXT NOT NULL, as_of TEXT NOT NULL, tenor TEXT NOT NULL, value TEXT NOT NULL, fetched_ts INTEGER NOT NULL,
  source TEXT NOT NULL, PRIMARY KEY (series, as_of, tenor));
CREATE TABLE finra_short (symbol TEXT NOT NULL, day TEXT NOT NULL, short_volume INTEGER NOT NULL, total_volume INTEGER NOT NULL, PRIMARY KEY (symbol, day));
CREATE TABLE watchlists (id INTEGER PRIMARY KEY, account_id TEXT NOT NULL, name TEXT NOT NULL, instrument_id TEXT NOT NULL,
  position INTEGER NOT NULL, UNIQUE (account_id, name, instrument_id));
INSERT INTO meta(key, value) VALUES ('schema_version', '1');
INSERT INTO meta(key, value) VALUES ('app_version_last_opened', '1.0.3.1');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('1010', 'Cash — settled', 'ASSET', 0, 'cash_settled');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('1100', 'Investments — equities/ETFs at cost', 'ASSET', 1, 'positions_cost');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('1110', 'Investments — bonds at cost', 'ASSET', 1, 'positions_cost');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('1120', 'Investments — private company shares at cost', 'ASSET', 1, 'positions_cost');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('1130', 'Private credit loans at amortised cost', 'ASSET', 1, 'positions_cost');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('1200', 'Receivable — unsettled sales', 'ASSET', 0, 'cash_pending_in');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('1210', 'Dividends receivable', 'ASSET', 1, 'accrued_income');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('1220', 'Accrued interest receivable — bonds and private credit', 'ASSET', 1, 'accrued_income');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('1225', 'Accrued swap receivable', 'ASSET', 1, 'accrued_income');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('1230', 'Accrued interest receivable — cash', 'ASSET', 0, 'accrued_income');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('1250', 'FX settlement receivable', 'ASSET', 0, 'cash_pending_in');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('1300', 'Margin deposit — futures initial margin', 'ASSET', 1, 'margin');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('1310', 'Margin deposit — listed options', 'ASSET', 0, 'margin');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('1320', 'Variation margin posted — OTC (PB)', 'ASSET', 1, 'margin');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('1330', 'Initial margin — OTC (PB)', 'ASSET', 1, 'margin');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('1360', 'Cross-currency notional receivable', 'ASSET', 1, 'financing_asset');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('1400', 'Cash collateral posted — stock borrow', 'ASSET', 1, 'collateral');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('1410', 'Accrued rebate receivable', 'ASSET', 1, 'accrued_income');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('1500', 'Reverse repo receivable', 'ASSET', 0, 'financing_asset');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('1700', 'Options purchased at cost', 'ASSET', 1, 'positions_cost');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('2100', 'Payable — unsettled purchases', 'LIABILITY', 0, 'cash_pending_out');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('2300', 'Accrued interest payable — PB financing', 'LIABILITY', 0, 'accrued_expense');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('2310', 'Accrued borrow fees payable', 'LIABILITY', 1, 'accrued_expense');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('2320', 'Payments in lieu payable', 'LIABILITY', 1, 'accrued_expense');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('2330', 'Variation margin received — OTC (PB)', 'LIABILITY', 1, 'margin_liability');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('2340', 'Accrued swap payable', 'LIABILITY', 1, 'accrued_expense');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('2350', 'FX settlement payable', 'LIABILITY', 0, 'cash_pending_out');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('2360', 'Cross-currency notional payable', 'LIABILITY', 1, 'financing');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('2500', 'Repo borrowing', 'LIABILITY', 0, 'financing');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('2600', 'Securities sold short (at proceeds)', 'LIABILITY', 1, 'positions_short');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('2700', 'Prime-broker financing', 'LIABILITY', 0, 'financing');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('2750', 'Currency loans — principal', 'LIABILITY', 0, 'financing');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('2755', 'Accrued interest payable — currency loans', 'LIABILITY', 0, 'accrued_expense');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('2900', 'Options written (at premium received)', 'LIABILITY', 1, 'positions_short');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('3000', 'Contributed capital', 'EQUITY', 0, 'capital');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('3100', 'Treasury advances (internal funding)', 'EQUITY', 0, 'transfers');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('3900', 'FX conversion clearing', 'EQUITY', 0, 'fx_clearing');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('4000', 'Realised P&L — securities and options', 'INCOME', 1, 'realised');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('4200', 'Dividend income', 'INCOME', 1, 'income');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('4300', 'Interest income — cash and coupons', 'INCOME', 1, 'income');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('4320', 'Fee income — private credit', 'INCOME', 1, 'income');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('4350', 'Rebate income — cash collateral', 'INCOME', 1, 'income');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('4400', 'Futures variation margin', 'INCOME', 1, 'realised');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('4450', 'Swap results — resets, net coupons, STM settlements', 'INCOME', 1, 'realised');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('4500', 'Reverse repo interest', 'INCOME', 0, 'income');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('5000', 'Commissions and fees', 'EXPENSE', 1, 'fees');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('5100', 'Interest expense — PB financing', 'EXPENSE', 0, 'financing_cost');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('5150', 'Interest expense — currency loans', 'EXPENSE', 0, 'financing_cost');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('5300', 'Securities borrow fees', 'EXPENSE', 1, 'financing_cost');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('5400', 'Payments in lieu', 'EXPENSE', 1, 'financing_cost');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('5500', 'Interest expense — repo', 'EXPENSE', 0, 'financing_cost');
INSERT INTO chart(gl, name, kind, per_instrument, bucket) VALUES ('5700', 'Buy-in penalties', 'EXPENSE', 1, 'financing_cost');
INSERT INTO books(book_code, name, profile, starting_capital_c, sort_order, created_ts) VALUES ('P', 'Personal Book', 'INDIVIDUAL', 100000000, 0, 1792562508);
INSERT INTO books(book_code, name, profile, starting_capital_c, sort_order, created_ts) VALUES ('F', 'Fund Book', 'FUND', 2500000000, 10, 1792562508);
INSERT INTO books(book_code, name, profile, starting_capital_c, sort_order, created_ts) VALUES ('I', 'Institutional Book', 'INSTITUTION', 100000000000, 20, 1792562508);
COMMIT;
