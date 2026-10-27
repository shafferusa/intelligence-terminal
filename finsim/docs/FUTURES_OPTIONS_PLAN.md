# FinSim Live Terminal - Futures and listed options plan (planner, 2026-10-21 18:58 EDT)

Owner, 2026-10-21 ~17:30: "and the futures market should be good too, along with options. just plz do good" - said right after asking
for margin / leverage trading with initial margin and margin calls. Margin is designed in docs/MARGIN_DESIGN.md (being written); this
plan never designs futures or options margin and names the interfaces it needs from that design instead (section "Interfaces needed
from MARGIN_DESIGN.md").

Inputs read: the futures audit (13 gaps FUT-01..FUT-13) and the options audit (19 gaps OPT-01..OPT-19) of 2026-10-21, both run against
the deployed 1.0.3 on temp homes (their evidence is quoted per gap below); docs/BUILD2_PLAN.md (P01, P02, P04, P05, P06, P10, P11 with
the 18:31 addendum, contracts C1-C27); BUILD_LOG.md to 18:30 (P01, P02, P04, P05, P06, P07, P09 and KEYBINDS done; P10 was editing
chain.js, chart.js, preview.js, instrument.js, hedges.py and strategies.py at 18:33-18:43; P03, P08, P11, P12 not finished); the 17:48
orchestrator line ("Build 3 = margin + futures/options + keybind wiring + Build 2 leftovers"); docs/KEYBINDS.md (page-level key
contract); and the working-tree code: instruments.py, products/futures.py, products/options.py, sessions.py, calendar.py, clock.py,
tables.py, orders_preview.py, orders_rules.py, orders.py (AON packages), matcher.py, hedges.py, strategies.py, marks.py, risk.py,
execution.py (fee_breakdown, C25), accounting.py (LotView), lifecycle_eod.py (ProductJob, add_product_job C13), api.py (settings,
Unsupported), api_products.py (Futures tab with USX-26 / USX-27, C5), quotes/providers.py, parsers.py, cache.py, worker.py (C14),
rates.py, db.py (C4, marks / futures_settle / hedge_packages DDL), static chain.js, ticket.js, markets.js, settings.js, positions.js,
instrument.js. docs/MARGIN_DESIGN.md and docs/OTC_TERMS_PLAN.md did not exist at 18:58.

Scope: futures and listed options in the US Exchange account of every book (Personal 1M, Fund 25M, Institutional 1B) and in the PB
accounts, which trade the same instruments. Real delayed prices, pretend money, no simulation: every price, settlement, final value and
fill comes from an observed free source, or the screen says LIMITED / unavailable and why.

## Summary

Five packages, FO-1 to FO-5, file-disjoint among themselves, built after Build 2 finishes (they take over files Build 2 packages owned),
place all 32 gaps; nothing is deferred. Order: FO-1 (contracts, calendars, sessions, free data sources: the foundation, contracts first)
-> FO-2 (futures money and lifecycle), FO-3 (listed options money, marks and risk) and FO-4 (order entry: rules, matcher, ticket,
preview, hedges, strategies) in parallel -> FO-5 (screens and the end-to-end walk).

What a trader gets:
- Futures: about 60 roots instead of 20 - the micros (MES MNQ M2K MYM MGC SIL MCL QM M6E), the SOFR and Fed funds strips (SR3 SR1
  ZQ), UB and TN, HO and RB, PL and PA, ZM ZL KE ZO ZR, livestock (LE HE GF), AUD CAD CHF MXN NZD, NKD and EMD, CME bitcoin and ether
  (BTC MBT ETH MET) - plus ICE softs, the dollar index and Cboe VX as labelled reference rows. Every root is listed to the exchange's
  depth (no phantom September Treasuries), with a curve (spread to front, implied SOFR / fed funds rate next to the NY Fed fixing).
- Money like an FCM statement: the exchange settlement (Yahoo's daily bar of the completed trade date, read the next morning) is the VM
  price; CME holidays have no settlement and their trades count for the next trade date; physically delivered contracts get first
  notice / close-out dates, warnings, an opening-order block and the owner's close-out policy; expiring contracts stop at the real
  halt and settle to the official final value (Cboe SET / RLS for the S&P / Russell SOQ, NY Fed SOFR / EFFR averages for SR3 / SR1 /
  ZQ); itemized fees (commission, exchange and clearing, NFA; assumed and editable); futures-relevant compliance notices only.
- Trading: a tick-aware futures ticket (tick value, 32nds for Treasuries, steppers, snap-to-tick), calendar spreads and a one-click
  Roll (all-or-none on one observation, never inside the prints), option combos up to 4 legs with a net debit / credit limit, option
  rolls, an option analytics block in the preview (breakeven, max gain / loss, greeks x contracts, DTE, time value, delta as a labelled
  ITM proxy), options approval levels per book, index option delta hedges with futures / micros / ETFs, listed index / ETF options as
  labelled proxies in the futures hedge panel.
- Options: A.M. series settle on SET / RLS / VRO and stop trading the day before; P.M. series on the close; index chains for SPX / SPXW,
  XSP, NDXP, RUT / RUTW, VIX / VIXW and DJXW (NDX and DJX A.M. monthlies hidden: no free settlement value); DJX at DJIA / 100; VIX
  options priced off the chain-implied forward; roots kept apart; every listed expiry (LEAPS on demand); ETF options to 16:15; always
  settled assignments with contrary instructions; dividend-assignment warnings; an expiry ladder with pin-risk notices; IV term
  structure, skew, expected move and an IV rank from recorded history only (VIX history for the S&P family); per-position greeks,
  rho, beta-weighted delta and a spot x vol grid.

## Owner decisions (the build ships the default until the owner answers)

- D1 Delivery close-out policy (FUT-01). A paper account cannot make or take delivery. Settings > Futures & options offers
  WARN_REFUSE (default), AUTO_CLOSE (an FCM's practice: at 10:00 ET on the close-out date the position is closed at an observed
  print) and AUTO_ROLL (closed and reopened in the next active month as one spread fill). The planner recommends AUTO_CLOSE once the
  owner agrees. Under WARN_REFUSE nothing is traded at the close-out date, but one last-resort close stays unavoidable because delivery
  cannot be simulated: at 10:00 ET on the first notice day (notice contracts) or one business day before the last trade date (other
  physical contracts) a position still open is closed at the first fresh observed print (else at that day's exchange settlement),
  labelled "delivery close-out: a paper account cannot take delivery". This is the only exception to "nothing is ever sold for you";
  Unsupported and the ticket say so.
- D2 "Assume dividend assignment" per book (OPT-06): off by default; when on, a short ITM American call whose time value is below the
  next dividend is assigned after the close of the day before the (estimated) ex-date, labelled "assumed (rule)".
- D3 Options approval levels (OPT-17): Personal 3 (spreads), Fund 4 and Institutional 4 (naked), assumed and editable per book.
- D4 Futures fees (FUT-12): commission + exchange and clearing + NFA per side, assumed and editable (Table T3); the PB schedules'
  futures_per_side becomes the PB's commission, so its seeds should drop by the pass-through (request to the prime.py owner in FO-2)
  to keep a PB contract's total cost where it is today.

## Sources (free, verified) and what no free source gives

Verified 2026-10-21 by the futures and options audits (quoted) and, for the NY Fed history, by the planner (18:44 EDT, 2 requests with
the terminal's honest User-Agent):
- Yahoo v8 chart (the app's endpoint; a browser UA is allowed for Yahoo only): daily and 1-minute bars with period1 / period2 (ESZ26
  2026-10-20 15:55-17:01 returned 66 bars); futures daily bars carry the exchange settlement (ESZ26 10-20 close 7755.25 inside the
  15:59-16:00 settlement minute 7753.25 -> 7759.00; GCV26 settle-only bar O=H=L=C=4122.4 on a day without trades); after 18:00 ET the
  bar labelled d holds the next trade date's evening session (ESZ26 '10-21' bar c 7783.75 v 3,573 at 18:20; the 10-20 bar was final
  by 18:01 on 10-21); expired symbols vanish (ESU26.CME 404 a month after expiry; CLX26.NYM still answered the day after its last trade
  date); events=div (KO: past ex-dates and amounts only, no future one).
- Yahoo v8 spark (query1.finance.yahoo.com/v8/finance/spark?symbols=...&range=5d&interval=1d): HTTP 200, no key, 20 symbols per call;
  every new root of Table T1 answered with 10-21 data; deferred months answered except ZNM27 ZNU27 ZTM27 ZTU27 ZFM27 ZBU27 NQU27 RTYU27
  YMU27; not on Yahoo: VXX26.CBF and 10YZ26.CBT.
- Cboe delayed option chains cdn.cboe.com/api/global/delayed_quotes/options/{_SPX,_NDX,_RUT,_VIX,_DJX,_XSP,SPY,USO}.json (200): per
  contract option (the OCC root is inside it: SPX symbols are 18 characters, SPXW 19), bid, bid_size, ask, ask_size, iv,
  open_interest, volume, delta, gamma, vega, theta, rho, theo, change, open, high, low, tick (the last trade's direction, not a price
  increment), last_trade_price, last_trade_time, percent_change, prev_day_close; data level current_price, iv30, iv30_change and a
  pseudo bid / ask for indices.
- Cboe index values: cdn.cboe.com/api/global/delayed_quotes/quotes/_SET.json, _RLS.json, _VRO.json (200; SET 7736.8799 at 10:35:51 ET,
  RLS 2787.0601 at 11:01:19 on 10-21) and cdn.cboe.com/api/global/us_indices/daily_prices/SET_History.csv (since 1998; 10/16/2026
  7757.09, 10/21/2026 7736.88), RLS_History.csv, VRO_History.csv (10/21/2026 16.82), SPX_History.csv (10/16/2026 close 7659.44),
  VIX_History.csv (OHLC). _NDS.json answered 403: there is no free NDX SOQ (nothing retried or circumvented).
- Cboe CFE: cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX/VX_2026-11-18.csv (200; Trade Date, Futures, OHLC,
  Close, Settle, Change, Total Volume, EFP, Open Interest; end of day only; last row 2026-10-20 settle 18.4881, OI 223,451).
- NY Fed markets API (markets.newyorkfed.org): rates/secured/sofr/last/{n}.json and rates/unsecured/effr/last/{n}.json (audits: SOFR
  3.88 % and EFFR 3.88 % for 2026-10-20); planner 18:44: sofr/last/70.json -> 70 rows 2026-07-13..2026-10-20 (3.60 % .. 3.88 %) and
  effr/search.json?startDate=2026-09-01&endDate=2026-10-20 -> 34 rows (3.63 % .. 3.88 %): enough to average a month or compound a
  quarter.
- CFTC public reporting publicreporting.cftc.gov/resource/6dca-aqww.json (200 with the .gov User-Agent; E-MINI S&P 500
  open_interest_all 1,931,607 as of 2026-10-13): weekly, all months together.
- Not available free (shown as LIMITED / unavailable with this reason, never filled in): www.cmegroup.com refuses scripts (403 "This IP
  address is blocked due to suspected web scraping activity"), so CME settlements come from Yahoo, and the CME holiday table, contract
  terms, fees and margins are hand-maintained and labelled "verify"; CME options-on-futures chains (no free source); the NDX SOQ (403);
  ICE contract rules (not verified, so ICE rows are quotes-only); per-month futures open interest (not in Yahoo meta: the CFTC weekly
  all-months figure is shown instead); VX intraday quotes (the CFE CSV is end of day); declared future ex-dividend dates (Yahoo gives
  past ones: a projected date is labelled "estimated"); options' per-class minimum price increments (not in the chain data).

## Rules for every package

1. Build 2's rules 1-8 hold: one owner per file (requests for other files go to requests_for_others with the exact edit); the suite
   stays green (py -3.12 <scratchpad>\run_suite.py); tests you own are updated by hand when behaviour changes on purpose, new tests only
   for money math (arithmetic in comments); no simulation, no invented prices or fills; trader wording on screen (no endpoints, package
   or gap ids, internal codes); vanilla JS, ASCII, tables fit 1440 px with the dock open and do not clip at 1100 px; 0 console errors;
   never deploy, never touch %USERPROFILE%\.finsim or ports 8765 / 8865, no git, never change finsim.terminal.VERSION, never stop a
   process you did not start; end with ONE BUILD_LOG line (clock time from `date`) and the structured result.
2. Build on Build 2, never re-do it: C1 product routes (register_routes), C4 ensure_schema_ext (additive DDL from a module's bind; the
   marks.kind CHECK stays CLOSE / SETTLE / CURVE, so no new mark kinds), C5 instrument_extra, C11 ticket kinds / market plug-ins, C13
   add_product_job (FO-2 owns lifecycle_eod.py; FO-3 only calls add_product_job from its bind), C14 register_refresher / request_bars,
   C16 add_conventions (setdefault: an EXISTING sentence is changed only by tables.py's owner, FO-1, with the texts given in FO-1),
   USX-26 active month + month picker, USX-27 stale rows, SHELL-10 Settings > Calendar form, SR-11 margin overrides, EXEC-8 trade date,
   EXEC-9 15:30 cut-off, P10 USX-25 chain defaults / USX-33 spec view / USX-12 position hedges, P11 BM-09 / BM-27 and the gross
   notional addendum, MONEY-3 pending P&L of a flat future, SR-13 option margin reservation.
3. Keys: a new key is a new action in static/keys.js first (KEYBINDS.md: "never e.key for an action the registry has"); FO-4 owns
   keys.js only to add its four price-step actions.
4. Data etiquette: the owner's live app shares Yahoo's rate limit. Per builder session at most 60 Yahoo requests (spark first: 20
   symbols per call), 10 Cboe, 4 NY Fed, 2 CFTC, 3 CFE, at least 2 s apart, honest User-Agents (quotes/http.ua_for). Fills and lifecycle
   runs are proven offline with the FakeProvider and a shifted clock on fresh temp homes; real data is read-only. RAM is tight: one
   server and one browser per agent (Playwright py-3.14), POST /api/shutdown and tasklist after.
5. Every constant that comes from an exchange rulebook (Tables T1 and T2, the CME holiday table, price limits, reportable levels, the
   fee table) is labelled on screen "verify" (as FUT_MARGIN is today) with its as-of date. A builder verifies what free data can verify
   (tick grids and settlement minutes from Yahoo 1-minute data, A.M. last trading days and 16:15 option hours from Cboe
   last_trade_time) and records the result in BUILD_LOG; the rest goes on the owner's browser checklist in the release notes.
6. Settings added by this plan (FO-1 validates them in api.py, FO-5 renders them, the other packages read them with their defaults):
   futures_delivery_policy, futures_fee_overrides, options_level_by_book, option_dividend_rule_by_book, and a third kind of
   session_overrides (no settlement).

## Contracts (each owner builds its contracts as STEP 1; consumers code against them)

FO-C1 (FO-1, instruments.py) futures contract table and dates:
- FutSpec gains settlement ("CASH" | "PHYSICAL"), notice ("LBD_PREV_MONTH" | None: first notice day = last business day of the month
  before the contract month, first position day one business day earlier), halt_et and halt_tz (the last trade date's halt),
  final_method ("SOQ" | "SETTLE" | "SOFR_COMP" | "SOFR_AVG" | "EFFR_AVG" | "REF_RATE" | "INDEX_VALUE" | None for physical), settle_et
  (the root's daily settlement minute end, Table T1), depth (listing rule), price_format ("32NDS/1" | "32NDS/2" | "32NDS/4" | "32NDS/8"
  | "CENTS/8" | "DECIMAL"), unit (text), venue ("CME" | "CBOT" | "NYMEX" | "COMEX" | "ICE US" | "Cboe CFE"), micro (bool), support
  ("FULL" | "LIMITED") with limited_reason, and im / mm that may be None (margins come from MARGIN M1, never invented here).
- contract_dates(code, year, month, cal) -> {last_trade, halt_ts, first_position, first_notice, close_out, roll_date} (ISO dates; None
  where not applicable): close_out = first_position - 1 business day for notice contracts, last_trade - 2 business days for other
  physical contracts, None for cash; roll_date = last_trade - 8 calendar days for equity index, close_out - 5 business days for physical,
  last_trade - 5 business days for other cash contracts.
- every FUTURE master row's spec carries root, month, year, settlement, notice, final_method, last_trade, halt_et, halt_tz,
  first_position, first_notice, close_out, roll_date, settle_et, price_format, unit, venue, micro, tick_value (tick x multiplier),
  limited_reason; repair_futures_master(tx) at start rewrites the spec, tick, multiplier, session group and support of ACTIVE rows from
  the table (ZT's tick included) and records one FUTURES_MASTER_REPAIRED event.
- delivery_phase(inst, today, cal) -> {phase: "NONE" | "WARN" | "BLOCK" | "LAST_RESORT", close_out, bd_left, last_resort, why} (pure):
  NONE for cash; WARN from 5 business days before close_out; BLOCK from close_out; LAST_RESORT on first_notice (notice contracts) or on
  last_trade - 1 business day (other physical contracts).
- next_active_month(master, root_or_inst, today, cal) -> Instrument | None: the first ACTIVE FULL month of the root whose roll_date is
  after today (the single source that replaces api_products NOTICE_GROUPS / NOTICE_ROLL_D / EXPIRY_WEEK_D; FO-5 rewires USX-26 to it).

FO-C2 (FO-1, instruments.py) listed option series:
- INDEX_OPTION_SERIES (Table T2): root -> {underlying, settlement "AM" | "PM", value "SET" | "RLS" | "VRO" | "CLOSE", ltd "PRIOR_BD" |
  "EXPIRY", style EUROPEAN, exercise CASH, offered, reason}; underlying_id_of_root(root) lives here (SPXW -> SPX, NDXP -> NDX, RUTW ->
  RUT, VIXW -> VIX, DJXW -> DJX, XSP -> XSP; products/options.py re-exports it).
- INDICES gains XSP ("Mini-SPX", derived = SPX / 10, cboe _XSP, no Yahoo symbol of its own: the unique Yahoo-symbol index forbids a
  second ^GSPC row); DJX gets spec.yahoo_divisor 100 (^DJI / 100, rounded half-even to 0.01, matching Cboe's ^DJX 528.56).
- ensure_option writes spec: root, series ("STANDARD" | "INDEX" | "NON_STANDARD"), settlement, settlement_value, last_trading_day,
  last_trade_et (16:00 single stocks; 16:15 for the verified 16:15 ETF list and index options; 16:00 for an expiring P.M. index series
  on its expiry date; the prior business day's close for A.M. series) and the session group (US_EQUITY_OPTIONS | US_ETF_OPTIONS |
  US_INDEX_OPTIONS); option_series(inst) reads it (table fallback for old rows); repair_option_rows(tx) at start.
- root_kind(root, underlying) -> "STANDARD" | "INDEX" | "NON_STANDARD" (a root that is neither the underlying's symbol, its Cboe
  symbol, nor an INDEX_OPTION_SERIES root is NON_STANDARD: an OCC-adjusted deliverable).

FO-C3 (FO-1, sessions.py / calendar.py / clock.py) sessions and trade dates:
- new groups: US_ETF_OPTIONS (09:30-16:15, 13:15 on NYSE early closes), CME_LIVESTOCK (Mon-Fri 09:30-14:05 ET, no evening session),
  CME_CRYPTO (CME hours 18:00-17:00 ET), ICE_US (judged like FOREIGN: from the quote record's Yahoo currentTradingPeriod), CBOE_FUTURES
  (reference rows, no session); every SESSIONS entry gains a trader label ("CME equity index", "CME livestock", "ICE US (quotes only)").
- the CME holiday table data/cme_holidays.json (per year, date, group or "*": {"halt": "HH:MM", "settle": false} or "CLOSED", plus
  as_of and source "CME holiday notice, entered by hand - verify") merged with session_overrides' third kind {"close": "HH:MM",
  "settle": false}. A no-settlement day has no trade date of its own: its session belongs to the next trade date (is_trading_day False,
  settle_time None, sessions_for(next trade date) lists it first).
- settle_time_for(inst, d) = the root's settle_et (Table T1) capped at the session close; settle_time(group, d) stays for callers that
  have no instrument.
- trade_date_at(group, at) = the trade date of the session open at that instant, else of the next session; clock.trade_date(now, group,
  cal) for the futures groups skips no-settlement days (the calendar knows them), so a Thanksgiving 10:00 ET fill books 2026-11-27.
- day_order_expiry(group, at) = the close of the LAST session of the trade date the order can first trade in (a grains DAY order
  entered in the night session is good through the day session's close; today it dies at 08:45).

FO-C4 (FO-1, quotes/*) data sources (each with a FakeProvider setter for offline runs and tests):
- YahooChart.spark(symbols (at most 20), range, interval) -> {symbol: [(ts, close)]}; YahooChart.daily_closes(symbols, days) ->
  {symbol: [(exchange date, close)]} built on spark (one call per 20 symbols); YahooChart.session_bars(symbol, period1, period2).
- CboeIndexValues.settlement_value(code SET | RLS | VRO, d) -> (Decimal, quote_time, source) | None: quotes/_{code}.json when its
  last_trade_time is on d, else the row of daily_prices/{code}_History.csv for d; CboeIndexValues.history(code) -> [(date, close)]
  (VIX_History.csv OHLC: the close is used).
- CboeCfe.vx_settlements(expiry_date) -> [(trade_date, settle, open_interest)].
- Cftc.open_interest(market_names) -> {name: (as_of, open_interest_all)} (one SoQL query for the latest report date).
- NyFed.effr(n) and NyFed.series_range(series, start, end) (search.json); quotes/rates.RateStore keeps a persisted daily SOFR and EFFR
  history and fixing_average(series, start, end, method COMPOUND | AVERAGE) on the calendar-day convention (a weekend or holiday uses
  the previous business day's fixing; compounding per business day with weight d_i / 360).
- parsers.ChainRow gains root, rho, theo, change, prev_close, open, high, low; ChainSnapshot gains an index of EVERY expiry in the
  payload {expiry: {roots, n, k_min, k_max, kept}}, all_expiries(), roots(expiry), rows_for(expiry, root=None) and iv30_change;
  expiries() keeps returning the kept ones (FO-3 switches to all_expiries()).
- ChainCache.request(underlying_id, expiries=(), all_strikes=()) keeps full rows for the asked expiries (and every strike of those in
  all_strikes) on the next single-flight fetch; still an LRU of 2 snapshots.
- QuoteCache.set_reference_close(instrument_id, price, trade_date, label): a FUTURE record's prev_close is that reference (the prior
  settlement) and Yahoo's previousClose stays as prev_close_yahoo; record_from_intraday applies spec.yahoo_divisor (DJX) and puts the
  derived XSP record (SPX / 10, same times, source "Yahoo ^GSPC / 10") whenever the SPX record is put.
- QuoteWorker.request_curve(root) -> Future of {instrument_id: {last, quote_time, daily: [(date, close)]}} for every listed month of
  the root (one spark call per 20 months; at most once a minute per root; each call takes one BROWSE token); futures 1-minute bars are
  requested from the session open (period1 / period2) instead of range=1d; built-in refreshers futures_listings (06:30 ET daily),
  cfe_vx (17:30 ET daily) and nyfed_rates (08:30 ET daily).

FO-C5 (FO-1, api.py):
- setting keys (validated, SETTINGS_CHANGED with before / after, plain messages naming the field): futures_delivery_policy =
  WARN_REFUSE (default) | AUTO_CLOSE | AUTO_ROLL; futures_fee_overrides = {root: {commission, exchange, nfa}} in USD per contract per
  side (each >= 0, at most 4 decimals); options_level_by_book = {book code: 1..4} (defaults P 3, F 4, I 4); option_dividend_rule_by_book
  = {book code: true | false} (default false); session_overrides accepts {close: HH:MM, settle: false} next to HH:MM and CLOSED.
- quote_dict: a FUTURE quote carries prev_close_basis (prior settlement | prior settlement (provisional) | previous close) and
  prev_close_yahoo.
- GET /api/calendar adds cme: [{date, group, halt, settle, source, as_of}] for the year.
- the static Unsupported rows are rewritten as given in FO-1.

FO-C6 (FO-2, products/futures.py) for FO-4 / FO-5:
- bind(rt): add_product_job FUTURES_DELIVERY_GUARD and FUTURES_ROLL_NOTICE, register_refresher futures_settlements and cftc_oi,
  ensure_schema_ext if a table is added.
- prior_settlement(services, conn, instrument_id, before=None) -> {price, trade_date, official, source, method, as_of} | None;
  provisional_settlement(services, inst, d) -> Observed | None (the close of the 1-minute bar ending at settle_et).
- instrument_extra(rt, inst, now) for FUTURE (C5): headline = last with its change against the prior settlement; spec_view rows of
  FUT-09; actions [roll] when held.
- fee_lines(services, acct, inst, side, qty) -> [{label, amount_c, basis}] (commission, exchange and clearing, NFA: Table T3 plus the
  owner's overrides).
- oi_weekly(root) -> {open_interest, as_of, market, source} | None.

FO-C7 (FO-3, products/options.py, marks.py, risk.py) for FO-4 / FO-5:
- underlying_level(services, snap, option_inst) -> (S, basis, quality, as_of): VIX and VIXW -> the expiry's implied forward; DJX ->
  the divided master mark; XSP -> SPX / 10; otherwise the underlying's mark. Every S used for option marks, margin inputs, delta-USD,
  gamma, hedges and the gross-notional ratios comes from it.
- chain_view(services, underlying_id, expiry=None, root=None, strikes=None, now=None) adds series [{expiry, root, label, settlement,
  dte, cycle (monthly | weekly | daily | quarterly | LEAPS), kept, closed_today}], forward (VIX), iv30, iv30_change, expected_move,
  hidden [{root, reason}] and adjusted_series; rows are keyed by (root, strike) for the selected (expiry, root).
- routes (C1): POST /api/options/instructions, GET /api/options/expiries?account_id= or ?book=, GET /api/options/iv?underlying_id=,
  POST /api/options/settlement_value (the owner enters a published A.M. value with its source, for a hidden root that is held).
- risk.position_greeks(services, snap, account_id) -> {instrument_id: {delta, gamma, theta, vega, rho (x contracts x multiplier), dte,
  moneyness, source}}; the risk view adds rho, beta_weighted_delta and pnl_grid.

FO-C8 (FO-4) for FO-5:
- T.ticket.open({kind: FUTURES_SPREAD, account_id, legs: [{instrument_id, side}], qty, net_limit}); T.ticket.open({combo: [{instrument_id,
  side, ratio}], account_id}); T.ticket.addLeg(leg) (a chain click with Shift adds a leg); T.ticket.roll({account_id, instrument_id,
  qty}) for a held future or option.
- T.fmt.futPx(instOrSpec, px), T.fmt.parseFutPx(instOrSpec, text) and T.fmt.tickText(inst) in core.js.
- POST /api/orders/preview and /api/orders/submit accept package: {kind: FUTURES_SPREAD | OPTION_COMBO | ROLL, legs: [{instrument_id,
  side, ratio}], qty, net_limit, tif}.

## Interfaces needed from MARGIN_DESIGN.md (named here, designed there)

- M1 futures rates: margin.futures_rates(services, conn, account_id, inst) -> {im_c, mm_c, source} | None, resolved in this order: the
  owner's Settings > Margins & limits override (SR-11), MARGIN's table, the static FutSpec value (the 20 old roots). None means "not
  set": FO-4's preview refuses an OPENING futures order with MARGIN_UNSET "set the initial and maintenance margin of <root> in
  Settings > Margins & limits (assumed - verify current exchange values)" - never a guessed number. Table T1's new roots carry no
  im / mm; MARGIN's table (or the owner's override) supplies them, micros included.
- M2 package requirement: margin.package_requirement(services, conn, account_id, legs) -> {initial_c, maintenance_c, method, notes}
  for a FUTURES_SPREAD / ROLL (spread credit, same root) and an OPTION_COMBO (strategy-based offsets), used by FO-4's preview and fill.
  Until MARGIN lands, FO-4 uses the sum of the legs (futures: M1 per leg on the net position after the package; options:
  strategies.package_margin_c as today), labelled "legs margined separately".
- M3 option requirement inputs: whatever replaces options.margin_requirement_c takes S from FO-3's underlying_level (VIX forward, DJX /
  XSP levels) and the series' settlement attribute (A.M. / P.M.).
- M4 forced close path: if MARGIN adds margin calls that close positions, FO-2's delivery close-out uses the same system-close function
  and the same Settings consent wording (name it, e.g. margin.system_close(ctx, account_id, inst, qty, reason, quote)); until then FO-2
  uses execution.apply_system_fill.
- M5 Settings > Margins & limits lists every root of Table T1 (MARGIN's rows) so the micros and the new roots can be margined.

Files this plan claims that MARGIN_DESIGN may also need (one owner per file across Build 3: the orchestrator sequences a MARGIN package
that edits one of them before or after the FO package that owns it, never at the same time, or MARGIN puts its code in its own module
called through M1-M4): products/futures.py (FO-2), products/options.py, risk.py (FO-3), orders_preview.py, components/preview.js
(FO-4), pages/positions.js, pages/risk.js, pages/settings.js, api_views.py (FO-5), api.py, tables.py (FO-1). Files this plan does NOT
claim and edits only by request: accounting.py and execution.py (exact edits in FO-2), prime.py (FO-2), db.py (C4 is used instead).

## Order of work

FO-1 first: its STEP 1 (contracts FO-C1 to FO-C5 with the 20 old roots and the index series filled in) within about 45 minutes,
logged in BUILD_LOG as "FO-1 STEP 1 contracts landed". The orchestrator may start FO-2, FO-3 and FO-4 at that line (they code against
the contracts and re-verify when FO-1 is done); their hard dependency is FO-1. FO-5 starts when FO-2, FO-3 and FO-4 are done. If a
MARGIN package owns a file listed above, it runs before or after that FO package, never concurrently.

## Packages

### FO-1 - Contracts, calendars, sessions and free data sources (the foundation)

- depends_on: none (Build 2 finished)
- gaps (6): FUT-04 FUT-08 FUT-13 OPT-02 OPT-13 OPT-14
- contract work for (finished elsewhere): FUT-01 FUT-02 FUT-03 FUT-05 FUT-07 FUT-09 FUT-12 OPT-01 OPT-03 OPT-04 OPT-06 OPT-08 OPT-15
  OPT-17 OPT-18
- owns:
  - `finsim/finsim/terminal/instruments.py`
  - `finsim/finsim/terminal/sessions.py`
  - `finsim/finsim/terminal/calendar.py`
  - `finsim/finsim/terminal/clock.py`
  - `finsim/finsim/terminal/tables.py`
  - `finsim/finsim/terminal/config.py`
  - `finsim/finsim/terminal/api.py`
  - `finsim/finsim/terminal/quotes/providers.py`
  - `finsim/finsim/terminal/quotes/parsers.py`
  - `finsim/finsim/terminal/quotes/cache.py`
  - `finsim/finsim/terminal/quotes/worker.py`
  - `finsim/finsim/terminal/quotes/rates.py`
  - `finsim/finsim/terminal/data/cme_holidays.json` (new)
  - `finsim/tests/test_terminal_sessions.py`
  - `finsim/tests/test_terminal_core.py`
  - `finsim/tests/test_terminal_quotes.py`
  - `finsim/tests/test_terminal_instruments_foreign.py`
  - `finsim/tests/test_terminal_api.py`

STEP 1 - CONTRACTS FIRST (about 45 minutes): FO-C1 to FO-C5 as signatures that work, with the 20 existing roots of Table T1 and the
index option series of Table T2 filled in (the new roots may follow in the same session), then the BUILD_LOG line "FO-1 STEP 1
contracts landed". Then the gaps.

FUT-04 (contract coverage; Table T1):
- Add every T1 root with all FutSpec fields. Yahoo symbol = root + month code + yy + "." + suffix (CME, CBT, NYM, CMX, NYB). New roots
  carry im / mm None (M1: MARGIN's table or the owner's override; until then their opening orders are refused MARGIN_UNSET by FO-4).
- Verify before encoding, gently (rule 4): (a) tick grids - one spark call per 20 front months (range=1d, interval=1m): the proposed
  tick passes when no close is off its grid and, on a liquid root, some closes are off the grid of twice the tick (otherwise the
  proposed tick may be too fine); an illiquid root is marked "tick not verifiable from today's prints" in BUILD_LOG; (b) settlement
  minutes - one chart call (range=1d, interval=1m) per group representative (SR3, ZQ, UB, HO, PL, MGC, ZM, LE, HE, 6A, BTC, plus 6E, HG
  and ZC for the old groups): settle_et is the end of the busiest minute inside the documented window (rates 15:00 and gold 13:30 /
  silver 13:25 are already verified by the audit's volume spikes). Record both results per root in BUILD_LOG.
- ICE rows (KC SB CC CT OJ DX, suffix NYB, group ICE_US) are LIMITED with limited_reason "ICE contract rules (notice days, hours) are
  not verified in this build: quotes and curve only". VX rows (group CBOE_FUTURES, no Yahoo symbol) are LIMITED with "Cboe VIX futures:
  end-of-day settlement and open interest from Cboe; no intraday quote in the free feed"; the cfe_vx refresher records their SETTLE
  marks (dated by the CSV's Trade Date) and keeps spec.open_interest / spec.oi_as_of. VX listing: the monthly expiries (Wednesday 30
  days before the third Friday of the following month, the business day before when that is a holiday - verify) for 8 months.
- Crypto rows (BTC MBT ETH MET) use group CME_CRYPTO and halt 16:00 Europe/London on the last trade date (11:00 ET most of the year;
  converted per date, so the March / October DST gaps come out right).
- tables.SESSIONS gains the new groups with trader labels (FO-C3); FUTURES_SPOT_MONTH keeps its 20 rows (FO-2 says "no limit in the
  static table - verify" for a root without one).
- The existing 20 roots get their T1 corrections and new fields (ZT tick 0.00390625, per-root settle_et, settlement, notice, halt,
  final_method, depth, price_format, unit, venue); repair_futures_master(tx) applies them to live rows at start.

FUT-08 (CME holidays):
- data/cme_holidays.json: {as_of, source, years: {YYYY: [{date, group: "*" or a group, halt: HH:MM, settle: false} or {date, group,
  closed: true}]}}, seeded for 2026 and 2027 from the NYSE full-day holidays of the existing file: Good Friday, Christmas and New
  Year's Day CLOSED (today's CME_CAL closures); every other NYSE holiday "halt 13:00 ET, no settlement" for the CME groups and CLOSED
  for CBOT_GRAINS and CME_LIVESTOCK, each row labelled "usual CME holiday pattern - verify against CME's notice for YYYY". Early-close
  days (11-27, 12-24) keep today's 13:00 approximation. The owner corrects any row in Settings > Calendar (FO-5) through
  session_overrides' third kind.
- calendar.py loads it (cme_rows(year), is_cme_no_settlement(group, d), cme_closed(group, d)); sessions.py / clock.py implement FO-C3.
  Result: sessions_for(CME_EQUITY_INDEX, 2026-11-26) is empty; sessions_for(..., 2026-11-27) = [11-25 18:00 -> 11-26 13:00 (holiday,
  no settlement), 11-26 18:00 -> 11-27 13:00 (early close)]; settle_time(..., 2026-11-26) None; FUTURES_VM never keys 11-26 (its
  vm_keys already skips days that are not trading days).
- GET /api/calendar adds the cme rows (FO-C5); session_overrides accepts the third kind; the Unsupported / conventions texts say
  "hand-maintained, verify".
- clock.trade_date: 2026-11-25 19:00 ET -> 11-27; 2026-11-26 10:00 ET -> 11-27 (today 11-26); 2026-11-27 11:00 ET -> 11-27;
  test_terminal_core.py gets these rows next to its existing ones (trade dates move money posting dates).

FUT-13 (evening-session conventions):
- QuoteCache.set_reference_close (FO-C4): at start the cache loads, per ACTIVE future, the latest marks SETTLE row before the current
  trade date as its reference (label from the row: "prior settlement" when FO-2's official settlement wrote it, else "prior settlement
  (observed print)"); FO-2 keeps it current (provisional at settle time + 15 min, official the next morning). chg % on every screen
  then compares with the prior settlement and no longer reads 0.00 at the 18:00 reopen; quote_dict says which basis it used.
- Futures 1-minute bars (C8 / request_bars) are fetched with period1 = the open of the session of the current trade date (the
  previous day's 18:00 ET; the last session when closed) and period2 = now, so one chart never mixes two trade dates.
- Request for the owner of accounting.py in Build 3 (else the integrator), exact edit: in both LotView builders (today about lines 893
  and 1163) read the lots with `LEFT JOIN fills f ON f.fill_id = lots.open_fill_id` and set open_date = f.trade_date when the instrument
  is a FUTURE (else the New York date of open_ts as now), so a lot opened at 18:15 shows the next trade date like its fill.

OPT-02 (DJX at DJIA / 100):
- DJX keeps Yahoo ^DJI with spec.yahoo_divisor 100: record_from_intraday divides last, prev_close, day_high, day_low, the bar and
  bars_tail and labels the source "Yahoo ^DJI / 100", rounded half-even to 0.01 (52,856.09 -> 528.56, Cboe's ^DJX); repair the DJX row
  at start. Every consumer (marks, margin S, observed_close, risk delta-USD and gamma-USD, hedges) then reads 528.56. FO-3 adds the
  generic 2 % guard (chain underlying vs master mark) for every index.
- XSP joins INDICES (FO-C2): derived from the SPX record (/ 10, half-even to 0.01, same quote_time), cboe _XSP, kind INDEX, LIMITED as a
  reference row (options only).

OPT-13 (series hours):
- US_ETF_OPTIONS (09:30-16:15) for the options of ETF_OPTIONS_1615 = SPY (verified: its options printed to 16:14:59 on 10-21,
  including the expiring series) plus QQQ, IWM and DIA only after the builder sees a contract of each print after 16:00:59 in today's
  Cboe chain (one fetch each, recorded in BUILD_LOG); an unverified ETF stays US_EQUITY_OPTIONS (orders after 16:00 rest: the cautious
  side).
- ensure_option / repair_option_rows write last_trading_day and last_trade_et per FO-C2: an expiring P.M. index series stops at 16:00
  on its expiry date (XSP's 10-21 series last traded 15:59:59 while other XSP expiries traded to 16:14:57; the builder confirms SPXW
  the same way on a daily expiry); A.M. series stop at the prior business day's close (VIX 10-21 last traded 10-20). FO-4 enforces
  these times in the preview and the matcher.
- Unsupported row: "Index options' Global Trading Hours and curb session - NOT_OFFERED - index options trade in the regular session
  only (09:30-16:15 ET)".

OPT-14 (every expiry and strike reachable):
- parse_chain keeps full rows inside CHAIN_MAX_DAYS (400) / CHAIN_STRIKE_BAND (35 %) as now, plus the light index of every expiry
  (FO-C4); ChainCache.request(..., expiries, all_strikes) keeps full rows for an asked expiry (or all its strikes) on the next
  single-flight fetch, at most 2 extra expiries per underlying (config CHAIN_ON_DEMAND_MAX = 2). Measure RSS with the SPX chain
  (13 MB JSON) before and after: the index may add at most 2 MB; record it in BUILD_LOG.
- FO-3 exposes all_expiries() in chain_view with the LEAPS label; FO-5 shows them and a strikes "all" toggle.

Contract work for the other packages (all in FO-1's files):
- FUT-01: FO-C1 dates, delivery_phase, next_active_month; Unsupported row "Physical delivery of futures - NOT_OFFERED - a paper
  account cannot make or take delivery: physically delivered contracts are closed or rolled before their close-out date (Settings >
  Futures & options); a position still open at first notice is closed at an observed price".
- FUT-02: per-root settle_et; YahooChart.daily_closes (spark); new FUT_SETTLE text (below).
- FUT-03: halt_et / halt_tz / final_method; CboeIndexValues; NyFed.series_range + RateStore history + fixing_average.
- FUT-05: listing depth per T1 (energy 24 consecutive months; SR3 12 quarterlies; SR1 13 and ZQ 12 months; metals' active months 2
  years out; grains 2 crop years; equity index 5 quarterlies; Treasuries 3 quarterlies; FX 4 quarterlies; livestock 6-8; crypto 6
  monthlies). A new month is LIMITED "not quoted by the free feed yet" until one Yahoo answer (the daily futures_listings refresher
  probes unanswered months, 20 per spark call) and FULL from then on (spec.first_quote_date); a month outside the exchange's cycle is
  never listed - existing rows of that kind (ZNU27 ZTU27 ZFU27 ZBU27 ...) are deleted when nothing references them (instruments
  _blocking_reference) else turned LIMITED "not listed by the exchange in this cycle"; SR3 / SR1 / ZQ rows carry tick 0.0025 for the
  nearest expiring month and 0.005 for the others, re-set by the daily roll; QuoteWorker.request_curve.
- FUT-07: ZT tick 0.00390625 (1/8 of 1/32; tick value USD 7.8125; 1,849 of 3,616 ZTZ26 one-minute prices on 10-21 were off the old
  1/128 grid, 0 off 1/256) applied to live rows by repair_futures_master; price_format per root (ZB / UB 32NDS/1, ZN / TN 32NDS/2,
  ZF 32NDS/4, ZT 32NDS/8, ZC ZW ZS KE ZO CENTS/8, everything else DECIMAL).
- FUT-09: Cftc.open_interest; T1 unit texts.
- FUT-12: futures_fee_overrides validation.
- OPT-01: INDEX_OPTION_SERIES (T2), CboeIndexValues, option spec fields.
- OPT-03 / OPT-08: ChainRow.root, rho, theo, change, prev_close, open, high, low; ChainSnapshot.rows_for(expiry, root).
- OPT-04: XSP row; underlying_id_of_root map.
- OPT-06 / OPT-17: option_dividend_rule_by_book and options_level_by_book validation.
- OPT-15: ChainSnapshot.iv30_change; CboeIndexValues.history(VIX).
- OPT-18: root_kind; the parser keeps every root.

Texts FO-1 writes (tables.CONVENTIONS keys that already exist, and api.py's static Unsupported rows):
- FUT_SETTLE: "Variation margin uses the exchange settlement: the close of Yahoo's daily bar for the completed trade date, read the
  next morning (delayed). During the evening the 1-minute bar ending at the contract's settlement time is shown as a provisional
  settlement only."
- CME_CAL: "CME holidays come from a hand-maintained table (verify against CME's notice): on a no-settlement holiday the session
  trades, publishes no settlement and its trades count for the next trade date."
- OCC: "At expiry, equity and ETF options in the money by USD 0.01 or more are exercised or assigned at the strike from the closing
  price (your do-not-exercise instruction applies until 17:00 ET); P.M. index options settle in cash on the closing value; A.M. index
  options stop trading the day before and settle in cash on Cboe's opening value (SET, RLS, VRO). Early assignment is warned about,
  never drawn at random."
- new keys FUT_DELIVERY ("A paper account cannot make or take delivery: a physically delivered contract must be closed or rolled by its
  close-out date, two business days before first notice (or before the last trade date where there is no notice period).") and
  FUT_FINAL ("An expiring cash-settled future stops at its exchange halt and settles to the official final value: the S&P 500 or
  Russell 2000 opening quotation (Cboe SET / RLS), 100 minus the NY Fed SOFR or EFFR average for SOFR and fed funds futures, else
  the exchange's final settlement read from Yahoo's daily bar of the last trade date.").
- FUT_MARGIN and OPT_MARGIN (OPT_MARGIN still says "moved to a margin deposit", stale since SR-13) are MARGIN_DESIGN's sentences:
  FO-1 changes them only with the text MARGIN's owner requests.
- Unsupported static rows: "Crypto" becomes "Spot crypto - NOT_OFFERED - coins are not tradeable; CME bitcoin and ether futures trade
  on the Futures tab"; "Options on futures - NOT_OFFERED - no free delayed source for CME option prices; the futures hedge panel offers
  listed index / ETF options as labelled proxies"; "Early assignment of short options - NOT_SIMULATED - never drawn at random: the
  terminal warns the day before an ex-dividend date when a short call is likely to be assigned (and assumes it only if you turn that
  rule on)"; plus the rows "Physical delivery of futures" (FUT-01 above), "Index options' Global Trading Hours and curb session"
  (OPT-13 above), "ICE softs and US dollar index futures - LIMITED - quotes and curve only: their notice-day rules are not verified in
  this build", "Cboe VIX futures - LIMITED - end-of-day settlement and open interest only", "NDX and DJX A.M. monthly options - LIMITED
  - no free source for their A.M. settlement value: hidden from the chain; NDXP and DJXW (P.M.) trade instead", "Futures open interest
  per month - LIMITED - not in the free feed; the CFTC weekly all-months figure is shown", "Trade-at-settlement (TAS) orders -
  NOT_OFFERED - not in this build", "Option price increments - APPROXIMATED - limit prices use a 0.01 grid; the exchange's per-class
  increments are not in the free data". The "Forced liquidation and margin calls" row is MARGIN_DESIGN's to rewrite.

FO-1 acceptance:
- Futures tab (Build 2's markets.js, real delayed quotes): the new groups and roots appear, one row per root, FULL rows quoted; ICE and
  VX rows LIMITED with their reasons; no ZNU27 / ZTU27 / ZFU27 / ZBU27; ZNM27 LIMITED "not quoted by the free feed yet"; CL lists 24
  months; GCZ26 (not GCV26) is gold's row.
- GET /api/instruments/GCZ26 spec: settlement PHYSICAL, first_notice 2026-11-30, first_position 2026-11-27, close_out 2026-11-25,
  last_trade 2026-12-29; ZNZ26 close_out 2026-11-25, last_trade 2026-12-21, halt 13:01 ET; ZCZ26 close_out 2026-11-25, last_trade
  2026-12-14; CLZ26 (no notice period) close_out 2026-11-18, last_trade 2026-11-20; NGX26 close_out 2026-10-26, last_trade 2026-10-28
  (delivery_phase on 2026-10-21 = WARN, 3 business days left); 6EZ26 close_out 2026-12-10, last_trade 2026-12-14, halt 10:16 ET;
  ESZ26 CASH, last_trade 2026-12-18, halt 09:30 ET, final SOQ, roll_date 2026-12-10; SR3Z26 last_trade 2027-03-16, final SOFR_COMP;
  ZTZ26 tick 0.00390625, tick_value 7.8125 (the live row repaired).
- Shifted clock, FakeProvider: an ESZ26 fill at 2026-11-26 10:00 ET has trade date 2026-11-27; no FUTURES_VM key exists for
  2026-11-26; a ZCZ26 DAY order entered 2026-10-21 21:00 ET is still working at 09:45 ET on 10-22 and expires at 14:20 ET; /api/sessions
  lists US_ETF_OPTIONS, CME_LIVESTOCK, CME_CRYPTO, ICE_US, CBOE_FUTURES with labels; /api/calendar?year=2026 lists the cme rows.
- DJX quotes 528.56 labelled "Yahoo ^DJI / 100" (within 2 % of the _DJX chain's underlying); XSP quotes SPX / 10; an SPY option row is
  in US_ETF_OPTIONS and a FakeProvider BTO fills at 16:10 ET; an expiring XSP series row carries last_trade_et 16:00; an SPX A.M.
  monthly row's last_trading_day is the Thursday.
- The SPY snapshot indexes 33 expiries (to 2029-01-19) with full rows kept for the 400-day window; asking for 2029-01-19 keeps its rows
  after one more Cboe fetch; RSS growth for the SPX index under 2 MB.
- After 18:00 ET a futures chart holds only the bars since 18:00; chg % of ESZ26 just after 18:00 compares with the prior settlement
  (not 0.00) and the quote says which basis.
- PATCH /api/settings accepts the four new keys and the third session_overrides kind; options level 5 or a fee of -1 is refused with a
  plain message naming the field.
- Money tests (hand-worked): 4 ZT contracts up 1 tick = 4 x 0.00390625 x 2,000 = USD 31.25; DJX 52,856.09 / 100 = 528.5609 -> 528.56;
  XSP 7,659.44 / 10 = 765.944 -> 765.94; the trade-date table above. Requests used are listed in BUILD_LOG (within rule 4).

### FO-2 - Futures settlement, delivery, expiry, fees and notices (money and lifecycle)

- depends_on: FO-1
- gaps (5): FUT-01 FUT-02 FUT-03 FUT-10 FUT-12
- contract work for (finished elsewhere): FUT-05 (prior settlements for the curve), FUT-06 (roll reminders), FUT-09 (instrument_extra,
  weekly open interest), FUT-13 (keeps the cache's reference close current)
- owns:
  - `finsim/finsim/terminal/products/futures.py`
  - `finsim/finsim/terminal/lifecycle_eod.py`
  - `finsim/finsim/terminal/compliance.py`
  - `finsim/tests/test_terminal_futures.py`
  - `finsim/tests/test_terminal_lifecycle.py`

STEP 1 - CONTRACTS FIRST: FO-C6 (bind, prior_settlement, provisional_settlement, instrument_extra, fee_lines, oi_weekly) so FO-4 and
FO-5 can code against them. Then the gaps; every money change is hand-worked in test_terminal_futures.py.

FUT-02 (the exchange settlement is the VM price):
- Probe first (rule 4): read the previous trade date's daily bar of ESZ26, GCZ26 and ZNZ26 with one spark call at least 3 times, 1 h or
  more apart, during your session; the bar of d-1 must not change from the time you choose onward. Set SETTLE_FINAL_AFTER_ET (default
  07:00 ET on the calendar day after d) to the earliest time your reads support and record the reads in BUILD_LOG. From 18:00 ET on d
  the bar labelled d holds the next evening session (audit), so nothing reads the bar of d before d+1 SETTLE_FINAL_AFTER_ET.
- futures_settlements refresher (C14, registered in futures.bind; every 15 minutes): (a) from SETTLE_FINAL_AFTER_ET, for each completed
  trade date without an official SETTLE mark, for every future that is held, has a working order or is a root's active month, one
  spark call per 20 symbols (range=5d, interval=1d) -> the close of d is recorded as the marks SETTLE row of d (method "exchange
  settlement (Yahoo daily bar)", quality close, source "Yahoo daily bar <symbol>: exchange settlement, delayed", quote_time = the
  settle_et epoch of d) through the existing light marks.record command, and set_reference_close(..., "prior settlement"); (b) from
  settle_et + 15 min, without any fetch, provisional_settlement from the quote record's bars -> set_reference_close(..., "prior
  settlement (provisional)"). A settle-only bar (O=H=L=C, a month without trades, e.g. GCV26 10-20) is a valid settlement.
- lifecycle_eod.MarksRecord stops writing SETTLE rows for futures (record_time / mark_kind for FUTURE); the rows already in a database
  stay as history.
- FUTURES_VM: vm_keys makes (account, instrument, d) due once the official SETTLE mark of d exists (not at settle time + 5 min);
  settle_print reads only that recorded row (the snapshot-record and daily_fallback paths go; ProductJob.retry_payload for FUTURES_VM
  goes); after 2 calendar days without a mark the existing SETTLE_PRINT_MISSING skip + attention names the symbol and the reason;
  business_date stays d. daily_fallback (kept for other callers) refuses the bar of d between 18:00 ET on d and SETTLE_FINAL_AFTER_ET
  on d+1. The futures_settle reference logic (legs, unposted, carried term, superseded keys) is unchanged; only the price source moves.
  MONEY-3 keeps showing a flat position's pending P&L until the VM posts (now the next morning); NAV is unaffected (open trade equity
  uses the live mark).
- Release-note item for the owner: compare 3 dates of VM settle prices with CME's published settlements in a browser.

FUT-01 (physical delivery):
- FUTURES_DELIVERY_GUARD (ProductJob through add_product_job, key {account}:{instrument}:{phase}, due 10:00 ET on trading days) for every
  held PHYSICAL contract, phase from instruments.delivery_phase:
  - WARN (from close_out - 5 business days): one WARN attention per account and contract, refreshed daily: "GCZ26: close-out
    2026-11-25 (5 business days) - a paper account cannot take delivery: close or roll to GCG27", details {roll_to, close_out,
    first_notice} (FO-5 renders Close / Roll buttons).
  - BLOCK (close_out 10:00 ET), by settings.futures_delivery_policy: WARN_REFUSE -> ERROR attention "past the close-out date: close or
    roll now; first notice <date>" (FO-4 refuses opening orders); AUTO_CLOSE -> one system fill closing the position at the latest
    executable observed print (is_executable, fresh), memo "delivery close-out (your setting)"; AUTO_ROLL -> one command closing it and
    opening next_active_month for the same quantity at both legs' fresh prints observed within the same minute (FO-4's spread rule),
    memo "delivery roll (your setting)". No fresh print -> SKIPPED, retried every 30 minutes.
  - LAST_RESORT (first_notice 10:00 ET for notice contracts, last_trade - 1 business day for the others), whatever the policy: a
    position still open is closed at the first fresh observed print; if none comes before the session close, at that day's official
    settlement once futures_settlements records it (trade date = that day), memo "delivery close-out: a paper account cannot take
    delivery". The normal fee lines apply. The attention auto-resolves when the position is flat.
  - Use MARGIN's system-close function when it exists (M4), else execution.apply_system_fill.
- FUTURES_FINAL runs only for CASH contracts; a PHYSICAL row that reaches it is SKIPPED "physical contract: closed by the delivery
  guard".

FUT-03 (expiry day):
- FUTURES_FINAL (CASH contracts) waits for the official final value of the last trade date (LTD), recorded as the SETTLE mark of LTD
  by futures_settlements according to final_method, then (after the LTD VM) closes the position at it as today:
  - SOQ: ES and MES from CboeIndexValues SET of LTD (published from about 10:30 ET), RTY and M2K from RLS; NQ, MNQ, YM, MYM, EMD and
    NKD from Yahoo's daily bar of LTD, labelled "Yahoo daily bar of the last trade date (no free opening-quotation value to cross-check)".
    For ES and RTY the next morning's Yahoo bar is compared: a difference above 0.01 opens an attention (the SET / RLS value stands: it
    is the opening quotation itself).
  - SOFR_COMP (SR3: 100 - SOFR compounded over the reference quarter), SOFR_AVG (SR1: 100 - the calendar-day average of SOFR over the
    contract month), EFFR_AVG (ZQ: the same with EFFR), computed with RateStore.fixing_average once the last fixing is published (next
    business day, about 08:00 ET); Yahoo's LTD bar is the cross-check: a difference above 0.0025 -> SKIPPED FINAL_PENDING_REVIEW with
    an attention showing both values; nothing is settled on a disagreement.
  - SETTLE / REF_RATE / INDEX_VALUE (BZ, QM, MCL, BTC, MBT, ETH, MET, HE, GF): Yahoo's daily bar of LTD read after the halt on LTD and
    again the next morning (before the symbol leaves the feed; both reads must agree, else an attention), labelled with what it stands
    for (ICE Brent settlement, CME CF Bitcoin Reference Rate, CME Lean Hog Index ...); QM and MCL are also compared with the CL
    settlement of the same date.
  - If the symbol has left the feed and no value was recorded: attention "final settlement unavailable from the free feed" and the
    position stays open; the owner may enter the published value with its source (C1 route POST /api/futures/final_value, labelled
    "entered by the owner"); nothing is invented.
- The halt itself (no orders, no fills after halt_ts) is FO-4's; the instrument page and the ticket show it (FO-5, FO-4).

FUT-10 (futures compliance notices; compliance.py):
- For a FUTURE: no PDT, WASH_SALE or REG_T rows (FINRA 4210 day trading and Reg T are securities rules; futures are Section 1256
  contracts). FUTURES_LIMITS runs for every profile, informational, with the SR-11 position_limit_overrides.
- A REPORTABLE notice "reportable position: CFTC level N contracts (17 CFR 15.03, verify)" from the table of 17 CFR 15.03(b), verified
  free on 2026-10-21 (eCFR versioner API, title 17, part 15, section 15.03: HTTP 200 when the request allows gzip, with the .gov
  User-Agent). Levels read that day: S and P 500 Stock Price Index 1,000; Other Broad-Based Securities Indexes 200; 30-Day Fed Funds
  600; 2-Year Notes 1,000; 5-Year and 10-Year Notes 2,000; 30-Year Bonds 1,500; Major Foreign Currencies 400; Other Foreign Currencies
  100; U.S. Dollar Index 50; Crude Oil 350; Natural Gas 200; Heating Oil 250; Unleaded Gasoline 150; Gold 200; Silver 150; Copper 100;
  Platinum 50; Corn 250; Soybeans 150; Wheat 150; Soybean Meal 200; Soybean Oil 200; Oats 60; Rough Rice 50; Live Cattle 100; Lean Hogs
  100; Feeder Cattle 50; Cocoa 100; Coffee 50; Cotton 100; Sugar No. 11 500; Orange Juice 50; All Other Commodities 25. Store it as a
  constant in compliance.py with its as-of date; the root mapping is labelled "mapping assumed - verify" (micros and E-minis counted in
  their own contracts; SOFR, crypto and palladium under All Other Commodities unless the owner changes it).
- A one-line tax note "Section 1256: marked to market at year end, 60 percent long-term and 40 percent short-term (informational)".
- The group's daily price-limit sentence (add_conventions, labelled verify): equity index - a 7 percent overnight limit and the 7 / 13 /
  20 percent circuit breakers with the NYSE; grains and livestock - daily limits reset twice a year (verify the current limit); energy,
  metals, rates, FX - dynamic circuit breakers, no fixed daily limit.

FUT-12 (itemized fees):
- fee_lines(services, acct, inst, side, qty): commission = the PB schedule's futures_per_side in a PB account, else the DEFAULT
  commission of Table T3 (full size 0.85, micro 0.25 per contract per side, assumed); plus exchange and clearing per root (T3, assumed)
  and NFA 0.02 (assumed); settings.futures_fee_overrides wins per root; every line is labelled "assumed - verify with your FCM fee
  schedule". ES: 0.85 + 1.38 + 0.02 = 2.25 per side, the same total as the old flat fee.
- Request for the owner of execution.py in Build 3 (else the integrator), exact edit in fee_breakdown: when inst.kind is FUTURE and
  services.futures has fee_lines, use its lines instead of the single commission line (total = their sum; basis = their labels joined
  with a plus sign). The C25 posting already writes each line to 5000 with its own memo, so the fill, the preview (orders_preview._fees
  reads fee_breakdown) and Transactions itemize with no other change.
- Request for the owner of prime.py (D4): each PB schedule's seeded futures_per_side becomes that PB's commission, so lower each seed by
  the full-size pass-through 1.40 (GS 2.00 -> 0.60, MS 2.10 -> 0.70, BNP 2.25 -> 0.85, and so on), labelled assumed; a PB full-size
  contract then costs what it costs today.

Cross-items FO-2 also does:
- FUTURES_ROLL_NOTICE (ProductJob, 10:00 ET): from each held contract's roll_date (FO-C1) an INFO attention "ESZ26 rolls to ESH27 from
  2026-12-10 (8 days before expiry)" with details {roll_to}, auto-resolved when flat (FUT-06's reminders; for physical contracts the
  delivery guard's WARN item is the reminder).
- instrument_extra (FUT-09): unit, multiplier, tick and tick value, point value, trading hours and the daily settlement time,
  settlement type, last trade with its halt time, first notice and close-out, final settlement method, prior settlement with source and
  as-of, margins from M1 with their source, the price-limit sentence, the weekly CFTC open interest with its date.
- the cftc_oi refresher (weekly, Saturday 08:00 ET: one SoQL query through Cftc.open_interest for the mapped market names; kept in
  memory and in spec.oi_weekly); prior_settlement for FO-5's curve.
- OPTION_EXPIRY's when-text in lifecycle_eod.py becomes "the expiry date: A.M. series from 11:30 ET, P.M. equity and ETF series from
  16:20 ET, P.M. index series from 16:35 ET" (FO-3 changes expiry_keys accordingly).

FO-2 acceptance (FakeProvider fills and a shifted clock on fresh homes; real reads within rule 4):
- Money tests, hand-worked in test_terminal_futures.py:
  (a) long 1 ESZ26 carried at reference 7,700.00, official settlement 7,755.25 -> VM USD 2,762.50 (55.25 x 50), posted the next
      morning with business date d; the old observed-print rule would have posted 2,612.50 at 7,752.25;
  (b) 4 ZTZ26 up one tick -> USD 31.25;
  (c) an ESZ26 fill at 2026-11-26 10:00 ET settles with 11-27; no VM is posted for 11-26;
  (d) long 2 GCZ26: WARN item from 2026-11-18; WARN_REFUSE -> ERROR item at 2026-11-25 10:00 ET and nothing traded; AUTO_CLOSE -> SELL 2
      at the fresh print at 10:00 ET on 11-25, IM released, fees 2 x (0.85 + 1.55 + 0.02) = 4.84; LAST_RESORT close at 2026-11-30 10:00
      ET under WARN_REFUSE;
  (e) ES final from SET (fixture value); SR3 final 100 - R with R compounded from a fixture of daily SOFR fixings (a Friday weighs 3 /
      360); ZQ final 100 - the November EFFR average over 30 calendar days; a 0.01 gap between SR3's computed final and Yahoo's bar ->
      FINAL_PENDING_REVIEW, nothing posted;
  (f) fees: ES 0.85 + 1.38 + 0.02 = 2.25 per side; MES 0.25 + 0.35 + 0.02 = 0.62 per side; three 5000 lines with their memos.
- Walk (existing screens): Transactions show the VM entry labelled exchange settlement (Yahoo daily bar); the attention strip shows the
  NGX26 and GCZ26 delivery items with their dates; Positions shows a delivery close-out fill with its memo; the ESZ26 preview lists no
  PDT, wash-sale or Reg T rows and shows the Section 1256 note and the reportable level; Data Status lists futures_settlements and
  cftc_oi.

### FO-3 - Listed options: A.M. / P.M. settlement, index options, VIX forward, assignment, dividends, analytics

- depends_on: FO-1
- gaps (9): OPT-01 OPT-03 OPT-05 OPT-06 OPT-07 OPT-12 OPT-15 OPT-16 OPT-18
- contract work for (finished elsewhere): OPT-04 (index chains server side, the 2 percent guard), OPT-08 (leg fields, closed expiries,
  index level), OPT-09 / OPT-10 / OPT-11 (underlying_level, series), OPT-14 (chain_view exposes every expiry)
- owns:
  - `finsim/finsim/terminal/products/options.py`
  - `finsim/finsim/terminal/products/dividends.py`
  - `finsim/finsim/terminal/marks.py`
  - `finsim/finsim/terminal/risk.py`
  - `finsim/finsim/terminal/lending.py`
  - `finsim/tests/test_terminal_options.py`
  - `finsim/tests/test_terminal_marks.py`
  - `finsim/tests/test_terminal_risk.py`
  - `finsim/tests/test_terminal_dividends.py`
  - `finsim/tests/test_terminal_lending.py`

STEP 1 - CONTRACTS FIRST: FO-C7 (underlying_level, the chain_view additions, the four routes, position_greeks). Then the gaps.

OPT-01 (A.M. settlement):
- OPTION_EXPIRY by series (option_series): P.M. series as today (the observed close; equity and ETF physical, index cash). A.M. series
  (SPX monthlies, RUT, VIX, VIXW): expiry_keys makes them due from 11:30 ET on the expiry date; the value is
  CboeIndexValues.settlement_value(SET | RLS | VRO, expiry) - the quotes JSON when its last_trade_time is on the expiry date, else the
  History CSV row - stored in a new table option_settlement_values (underlying_id, expiry, code, value, quote_time, source, recorded_ts;
  C4 from options.bind), cited in the fill memo ("cash-settled at SET 7757.09 (Cboe, 2026-10-16 opening quotation)") and never written as
  the index's CLOSE mark. No value yet -> SKIPPED EXPIRY_PENDING_SETTLEMENT with an attention, retried hourly; never the close.
- NDX A.M. monthlies and DJX standard options are not offered (T2): hidden from the chain with the reason; one already held (1.0.3
  allowed it through Strategies) waits at expiry as EXPIRY_PENDING_SETTLEMENT with the action "enter the published settlement value and
  its source" (POST /api/options/settlement_value), settled on it with "entered by the owner".
- Orders on an A.M. series after its last trading day are refused by FO-4 from FO-1's spec ("stopped trading Thursday; settles on
  Friday's opening quotation").

OPT-03 (roots kept apart):
- chain_view keys rows by (root, strike) for the selected (expiry, root); series carries one entry per (expiry, root) with its label,
  e.g. "Fri 2026-11-20 . SPXW . PM . 30 DTE" and "Fri 2026-11-20 . SPX . AM (SET)"; ?root= selects; the default root is the P.M. root for
  SPX / NDX / RUT / DJX (SPXW, NDXP, RUTW, DJXW), VIX on VIX monthly dates and VIXW on the other Wednesdays, and the standard root for
  equities. leg_dict and ensure_option carry the root. FO-4 makes strategies and hedges pick a root explicitly.

OPT-05 (VIX priced off its forward):
- Per VIX expiry, from the cached chain: among strikes with both mids, the one with the smallest absolute C_mid - P_mid; F = K + (C_mid -
  P_mid) x e^(rT), r from marks.risk_free (^IRX, else the curve's 3M), T = calendar days / 365; labelled "implied forward from Cboe mids
  (K, C, P, r, T)", cached per snapshot. underlying_level returns F for VIX / VIXW options; marks.option_model prices them as Black-76
  (BSM on F with q = r); margin inputs (M3), delta-USD, gamma and the gross-notional ratio use F; never spot ^VIX. No strike with both
  mids -> "forward unavailable": those options stay unpriced by the model and their delta-USD is listed as unavailable.

OPT-06 (dividend-assignment risk; no random events):
- dividends.next_ex(services, conn, underlying_id) -> (date, amount, basis "declared" | "estimated"): declared when the dividends table
  holds a future ex-date (Yahoo has none today), else last ex-date + the median interval of the last 4 ex-dates (business-day rolled),
  amount = the last one, labelled "estimated, not declared".
- OPTION_DIVIDEND_RISK (ProductJob through add_product_job, 15:00 ET on trading days): for ITM American calls whose underlying's next
  ex-date is the next business day, time value = Cboe bid - intrinsic (S = the underlying last): a short with time value below the
  dividend -> WARN "assignment likely tonight: time value 0.12 < dividend 0.53"; a long -> INFO "exercise today to collect the dividend"
  with an Exercise action; a deep ITM short put with time value under 0.05 -> INFO "early assignment possible".
- option_dividend_rule_by_book (D2, default off): when on and the condition held at the close, the short call is assigned after that
  close through the existing _physical path (ASSIGNMENT at the strike, trade date that day), memo "assumed (rule): dividend assignment".
- dividend_outlook(services, account_id) -> per underlying {next_ex, amount, basis} for Positions and Risk (FO-5).

OPT-07 (assignments always settle; contrary instructions):
- h_expire never leaves an expired option open: a put exercise or call assignment that leaves the account short with no borrow
  available opens the short with a forced borrow (lending.open_borrow(..., forced=True): flagged HARD_TO_BORROW at the heuristic's fee,
  memo "assignment borrow"), plus one ERROR attention "short 100 XYZ after assignment: cover it or keep paying the borrow fee". No
  automatic buy-in (nothing is bought for you). The EXERCISE_NEEDS_BORROW / ASSIGNMENT_NEEDS_BORROW skips go; their open attention items
  resolve at start.
- Contrary instructions: table option_instructions (account_id, instrument_id, expiry, action DNE | EXERCISE, qty, created_ts,
  request_id; C4); POST /api/options/instructions {account_id, instrument_id, action, qty}, accepted on the expiry date until 17:00 ET
  for AMERICAN equity and ETF options (refused with the reason otherwise: "index options settle in cash automatically", "after 17:00 ET
  the exercise decision is final"); OPTION_EXPIRY applies them (DNE: the contract lapses even when in the money; EXERCISE: exercised
  even when out of the money, warned), and the result memo names the instruction.
- OPTION_EXPIRY_DAY (ProductJob, 15:00 ET on an expiry date with positions): WARN for in-the-money contracts whose delivery the account
  cannot fund (the cash of a call exercise or put assignment above available) or borrow (a call assignment without shares where the
  heuristic shows nothing to borrow), with the amounts. Margin consequences are MARGIN_DESIGN's.

OPT-12 (expiry ladder and pin risk):
- expiry_ladder(services, account_id or book) and GET /api/options/expiries: per expiry date the contracts with ITM / OTM from the
  latest underlying print (or the forward for VIX), distance to the strike in percent and points, intrinsic, time value, the settlement
  basis (close, SET, RLS or VRO), the instruction if any, and after 16:00 ET the expected outcome from the close (exercise, assign or
  lapse; cash needed; shares delivered).
- PIN_RISK (inside OPTION_EXPIRY_DAY at 15:00 ET): a short with absolute(S - K) / S under 1 percent -> WARN "assignment is uncertain
  near the strike; close or roll".

OPT-15 (implied volatility views):
- chain_view header: iv30 and iv30_change (Cboe), and the expected move to the selected expiry = ATM call mid + ATM put mid (labelled
  "straddle mid"), all labelled "Cboe IV".
- GET /api/options/iv?underlying_id=: the term structure (ATM IV per kept expiry: the strike nearest the spot or forward, the mean of
  call and put IV) and the skew (IV by strike for one expiry), from the cached chain only (no extra fetch).
- IV rank only from recorded history: table option_iv_history (underlying_id, date, iv30, iv30_change, source; C4); every chain fetch
  writes today's row (the last of the day wins) and a C14 refresher (16:20 ET) fetches, one at a time, at most 5 held underlyings not
  fetched that day; shown "IV rank (since <first date>, N days)" and never back-filled. For SPX, XSP and SPY the window may use Cboe
  VIX_History.csv, labelled "VIX history (Cboe) used as the S and P 500 30-day implied volatility".

OPT-16 (risk):
- risk.position_greeks (FO-C7): delta, gamma, theta, vega and rho x contracts x multiplier from the Cboe row (model fallback, labelled),
  DTE and moneyness (ATM within 0.5 percent). The risk view adds the rho total, a beta-weighted delta (sum of delta-USD x beta / SPY
  last, in SPY shares and USD; beta from the master spec, 1.0 assumed and labelled where missing) and a P&L grid: spot -20 to +20
  percent (steps of 5) x vol -10 to +10 points (steps of 5) at T+0, T+7 and at expiry, options repriced with marks.price_option on
  their model inputs (S from underlying_level), shares and futures linear; unpriced positions are listed, never counted as zero. P11's
  gross-notional ratios take S from underlying_level.

OPT-18 (OCC-adjusted series):
- A NON_STANDARD root (FO-1 root_kind) is hidden from the rows and the strategies; chain_view lists it in adjusted_series {root,
  expiries, n} with "deliverable not known to this build"; ensure_option books it with spec.series NON_STANDARD and support LIMITED; a
  held one is valued at the Cboe mid with a WARN "non-standard deliverable: valued at the Cboe mid; its exercise needs manual review",
  and its expiry is SKIPPED NON_STANDARD_REVIEW with an attention (never settled as 100 shares). FO-4 refuses orders at preview.

Contract work: OPT-04 - for an INDEX underlying, underlying_dict shows the level only (no pseudo bid / ask / mid); the guard: when the
chain underlying differs from the master mark by more than 2 percent the root is UNDERLYING_MISMATCH (rows read-only, ensure_option
refused, marks unavailable, expiry SKIPPED, one attention per root). OPT-08 - leg_dict carries root, rho, theo, change, prev_close,
mid, last and volume; a series whose last_trade_et has passed on its expiry date leaves `series` (still reachable with ?expiry= for a
held contract). OPT-14 - chain_view lists all_expiries(); an expiry outside the kept window answers 202 {status fetching} after
ChainCache.request(uid, expiries=[exp]) and its rows on the next fetch; ?strikes=all the same.

FO-3 acceptance (FakeProvider and fixtures for expiries on a shifted clock; real Cboe reads within rule 4):
- Money tests, hand-worked in test_terminal_options.py:
  (a) long 1 SPX 2026-10-16 C7700 -> SET 7757.09 -> USD 5,709.00 receivable T+1 (57.09 x 100; the short pays the same); the P7700
      lapses (the old close rule paid 4,056.00 = 40.56 x 100 at the 7,659.44 close);
  (b) VIX 2026-10-21 C16.50 -> VRO 16.82 -> USD 32.00 (the close rule gave 18.00); C16.75 -> USD 7.00 (the close rule let it lapse);
  (c) DJXW C520 with the ^DJI close 52,856.09 -> DJX 528.56 -> USD 856.00; a C530 lapses (1.0.3 would have paid about USD 5.23 M);
  (d) XSP C765 with the SPX close 7,659.44 -> 765.94 -> USD 94.00;
  (e) VIX forward fixture: K 17, C mid 1.20, P mid 0.80, r 3.88 percent, T 28 / 365 -> F = 17 + 0.40 x e^(0.0388 x 0.076712) =
      17.4012;
  (f) dividend risk: short 1 KO C60, S 70.40, bid 10.52 -> time value 0.12 < 0.53 -> WARN; rule on -> ASSIGNMENT of 100 KO at 60.00
      after the close (proceeds 6,000.00 T+1 from 100 held shares; a forced borrow when none are held);
  (g) DNE on long 1 AAPL C200 (cost 45.00, close 200.30) -> lapses, realised -45.00 (without it: BUY 100 at 200.00 = 20,000.00 payable
      T+1, basis 20,045.00);
  (h) an assignment with no shares and nothing to borrow -> short 100 at the strike with a forced borrow and an ERROR item; the option
      row is closed;
  (i) 2 SPY C680 with Cboe delta 0.45 -> position delta 90 shares; the rho total = sum of rho x contracts x 100.
- API walk (real Cboe): /api/instruments/SPX/chain?expiry=2026-11-20 lists SPX (A.M., SET) and SPXW (P.M.) as separate series; the DJX
  chain lists DJX standard as hidden with its reason and shows DJXW; the VIX chain carries the forward and its inputs; the SPY chain
  lists 33 expiries with LEAPS reachable on demand; /api/options/iv answers from the cached chain; shifted clock: PIN_RISK and dividend
  items appear at 15:00 ET; the risk view returns rho, beta_weighted_delta and pnl_grid (FO-5 renders them).

### FO-4 - Order entry: halts and delivery blocks, tick-aware futures ticket, spreads and rolls, option combos, option analytics, hedges, strategies, approval levels

- depends_on: FO-1
- gaps (8): FUT-06 FUT-07 FUT-11 OPT-09 OPT-10 OPT-11 OPT-17 OPT-19
- contract work for (finished elsewhere): FUT-01 (warn / refuse in the preview), FUT-03 (halts), FUT-04 (micro overlay), FUT-12 (fee
  lines in the preview), OPT-01 (A.M. series refused after their last trading day), OPT-03 (explicit roots), OPT-13 (series cut-offs),
  OPT-18 (non-standard refused)
- owns:
  - `finsim/finsim/terminal/orders_preview.py`
  - `finsim/finsim/terminal/orders_rules.py`
  - `finsim/finsim/terminal/orders.py`
  - `finsim/finsim/terminal/matcher.py`
  - `finsim/finsim/terminal/hedges.py`
  - `finsim/finsim/terminal/strategies.py`
  - `finsim/finsim/terminal/strategies_catalogue.py`
  - `finsim/finsim/terminal/books.py`
  - `finsim/finsim/terminal/static/core.js`
  - `finsim/finsim/terminal/static/keys.js`
  - `finsim/finsim/terminal/static/components/ticket.js`
  - `finsim/finsim/terminal/static/components/preview.js`
  - `finsim/finsim/terminal/static/components/hedge.js`
  - `finsim/tests/test_terminal_orders.py`
  - `finsim/tests/test_terminal_hedges.py`
  - `finsim/tests/test_terminal_strategies.py`
  - `finsim/tests/test_terminal_books.py`
  - `finsim/tests/test_terminal_fill_live_bars.py`

STEP 1 - CONTRACTS FIRST: FO-C8 (ticket opening forms, T.fmt futures helpers, the package request shape). Then the gaps.

Gates FO-4 adds for the other packages (preview and submit share validate; the matcher shares orders_rules):
- FUT-01: a FUTURE in delivery_phase WARN gets the warning "close-out 2026-11-25 (5 business days): a paper account cannot take
  delivery - consider GCG27"; in BLOCK or LAST_RESORT an OPENING order or a flip (anything that raises the absolute position or crosses
  zero) is refused with DELIVERY_CLOSE_OUT "a paper account cannot take delivery - trade GCG27"; closing orders always pass.
- FUT-03: after halt_ts on the last trade date an order is refused ("ESZ26 stopped trading at 09:30 ET on its last trade date") and
  evaluate returns NONE HALTED; working orders on the contract are cancelled at the halt with that reason.
- OPT-01 / OPT-13: an option order after spec.last_trading_day + last_trade_et is refused and stops filling (A.M. series after the
  prior business day's close: "stopped trading Thursday; settles on Friday's opening quotation"; an expiring P.M. index series after
  16:00 ET); EXEC-9's 15:30 opening cut-off applies on the last trading day.
- OPT-18: a NON_STANDARD series is refused ("non-standard deliverable: not tradeable in this build").
- M1: an OPENING futures order whose margin.futures_rates is None is refused MARGIN_UNSET (see M1).
- FUT-04: INDEX_OVERLAY offers the micro (MES / MNQ) when the full-size contract rounds to 0 or leaves the larger residual, showing
  both residuals.
- FUT-12: the preview shows the fee lines from fee_breakdown (preview.js renders several lines).

FUT-06 (calendar spreads and Roll):
- Package kind FUTURES_SPREAD: two legs of one root, 1:1, buy one month and sell another; spread = front month price - deferred month
  price. hedge_packages gains package_kind and net_limit (orders.SCHEMA_EXT through C4); the legs are orders with fill_mode AON.
- orders_rules.evaluate_spread (pure): both legs working and unexpired, the session open, both quotes executable and fresh, both prices
  taken from ONE spark observation (QuoteWorker.request_curve(root): one call returns both legs) with both last 1-minute bars in the
  same minute; derived spread = last_front - last_deferred; a bought spread (buy front, sell deferred) fills when spread <= net_limit, a
  sold one when spread >= net_limit (MARKET: whenever both legs print in one observation); each leg fills at its own observed last;
  quantity capped by each leg's participation cap; otherwise it rests with the reason ("the legs last traded in different minutes",
  "spread -63.25 is above your limit -65.00"). Disclosed sentence: "spread derived from two delayed prints; there is no spread order
  book in the free feed; all-or-none at the legs' observed prices, never inside them". The matcher evaluates working spreads on quote
  updates of either leg (request_curve at most once a minute per root).
- Kind ROLL (T.ticket.roll from Positions, FO-5): a FUTURES_SPREAD that closes the held month and opens next_active_month for the same
  quantity (long ESZ26 -> sell ESZ26 / buy ESH27). The preview shows the roll price (the spread), its value (spread x multiplier x qty,
  e.g. (7,851.25 - 7,788.00) x 50 = USD 3,162.50 per contract: "the new month is 63.25 points above the old one"), the two fee sets and
  the margin from M2 (until MARGIN: the net position's requirement, unchanged for the same root).
- strategies_catalogue gains futures "Calendar spread" and "Roll" templates opening the spread ticket; hedges.py's CALENDAR candidate
  becomes a FUTURES_SPREAD package (no leg risk) instead of a sequential leg.

FUT-07 (tick-aware futures ticket):
- core.js T.fmt.futPx(inst, px) by price_format: 32NDS/n shows points-32nds with ASCII fractions of a 32nd (ZB 117-08; ZN 112-16 1/2;
  ZF 109-00 1/4; ZT 101-29 5/8), CENTS/8 shows cents and eighths (the CBOT apostrophe form, 425.75 -> 425 and 6/8), DECIMAL as today;
  the decimal on hover. T.fmt.parseFutPx accepts those forms, the CME compact forms (112-165, 112-16+) and decimals.
- ticket.js, for a FUTURE: one info line "tick 0.25 = USD 12.50 . point USD 50 . contract USD 389,400 . last trade 2026-12-18 09:30
  ET . cash-settled" (physical: "close-out 2026-11-25"); the price boxes step one tick with the new keys.js actions ticket.price_up /
  ticket.price_down (default ArrowUp / ArrowDown, box actions) and ten ticks with ticket.price_up_10 / ticket.price_down_10 (PageUp /
  PageDown), read through T.keys.is per the KEYBINDS contract; a typed off-grid price snaps to the nearest tick with a visible "rounded
  to the tick" note before the preview (PRICE_TICK on the server stays the backstop); Treasury prices typed and shown in 32nds.
- The preview adds "P&L per tick for 3 contracts: USD 37.50" (tick value x qty) and the contract value. ZTZ26 LIMIT 101.92578125
  (101-29 5/8) is accepted.

FUT-11 and OPT-19 (options on futures: listed proxies instead):
- The futures hedge panel keeps "Options on futures: not offered - no free delayed source for CME option prices" and offers, as
  labelled proxies with basis risk, protective listed options from the Cboe chains: ES / MES -> SPXW, XSP, SPY; NQ / MNQ -> NDXP, QQQ;
  RTY / M2K -> RUTW, IWM; YM / MYM -> DJXW, DIA; CL / MCL / QM -> USO; GC / MGC -> GLD; SI / SIL -> SLV; ZN / TN -> IEF; ZB / UB -> TLT;
  6E / M6E -> FXE. Sized by notional: contracts = round(futures notional / (proxy level x 100)), a put about 7 percent out of the money
  for a long (a call for a short) at the nearest listed strike, the first expiry at least 30 days out, labelled "proxy: XSP options
  (Cboe), basis risk against ESZ26". Long 1 ESZ26 (notional 389,400): XSP at 772.00 -> round(389,400 / 77,200) = 5 puts; SPY at
  676.10 -> round(389,400 / 67,610) = 6 puts. The futures ticket's info line points to the same alternatives.

OPT-09 (option analytics in the preview):
- orders_preview adds an `option` block for OPTION orders from the chain row and underlying_level (FO-3): DTE; intrinsic and time
  value; breakeven (call K + premium, put K - premium, at the expected price); max gain and max loss of the single leg ("unlimited"
  where true: a short call's loss, a long call's gain); delta, gamma, theta and vega x contracts x 100 (Cboe, labelled) and IV;
  "probability ITM ~ absolute delta (a market-implied proxy, not a forecast)"; the payoff points of strategies.payoff_grid for one leg;
  for index options the settlement basis (A.M. SET / P.M. close) and the last trading time.
- preview.js renders it; ticket.js adds Bid / Mid / Ask price buttons. A chain click keeps today's prefill (the clicked side,
  marketable); Mid sets the limit with the note "a limit at the mid fills only if the delayed ask (bid) later reaches it: fills are at
  the observed NBBO, never inside it".

OPT-10 (combos, explicit expiries and strikes, roll):
- Shift + click on a chain price adds a leg to a combo in the ticket (T.ticket.addLeg; up to 4 legs on one root, side and ratio per
  leg), sent as package {kind OPTION_COMBO, legs, qty, net_limit, tif}.
- evaluate_package gains net_limit: fills only when the observed net (sum of sign x executable price x ratio, ask for buys and bid for
  sells, a debit positive) is at or better than net_limit (debit <= limit, credit >= limit), on ONE chain observation with displayed
  sizes covering each leg's quantity x ratio; each leg at its observed NBBO side, never inside; else it rests and the order view shows
  the current net. AON packages without net_limit behave as today.
- strategies.package_legs accepts expiry=YYYY-MM-DD (or weeks= / days=) and strike overrides with explicit units, K:25 (absolute) and
  %:-7 (percent); a bare number is an absolute strike (the old "below 60 means percent" rule goes; the Strategies tab sends units).
- Roll of a held option (T.ticket.roll from Positions): a 2-leg OPTION_COMBO closing it and opening the chosen expiry and strike
  (default the same strike on the next monthly expiry) with a net limit.
- Margin of a combo or roll: M2 (until MARGIN lands: strategies.package_margin_c, labelled "legs margined separately").

OPT-11 (index option delta hedges):
- For an INDEX underlying: DELTA_HEDGE_FUTURE with n = round(delta x contracts x 100 x S / (F x multiplier)) on the front full-size
  future (ES, NQ, RTY, YM) and its micro (MES, MNQ, M2K, MYM) when that leaves the smaller residual (XSP -> ES / MES with S the XSP
  level, DJX -> YM / MYM with S the DJX level); DELTA_HEDGE_ETF with shares = round(delta x contracts x 100 x S / ETF last) in SPY,
  QQQ, IWM or DIA; each shows its ratio, the residual delta-USD and the observed quotes used; VIX offers none ("VX futures are reference
  rows only: no intraday quote in the free feed"). The old "BUY 100 DJX" equity leg on an index row never appears.
- STO 1 SPXW C7800 with delta 0.30 and SPX 7,720.00: position delta-USD -231,600 -> BUY 1 ESZ26 (231,600 / 389,400 = 0.59; residual
  +157,800) or BUY 6 MES (231,600 / 38,940 = 5.95; residual +2,040) or BUY 343 SPY at 676.10.

OPT-17 (options approval levels):
- books.options_level(conn, book_code) from settings.options_level_by_book (D3 defaults P 3, F 4, I 4): level 1 covered calls (a short
  call covered by unreserved shares) and cash-secured puts (cash for the strike x 100 x contracts available); level 2 adds buying
  options; level 3 adds spreads (a short covered by a long of the same underlying and right: verticals, calendars, diagonals, iron
  condors); level 4 adds naked shorts. Closing orders (BTC, STC) always pass. Preview and submit refuse with OPTION_LEVEL "naked calls
  need options level 4 (this book: 3); change it in Settings"; the ticket shows the book's level.

FO-4 acceptance (browser walk with real delayed quotes in the dock; fills with the FakeProvider on a shifted clock):
- Money tests, hand-worked in test_terminal_orders.py / test_terminal_strategies.py:
  (a) bought spread ESZ26 7,788.00 / ESH27 7,851.25 at limit -60.00: spread -63.25 <= -60.00 -> both legs fill at their prints, fees
      2 x 2.25 = 4.50, no cash otherwise; legs from different minutes -> rests with the reason;
  (b) roll of long 1 ESZ26 -> SELL ESZ26 / BUY ESH27 as one package; the preview shows USD 3,162.50 and two fee sets;
  (c) combo AAPL C200 ask 5.20 / C210 bid 1.95: net debit 3.25 <= 3.30 -> fills, cash -325.00 - 1.30 fees = -326.30 (T+1); at limit
      3.20 it rests;
  (d) 3 ESZ26: P&L per tick USD 37.50; ZTZ26 LIMIT 101.92578125 accepted; a typed 101.926 snaps to 101-29 5/8 with the note;
  (e) index delta hedge numbers above (1 ES, 6 MES or 343 SPY);
  (f) Personal (level 3) STO 1 AAPL C210 with no shares -> OPTION_LEVEL; the same as the lower leg of a vertical -> accepted.
- Walk: the ESZ26 ticket shows the info line, steps the limit with the arrow and page keys, and previews fee lines and P&L per tick;
  NGX26 previews the close-out warning; a shifted clock past 2026-11-25 refuses BUY GCZ26 and accepts SELL; a shifted clock after
  09:30 ET on 2026-12-18 refuses ESZ26; an SPX A.M. monthly order on its expiry Friday is refused with the opening-quotation sentence;
  Shift + clicks build a 2-leg combo with a net limit that previews and submits; the SPXW option preview shows the analytics block and
  Bid / Mid / Ask; the hedge panel of an SPXW short call offers ES / MES / SPY hedges and the panel of a long ES position offers the
  XSP / SPY proxies; 0 console errors; the dock fits 1440 px.

### FO-5 - Screens: the Futures tab and curve, instrument pages, option chain, chart, Positions, Risk, Settings; the end-to-end walk

- depends_on: FO-2, FO-3, FO-4 (and FO-1 through them)
- gaps (4): FUT-05 FUT-09 OPT-04 OPT-08
- screens for every other gap (listed in the walk below)
- owns:
  - `finsim/finsim/terminal/api_products.py`
  - `finsim/finsim/terminal/api_views.py`
  - `finsim/finsim/terminal/static/pages/markets.js`
  - `finsim/finsim/terminal/static/pages/instrument.js`
  - `finsim/finsim/terminal/static/pages/positions.js`
  - `finsim/finsim/terminal/static/pages/risk.js`
  - `finsim/finsim/terminal/static/pages/settings.js`
  - `finsim/finsim/terminal/static/components/chain.js`
  - `finsim/finsim/terminal/static/components/chart.js`
  - `finsim/finsim/terminal/static/terminal.css`
  - `finsim/tests/test_terminal_static.py`
  - `finsim/tests/test_terminal_ui_ops.py`
  - `finsim/tests/terminal_fixture_server.py`
  - `finsim/tests/fixtures/terminal/api/`

FUT-05 (listing depth and the curve):
- api_products: active_months / roll_date read FO-1's next_active_month and roll_date (NOTICE_GROUPS, NOTICE_ROLL_D and EXPIRY_WEEK_D go;
  USX-26's picker keeps working); FUTURES_GROUP_LABELS read tables.SESSIONS labels (the new groups included).
- GET /api/futures/curve?root= (register in api_products): QuoteWorker.request_curve(root) (202 + retry_ms while fetching) -> per listed
  month: month, last (quote_time), prior settlement (FO-2 prior_settlement: official or provisional, with as-of), change against it,
  spread to the front month, support (LIMITED months say "not quoted by the free feed yet"); SR3 / SR1 / ZQ add the implied rate (100 -
  price) next to the NY Fed SOFR / EFFR fixing with its date.
- markets.js: a "curve" expander on each root's row of the Futures tab rendering that table (fits 1100 px), refreshed at most once a
  minute while open.

FUT-09 (contract data on screen):
- api_products: EXTRA_MODULES gains FUTURE: ("futures",), so the instrument page shows FO-2's spec_view (P10's USX-33 renders it); the
  Futures-tab rows add month, last trade date and days left, close-out (physical) with business days left, prior settlement and the
  change against it, contract value (last x multiplier), cash / physical, tick value and the weekly CFTC open interest ("OI all months,
  weekly, CFTC as of 2026-10-13"), from the master and FO-2 (no extra fetch); compact columns with the rest on hover, fitting 1440 px
  with the dock open.
- api_views position rows for a FUTURE carry close_out, bd_left, last_trade, settlement and "% acct NAV" on notional (C24's notional:
  the 0.0000 weight goes); positions.js shows them with Close / Roll (T.ticket.roll) / Hedge.

