# FinSim map part: live and real data

Author: mapping subagent, 2026-10-20 (read-only pass; nothing was run, no network, no git).
Scope: `finsim/engines/live.py`, `realfeed.py`, `realmacro.py`, `realnews.py` and where they are wired in
(`finsim/world.py`, `finsim/api/service.py`, `finsim/api/server.py`, `finsim/engines/market.py`,
`finsim/engines/simulation.py`, `finsim/engines/fx.py`, `finsim/clock.py`); the Desk app's data layer
(`finsim2/data/*`, `finsim2/hedge/market.py`), read for what already works on this machine; the game modules
at a high level. Paths are relative to `C:\Users\logan\intelligence-terminal\finsim\` unless they start with
`docs/` (that one is the repo root `C:\Users\logan\intelligence-terminal\docs\`).
Each claim is tagged **VERIFIED** (I read the code, path:line) or **INFERRED** (my reading, not checked line by line).

---------------------------------------------------------------------------------------------------------------

## 0. Short answers (read this first)

**Does FinSim already have a live / real-price mode?** Yes, partly. Every save has a `market_source` of
`SIMULATED` or `REAL`. A career save (`clock_mode REAL_TIME`) is `REAL` by default and a sandbox save is
`SIMULATED` by default (VERIFIED `finsim/api/service.py:102`). A simulated save can be switched to REAL later
(`switch_to_real`, VERIFIED `service.py:194-216`, `world.py:293-311`). But REAL mode is a **hybrid**. It is
not "no simulation":

1. **The simulator still produces every day.** `SimulationEngine.run_daily_process(d)` calls
   `w.market.generate_day(d)` for REAL saves too (VERIFIED `finsim/engines/simulation.py:41`). `generate_day`
   draws a seeded random day: regime switching, `macro.step`, lending, dealers, the vol surface and commodity
   curves (VERIFIED `finsim/engines/market.py:877-899, 1055-1057, 1081-1084`). Only after that does it rescale
   the simulated bars so that each close equals the real Yahoo close (`_pin_day`, VERIFIED
   `market.py:1059-1062, 581-657`). Open, high, low, bid and ask are the simulator's numbers times the same
   factor, and volume is the simulator's own number (VERIFIED `market.py:587-594`). So a REAL day has made-up
   intraday ranges and made-up volume around a real close.
2. **Real closes reach a save only through the day advance.** A REAL save cannot process day `d` until the real
   session has closed (16:00 NY) and Yahoo has a SPY close for `d`. `real_market_ready` enforces this
   (VERIFIED `world.py:342-363`), and both `advance` (VERIFIED `world.py:1086`) and `catch_up` (VERIFIED
   `world.py:1107`) call it.
3. **Between day advances there is a live overlay** (`LiveDesk`, `finsim/engines/live.py`):
   - Yahoo's spark `regularMarketPrice` (labelled "up to 15 minutes delayed") is shown next to the last close.
   - A `LIVE` ticket fills at once at that price through the ordinary fill logic (VERIFIED `live.py:1-22,
     302-312, 388-457`).
   - Resting live orders are checked on every quote refresh and on the server's 60-second scheduler tick
     (VERIFIED `service.py:170-192, 225-240`; `server.py:423-427`).
   - `NEXT_UPDATE` instructions are worked at 15-minute quote ticks from 09:45 to 16:15 NY (VERIFIED
     `world.py:786-787, 806-819`; `live.py:314-367`; `clock.py:90-92`).
4. **Positions are re-marked only at the daily close.** `LiveDesk` never writes a mark. For a live fill it calls
   only `trading._fill`, `options.recompute_margin` and `futures.sweep_margin` (VERIFIED `live.py:451-456`).
   Marks, P&L, NAV, risk, settlement and accruals all run in `run_daily_process` (VERIFIED
   `simulation.py:98-131`). So the server keeps no live P&L or NAV; the page shows only quote-vs-last-close
   changes (INFERRED from `service.live` returning quotes only, VERIFIED `service.py:170-192`).
5. **In REAL mode many asset classes are still model-priced off the simulated state**:
   - Listed options use the simulator's model vol surface, repriced at the live underlying (VERIFIED
     `live.py:206-215`; surface stepped at `market.py:1081-1084`).
   - Bonds use a 3-point Nelson-Siegel fit to `^IRX`/`^TNX`/`^TYX` plus the simulator's IG/HY spreads (VERIFIED
     `market.py:624-637, 660-682`).
   - Futures are the simulated contract close scaled by a reference ratio (VERIFIED `live.py:216-244`).
   - FX forward carry and foreign curves come from the FX model with FRED policy rates pinned in (VERIFIED
     `market.py:1006-1008, 1296-1313, 1151-1170`).
   - The OTC, private credit, PE and IB engines are simulated (INFERRED; see section 6). PE and IB refuse
     commands in REAL saves (VERIFIED `investment_banking.py:197-198`, `private_equity.py:297-298`).

**What "current-day live prices, no simulation, fake cash" needs (summary).** The live quote layer (`RealFeed.live`
plus a slimmed `LiveDesk._quote_from`) becomes the only price source. Positions are marked from it on demand. The
ledger is kept as the record of fills, cash, borrow and interest. `generate_day`, `_pin_day`, `apply_real_history`
and the seeded engines are retired as price sources. Section 5 gives, for each asset class, the $0 source, its
delay and limits, and what must be labelled "computed" instead.

---------------------------------------------------------------------------------------------------------------

## 1. Files and what each does

### 1a. FinSim live/real modules (standard library only)

| File | Lines | What it does |
|---|---|---|
| `finsim/engines/realfeed.py` | 264 | `RealFeed`: Yahoo daily closes plus the latest quote, with a disk cache. **Endpoint** `https://query2.finance.yahoo.com/v7/finance/spark` (VERIFIED :26), UA `"Mozilla/5.0"` (VERIFIED :25), `BATCH = 20` symbols per request ("Yahoo answers 400 to bigger spark batches", VERIFIED :27). **Symbol maps:** `RATE_SYMBOLS` ^IRX/^FVX/^TNX/^TYX (:28); `COMMODITY_SYMBOLS`, 23 continuous front months `CL=F`…`TIO=F` (:29-30); `CENTS_QUOTED` (:31); `FX_SYMBOLS`, 25 currencies `EURUSD=X`, `JPY=X`… with an invert flag (:32-35); `INDEX_SYMBOLS` DXY/VIX/SX5E/DAX/UKX/NKY/HSI (:36); `YIELD_SYMBOLS` {0.25: ^IRX, 5: ^FVX, 10: ^TNX, 30: ^TYX} (:37). **Cache:** `$FINSIM_HOME/realfeed-cache.json`, else `~/.finsim/realfeed-cache.json` (VERIFIED :40-41, :50). |
| `finsim/engines/live.py` | 457 | `LiveDesk`: live quotes per instrument, live fills, and sweeps of resting live orders and 15-minute instructions. Source label `"Yahoo Finance (up to 15 minutes delayed)"` (VERIFIED :37). Treasury-future references `ZT=F/ZF=F/ZN=F/ZB=F` (VERIFIED :40). `EQUITY_CLASSES` = EQUITY, ETF, ADR, REIT, PREFERRED, CRYPTO (VERIFIED :41). |
| `finsim/engines/realmacro.py` | 355 | `RealMacro`: FRED series through the public CSV endpoint `https://fred.stlouisfed.org/graph/fredgraph.csv?id=…`, no key (VERIFIED :25), UA `"FinSim/1.0 (local simulator; standard library)"` (VERIFIED :42). **Series:** CPI, core CPI, UNRATE, PAYEMS, GDP, DFEDTARU/L, RSAFS, PCEPILFE and housing (VERIFIED :26-29); 20 foreign policy rates (ECBDFR + OECD `IRSTCI01xxM156N`, monthly, VERIFIED :33-36); HKD/SAR pegs (:37); five 10-year yields (OECD `IRLTLT01xxM156N`, monthly, :40). **Calendars:** hard-coded FOMC dates for 2025-2026, falling back to the third Wednesday (VERIFIED :44-46, :72-75); approximate release calendar (VERIFIED :78-96). **Pure views:** `state_as_of`, `releases_on`, `policy_rates_as_of`, `yield_anchors_as_of`, `upcoming`, `housing_as_of` (:191-355). |
| `finsim/engines/realnews.py` | 221 | `RealNews`: Yahoo news search `https://query2.finance.yahoo.com/v1/finance/search` (VERIFIED :21) and the Federal Reserve press RSS `https://www.federalreserve.gov/feeds/press_all.xml` (VERIFIED :22), both fetched with UA `"Mozilla/5.0"` (VERIFIED :23, :28-29). `NewspaperNews` reads this repo's own `site/reports/index.json` and the edition HTML from disk (`FINSIM_NEWSPAPER` env var or the repo path) (VERIFIED :72-83, :126-162). |

### 1b. Wiring

| File | Lines | Role |
|---|---|---|
| `finsim/world.py` | 68, 78 | `self.real_news = None`, `self.live = live.LiveDesk(self)` (VERIFIED) |
| | 209-210 | handlers for `MARKET_SOURCE_CHANGED` and `REAL_MACRO_LOADED` (VERIFIED) |
| | 275-278, 280-286 | WORLD_CREATED handler reads `market_source` → `_setup_real_feeds()` builds `RealFeed`, `RealMacro`, `RealNews` (VERIFIED) |
| | 293-311 | `switch_to_real(real_history, real_macro)` emits MARKET_SOURCE_CHANGED, REAL_HISTORY_LOADED, REAL_MACRO_LOADED (VERIFIED) |
| | 313-316, 318-336 | `_h_real_history` → `market.apply_real_history`; `_fix_quote_scale` (pence→pounds repair for pre-2026-09-24 saves) (VERIFIED) |
| | 342-363 | `real_market_ready(d)`: session closed? real targets? else `feed.refresh("1mo")` + `real_macro_feed.refresh()` (VERIFIED) |
| | 662-705 | `World.create` REAL branch: fetches 1y if no history was given, requires a SPY close on the start date, emits REAL_HISTORY_LOADED / REAL_MACRO_LOADED, then `run_daily_process(start_date)` (VERIFIED) |
| | 775-796 | `place_order(... execution="NEXT_UPDATE"|"LIVE")`: LIVE needs `live.available()`; NEXT_UPDATE gets `executes_at = next_quote_tick` when ticks are active; LIVE calls `self.live.fill` (VERIFIED) |
| | 798-819 | `work_live`, `quote_ticks_active`, `quote_updates` (VERIFIED) |
| | 1079-1115 | `advance(days)` (sandbox; career raises unless forced) and `catch_up(at)` (career) both gate each day on `real_market_ready` (VERIFIED) |
| | 1139-1142 | REAL saves must update after 16:00 NY (VERIFIED) |
| `finsim/api/service.py` | 83-155 | `create_world`: REAL → `RealFeed.refresh("1y", force=True)`, `history(cal_sd-420d, latest)`, `RealMacro.refresh()` + `snapshot(cal_sd-800d)` (VERIFIED :116-138) |
| | 170-192 | `live(world_id, ids)`: works the live book if anything is pending, then `w.live.quotes(ids)` (up to 250 ids; default SPY, QQQ, IWM + every held security) (VERIFIED) |
| | 194-216, 218-240 | `switch_to_real`, `work_live`, `work_live_loaded` (the scheduler's live sweep) (VERIFIED) |
| | 242-273 | `catch_up_all`, `catch_up_one`, `world()` (loads a save by replaying its events) (VERIFIED) |
| | 296-306 | `world_info` exposes `market_source`, `live`, `quote_updates`, `made_up_desks`, `real_market` (the `waiting` field calls `real_market_ready`, which can hit the network) (VERIFIED) |
| | 1330-1353, 1532, 1638-1650, 1967 | housing page (refreshes FRED if housing series are missing), FX page source label, ticket preview with a LIVE quote, macro page source label (VERIFIED) |
| `finsim/api/server.py` | 102-106 | routes `live`, `work_live`, `switch_to_real` (VERIFIED) |
| | 393-453, 467 | `Scheduler`: every 60 s, `catch_up_one` for **every stored world**, then `work_live_loaded()`. `serve()` always starts it (VERIFIED :467) |
| `finsim/app.py` | 1-16, 41, 56 | The desktop installer and launcher (a background service; 127.0.0.1 only). `FINSIM_HOME` (default `~/.finsim`) holds `finsim.db` and `server.log`, and is also where `realfeed-cache.json` and `realmacro-cache.json` go. No direct live or real-data hooks (VERIFIED by grep: no RealFeed, LiveDesk or market_source references in app.py) |
| `finsim/engines/market.py` | 403-406 | `real_history`, `real_feed`, `real_macro`, `real_macro_feed` attributes (VERIFIED) |
| | 563-569 | `real_targets(d)`: stored history first, then `real_feed.targets_for(d)` (VERIFIED) |
| | 581-657 | `_pin_day`: scales equities, commodities (and their futures/physicals) and FX to the targets; rebuilds the curve from rates and reprices bonds; moves financial futures with their source (VERIFIED) |
| | 660-682 | `_curve_params`: Nelson-Siegel (tau 2.0) solved to the 13-week bill, 10y and 30y; slope and curvature clamped to ±3% (VERIFIED) |
| | 684-798 | `apply_real_history`: overlays the real year on the generated prehistory (equities, commodities, FX, curves, bonds, financial futures) (VERIFIED) |
| | 873-1087 | `generate_day`: the simulator; REAL branches at 900-910 (macro pin, no invented corporate events), 1006-1008 (FX policy-rate pin), 1058 (sim news dropped), 1059-1062 (pin), 1076-1077 (real VIX) (VERIFIED) |
| | 1090-1137 | `ingest_close` (replay uses the stored bars) (VERIFIED) |
| | 1151-1182, 1286-1344 | foreign curves anchored to FRED 10y; `real_macro_series`; `_pin_fx_rates`; `_pin_real_macro` (VERIFIED) |
| `finsim/engines/simulation.py` | 34-143 | `run_daily_process(d)`: the whole daily cycle (section 4, F16); real headlines at 57-76 (VERIFIED) |
| `finsim/engines/fx.py` | 69-125 | spot deals `execution="LIVE"` use `w.live.fx_mid` (crosses through USD) (VERIFIED) |
| `finsim/clock.py` | 71-126 | `MARKET_TZ`, `SESSION_FINAL` 16:00, `session_closed`, quote ticks 09:45-16:15 every 15 min (VERIFIED) |
| `finsim/engines/macro.py` | 95, 126 | uses `realmacro.fomc_dates` and `realmacro.upcoming` (VERIFIED by grep only) |

### 1c. Desk (finsim2) data layer: what already works on this machine (read only)

| File | What it does | Hosts / UA / limits |
|---|---|---|
| `finsim2/data/__init__.py` | Shared HTTP plumbing (`http_get`: gzip, 64 MB cap, error messages without URLs so keys cannot leak, request-budget "park" for GETs), `env_key(name)` (rejects placeholders and never logs the value), `num()` (finite numbers only) | `GOV_UA = "LoganTerminal/1.0 (loganshaffer87@gmail.com)"` for every .gov request (VERIFIED :18-19); `BROWSER_UA` (Chrome 124 string) "for Yahoo Finance only" (VERIFIED :20-22) |
| `finsim2/data/yahoo.py` | Daily history via **v8 chart** `…/v8/finance/chart/{sym}?period1&period2&interval=1d&events=div,split` | Hosts query1 then query2 (VERIFIED :15); retries 429/5xx up to 3 per host, backoff 1.5 s doubling (VERIFIED :16-18, :58-80); BROWSER_UA (:26) |
| `finsim2/data/refresh.py` | Orchestrates stale downloads into the SQLite research store (docstring :1-19):<br>• Yahoo bars: incremental with a 7-day overlap; a full re-download if past closes changed, i.e. a split or dividend re-adjustment (VERIFIED :120-154); a partial live bar is expected (:141)<br>• FRED: first releases with `FRED_API_KEY`, else the latest vintage, 30-day overlap (VERIFIED :168-191)<br>• **Synthetic TREASURY/CORP_BOND total-return indices** from a FRED yield: `r_t = y_{t-1}·n/252 − D·dy + ½·C·dy²` (VERIFIED :65-100). This is the Desk's existing way of computing a bond proxy from a yield<br>• SEC companyfacts at most weekly (VERIFIED :194-226) | `pause=0.25` s between Yahoo requests (VERIFIED :230-239, :284-289) |
| `finsim2/data/store.py` | SQLite research store, WAL (VERIFIED :194-197); path `$FINSIM2_RESEARCH_DB`, else `$FINSIM2_HOME/research.db`, else `~/.finsim2/research.db` (VERIFIED :107-111). Tables and methods (VERIFIED by the def list): daily prices (`upsert_prices`, `prices`, `last_price_date` :320-353); macro series with their kind (`upsert_macro`, `macro_kind` :376-437); fundamentals (:441-451); alt datasets (`put_alt`, `alt` :492-506); news (:510-558); fetch log (:588-599); predictions, model runs and backtests (:616-687); its own portfolios and **transactions** (`add_transaction(s)` :693-736); **option quotes** (`upsert_option_quotes`, `option_chain(underlying, asof, max_age_days=7)` :746-752) | — (daily, end-of-day data; nothing intraday) |
| `finsim2/data/fred.py` | FRED CSV `fred.stlouisfed.org/graph/fredgraph.csv` and API `api.stlouisfed.org/fred/series/observations` (+ vintagedates) | GOV_UA (VERIFIED :31-33, :49); key env `FRED_API_KEY` (:151, :252); retry base 2 s, 429 back-off (:40, :225) |
| `finsim2/data/eulerpool.py` | Eulerpool analyst / fundamentals probe and refresh (research only) | `https://api.eulerpool.com` (VERIFIED :36); env `EULERPOOL_API_KEY` (VERIFIED :35); `MIN_INTERVAL = 0.25` s (:43); honest UA `"FinSim2/1.0 (personal research client; python)"` (:46) |
| `finsim2/data/cboe.py` | Cboe VIX-family history CSVs and **daily option-chain snapshots** from `https://cdn.cboe.com/api/global/delayed_quotes/options/{SYM}.json` (VERIFIED :43-44), "delayed ~15 minutes"; stores features only (iv30/60/90, skew25, volume, OI), not the chain (VERIFIED :1-26) | `MIN_INTERVAL = 1.0` s (:48); **uses BROWSER_UA** (VERIFIED :41, :65) |
| `finsim2/data/futcurve.py` | Commodity futures curve contracts 1-4 from Yahoo per-contract symbols (`CLX26.NYM`, `ZCZ26.CBT`, `GCZ26.CMX`) and EIA history (ends 2024-04-05) | Yahoo v8 chart `range=5d` (VERIFIED :29); `MIN_INTERVAL = 0.4` s (:31); env `EIA_API_KEY` (:6) |
| other feeds (grep only) | Finnhub (`FINNHUB_KEY`, `MIN_INTERVAL 1.05` s, 61 s wait on limit: `analyst.py:30-32, 132`); CFTC (`publicreporting.cftc.gov`, GOV_UA, 0.5 s); FINRA Reg SHO (`cdn.finra.org`, 0.35 s); SEC (`data.sec.gov`, `MIN_INTERVAL 0.12` s ≤10/s); EIA (`api.eia.gov`, `EIA_API_KEY`); Deribit/Binance funding (`cryptoderiv.py:28-30`); Dow Jones RSS (`news.py:48`) | VERIFIED by grep (lines cited) |
| `finsim2/hedge/market.py` | Point-in-time market inputs for hedging: Treasury CMT curve from FRED DGS1MO…DGS30 (VERIFIED :16-17); implied vol from Cboe vol indices via FRED (VIXCLS, VXVCLS, VXNCLS, RVXCLS, GVZCLS, OVXCLS, single-name VXAPLCLS…) (VERIFIED :19-29); short rates by currency (DGS3MO, ECBDFR, IUDSOIA, OECD) (VERIFIED :31-32); staleness limits: price 5 sessions, daily macro 10 days, monthly 100 days, older returns None and is never guessed (VERIFIED :34, :54-69); Corwin-Schultz spread from highs/lows (VERIFIED :176-197) | No network itself; reads the research store |

**Allowed domains** (repo `docs/RUNBOOK.md:31-66`, VERIFIED). The list includes:
- prices: `query1/query2.finance.yahoo.com`, `cdn.cboe.com`, `api.twelvedata.com`, `api.frankfurter.dev`, `api.coingecko.com`, `api.polygon.io`, `api.nasdaq.com`, `www.nyse.com`, `finnhub.io`, `www.alphavantage.co`;
- government and rates: `fred.stlouisfed.org`, `api.stlouisfed.org`, `home.treasury.gov`, `api.fiscaldata.treasury.gov`, `www.treasurydirect.gov`, `www.federalreserve.gov`, `www.bls.gov`, `apps.bea.gov`, `www.census.gov`, `cdn.finra.org`;
- SEC: `www.sec.gov`, `data.sec.gov`, `efts.sec.gov`.

`api.eia.gov`, `api.eulerpool.com`, Deribit and Binance are **not** on that list (VERIFIED by reading :31-66). The runbook says `cdn.cboe.com` stays listed "for VIX quotes and history" and that the options market-statistics path is retired after constant 403s (VERIFIED :73-74). Key variable names in the runbook: `FRED_API_KEY`, `TWELVE_DATA_KEY`, `FINNHUB_KEY`, `ALPHA_VANTAGE_KEY`, `COINGECKO_KEY` (VERIFIED :82-86). No FinSim or Desk file uses Stooq (VERIFIED: grep for "stooq" over `finsim/` and `finsim2/` finds nothing).

---------------------------------------------------------------------------------------------------------------

## 2. Key functions and data structures (signatures)

### realfeed.py
- `RealFeed.__init__(self, equity_symbols: Dict[str,str], cache_path=None, fetch=None, fetch_live=None, scales=None)`
  (VERIFIED :47-56). State:
  - `equities` {security id → Yahoo symbol}
  - `scales` {id → quote scale; London pence → pounds}
  - `data` {symbol → {iso date → close}}
  - `fetched_at` {symbol → epoch}
  - `live_cache` {symbol → quote + "fetched"} (memory only)
- `_spark_results(symbols, rng, interval) -> List[Dict]` (VERIFIED :78-114):
  - 20 symbols per request; 30 s timeout; 4 attempts with sleeps of 2, 4 and 6 s.
  - A 400/404 on a batch is split in halves recursively so the good symbols survive; a single bad symbol is skipped.
  - Waits 0.5 s between history batches and 0.2 s between live batches.
  - There is no special handling of 429: it is retried like any other error, then raised (VERIFIED :91-108).
- `_spark(symbols, rng) -> {symbol: {date: close}}`: daily closes, dated with the exchange `gmtoffset` (VERIFIED :116-133).
- `_spark_live(symbols) -> {symbol: {price, time, prev_close, high, low, volume, currency, exchange}}` from the spark
  `meta` (`regularMarketPrice`, `regularMarketTime`, `chartPreviousClose`, …) (VERIFIED :135-149).
- `live(symbols, max_age_s=60.0)`: refetches anything older than `max_age_s`; a symbol Yahoo does not quote is
  cached as `price: None` so it is not retried on every call (VERIFIED :151-163).
- `refresh(rng="1mo", max_age_s=1800.0, force=False)`: refetches stale symbols across `all_symbols()` (equities, rates,
  commodities, FX; indices come in through `equities`) and writes the disk cache (VERIFIED :165-180).
- `latest_date()`, `has(d)` (both keyed on SPY), `_value(sym, d, carry=True)`.
- `targets_for(d) -> {date, equities, commodities, fx, rates} | None` (VERIFIED :202-219).
- `history(start, end)` builds the block stored in a save (VERIFIED :221-230).
- `targets_from_history(h, d)` (static) (VERIFIED :232-248).
- `equity_symbols_for(securities)` (Yahoo symbol = `sec.yahoo` or the id; crypto `{id}-USD`; indices that have a
  symbol) and `equity_scales_for(securities)` (VERIFIED :251-264).

### live.py (`class LiveDesk`, VERIFIED :59-457)
- `available() -> bool`: `market_source == "REAL"` and `market.real_feed` is set (:65-67).
- `_symbols_for(sec) -> List[str]`: which Yahoo symbols a security's quote needs (:76-96). Bonds need all four yield
  symbols; options need the underlying (or its index source); futures need the commodity front month or the
  financial reference.
- `quotes(ids, max_age_s=60.0) -> {id: quote}`: one batched fetch; also handles `SPOT:<code>` ids (:98-126).
- `live_curve(raw) -> (YieldCurve|None, {tenor: shift})`: the save's curve shifted tenor by tenor to the live yields,
  linear between the four tenors and flat outside them (:128-153).
- `quote(sec, max_age_s=60.0)` and `_quote_from(sec, raw) -> quote | None` (:155-252).
  - Quote dict: `{id, last_close, last_close_date, source, price, time, time_ny, age_s, change_pct, method, ref, ref_price, …}`.
  - `method` takes these values:
    - `direct`: equities and indices.
    - `curve`: bonds, priced as `last + (P(live curve) − P(save curve))`.
    - `underlying`: options, the model quote at the live underlying, with bid/ask/iv/delta.
    - `ratio`: futures, priced as `last × live_ref / ref_close`, rounded to the tick.
    - `front_month`: `SPOT:` ids.
    - `last_close`: bonds when no yields are available.
- `fx_mid(buy, sell) -> {mid, time, time_ny, source, usd} | None` (:254-273).
- `resting()`: LIVE orders still WORKING or PARTIALLY_FILLED, without a condition and not part of a strategy (:278-286).
  `instructions()`: non-LIVE working orders (:288-296). `pending()` (:298-300).
- `fill(order, cause) -> Trade|None` (:302-312). `work(max_age_s=60.0, at=None) -> {checked, instructions, quoted,
  filled, triggered, ratcheted, asof, tick, next_tick}` (:314-367). `_condition_met(...)` (:369-386).
  `_work_one(order, q, cause, tick=None)` (:388-457).
- `last_tick` is kept in memory only (:62).

### realmacro.py
- `RealMacro.__init__(cache_path=None, fetch=None)`.
- `refresh(max_age_s=6*3600, force=False, budget_s=90)`: after a failure it waits 15 minutes before retrying; it
  stops at the first non-HTTP failure (VERIFIED :151-172).
- `snapshot(since=None)`: two years by default (:174-177).
- Pure functions: `release_date(kind, period, roll)`, `state_as_of(series, asof, roll)`, `releases_on(series, d, roll)`,
  `policy_rates_as_of(series, asof)`, `yield_anchors_as_of(series, asof)`, `upcoming(series, d, roll, days=45)`,
  `housing_as_of(series, asof, roll)` (VERIFIED :78-355).
- Releases carry `consensus: None` and zero surprise and zero shock (VERIFIED :244-276), so in a REAL save releases
  move nothing.

### realnews.py
- `RealNews.headlines_for(d, tickers, known_links=None, max_items=30)`: returns the newspaper items first, then
  Yahoo market and company news and the Fed RSS for the NY date `d`, newest first, deduplicated (VERIFIED :173-217).
- `NewspaperNews.headlines_for(d, max_per_edition=24)` (VERIFIED :145-162). `parse_newspaper(html, link_base, when_iso)`
  parses the HTML with regexes (VERIFIED :100-123).

### Events (types used by this layer)
- `WORLD_CREATED` (payload `market_source`), `MARKET_SOURCE_CHANGED`, `REAL_HISTORY_LOADED` (payload `history` = the
  whole `RealFeed.history` block: about 420 days of closes for every symbol), `REAL_MACRO_LOADED` (payload `series`:
  800 days of FRED series) (VERIFIED `world.py:670-698, 306-309`; `service.py:131, 138`).
- `MARKET_CLOSE`: every bar of the day, plus curve, state, commodities, lending, FX, vol, dealers, macro and corporate
  events (VERIFIED `simulation.py:43-49`).
- `LIVE_SWEEP`: order ids, tick and quotes (VERIFIED `live.py:345-346`).
- `TRADE_EXECUTED`: carries the quote time, source, method and ref (VERIFIED `live.py:446-451`).
- `ORDER_STATUS_CHANGED` (VERIFIED `live.py:418-433`) and `NEWS_PUBLISHED`.
- Order fields this layer reads or writes: `execution` (`LIVE` / `NEXT_UPDATE`), `executes_at`, `last_tick`,
  `condition`, `condition_met_date`, `triggered`, `trail_level`, `trail_pct`.

---------------------------------------------------------------------------------------------------------------

## 3. Data flow

**Creating a career (REAL) save.** All steps VERIFIED (`service.py:116-145`; `world.py:662-705`).
1. `POST /worlds` → `Service.create_world`.
2. `build_universe(today, seed)` → `RealFeed(...).refresh("1y", force=True)`.
   - The universe is the static snapshot `finsim/data/universe.json` (518 KB, `as_of` 2026-09-18, built by
     `tools/refresh_universe.py` from Yahoo and SEC EDGAR; VERIFIED `market.py:111-117`).
   - Its equity-like securities: 550 EQUITY, 136 ETF, 79 ADR, 28 REIT, 5 PREFERRED and 29 CRYPTO, so **827 equity-like
     Yahoo symbols** (VERIFIED by counting `asset_class` in universe.json).
   - Add 4 rate, 23 commodity and 25 FX symbols, plus up to 7 indices: about **886 symbols** in total.
   - At 20 per request that is about 45 spark requests, with 0.5 s sleeps between batches (INFERRED arithmetic from
     `realfeed.py:27, 113`).
3. `history(cal_sd-420d, latest)`, then `RealMacro.refresh()` and `snapshot(cal_sd-800d)`.
4. `World.create(...)` emits WORLD_CREATED, then REAL_HISTORY_LOADED (handled by `apply_real_history`), then
   REAL_MACRO_LOADED.
5. `current_date = prev_business_day(start)` and `run_daily_process(start)`, which runs the simulator and pins the
   real close.

**The daily advance, career saves.** Steps 1-4 are VERIFIED (`server.py:405-427`; `service.py:251-256`;
`world.py:1099-1115`; `simulation.py:34-139`; `market.py:873-1087`).
1. `Scheduler.tick` (every 60 s) → for every stored world, `catch_up_one` → `World.catch_up(at)`.
2. While `current_date < target_sim_date`, it calls `real_market_ready(next)`:
   - session closed?
   - `real_targets` from history or the feed?
   - otherwise `feed.refresh("1mo")` (cache max age 30 min).
3. `advance_one_day` → `run_daily_process(d)`:
   - `generate_day(d)`: the seeded simulation, `_pin_real_macro`, `_pin_fx_rates`, `_pin_day`.
   - MARKET_CLOSE.
   - real headlines.
   - client and AI desks.
   - `trading.work_orders` (instructions meet the session).
   - settlement, corporate actions, futures, financing desks, FX, OTC, options.
   - `pnl.mark_all` (twice).
   - prime, accruals, treasury, investors.
   - careers, `pnl.snapshot`, risk, `expire_day_orders`, missions, reviews, briefing.
4. Then `work_live_loaded()` sweeps resting live orders.
5. Sandbox (simulated) saves move only with `advance`: `Service.advance(world_id, days)` → `World.advance(days)`
   (VERIFIED `service.py:404-407`). With `force=False` a career save refuses (VERIFIED `world.py:1080-1082`). This
   is the owner's "advance day" button (the front-end binding was not read).

**Price generation inside `MarketEngine.generate_day(d)`** (the machinery to be replaced; all steps VERIFIED
`finsim/engines/market.py`):
1. A seeded RNG per date: `random.Random(f"{seed}|day|{d}")` (:877). The regime switches monthly through
   `_regime_for_month` (:886-890); `R = REGIMES[regime]` (:891).
2. `macro.step(...)` (:897): releases, earnings, ratings and defaults. REAL saves then call `_pin_real_macro`
   (:900-902). Simulated corporate events (`cevents.step`) run only in SIMULATED saves (:904-909). Earnings and
   default jumps are added to the per-security returns (:911-916), as are forced returns and rate shocks (:917-923).
3. The market factor `mkt` and sector factors from Gaussian draws (:926-928).
4. Nelson-Siegel level, slope and curvature random walks, with the level pulled toward the policy rate (:931-940).
   IG and HY spreads mean-revert and widen when the market falls (:942-945). The curve is built at :947-949, with
   `policy_rate = NS(0.08y)`.
5. Bars, for each security (:955-1000):
   - defaulted bonds sit at recovery;
   - bonds are the clean price off the curve, with half-spread `spread_bps × R.spread_mult`;
   - equity-like securities: `r = beta·mkt + sector + idio`, clamped to [-45%, +35%], plus jumps; close = prev ·
     e^r − dividend; a random gap for the open; random high and low ranges; volume = ADV × a regime/shock
     multiplier × lognormal noise; bid/ask = close ± `spread_bps × R.spread_mult / 2`.
6. `derive_indices` (:1002). `ensure_listings` (:1004) lists new option and future months. `fx.step(...)` (:1006),
   then `_pin_fx_rates` in REAL saves (:1007-1008). DXY is the basket and the VIX is a regime/realised blend
   (:1009-1015).
7. Financial-future inputs: spot S, carry q and, for non-USD, r (:1016-1034). `commodities.step(...)` (:1035).
   Contract bars come from `bars_for_contracts` with seeded shapes (:1036-1043). Physical inventory is priced at
   spot ± spread (:1045-1051).
8. `lending.step` and `dealers.step` (:1055-1057). The news list is set (:1058). **REAL pin: `_pin_day(…,
   real_targets(d))`** (:1059-1062); otherwise on the start date `_anchor_start_day` pins to the seed snapshot
   (:1063-1065).
9. `mbs.step` and `reset_floaters` off the final curve (:1068-1069). State, history and curves are appended
   (:1071-1074). The VIX is taken from the pinned bar (:1076-1077). `vol.step` runs for every underlying, giving
   the **model implied-vol surface** (:1081-1084).

Replay does not regenerate any of this: `ingest_close` adopts the stored MARKET_CLOSE payload (:1090-1137).
Everything that reads `market.history[-1]`, `market.curve()`, `fx.spot`, `commodities.state`, `vol.state` or
`macro.state` depends on this pipeline. That includes `trading._bar` (VERIFIED `trading.py:232-238`), which every
fill uses, and `LiveDesk._quote_from`, which uses the last bar as `last_close` (VERIFIED `live.py:166-171`).

**Live quotes and fills.** Both paths VERIFIED (`service.py:170-192`; `world.py:775-796`; `live.py:302-312, 388-457`).
- Quote path: `GET /worlds/{id}/live?ids=…` → `Service.live` → `w.work_live` if pending → `LiveDesk.quotes(ids)`
  → `RealFeed.live` (60 s memory cache) → `_quote_from` for each security.
- Fill path:
  1. A LIVE ticket → `World.place_order(execution="LIVE")` → `trading.enter_order`.
  2. `live.fill` → `quote(sec)` → `_work_one`.
  3. `_work_one` builds a synthetic `Bar(open=high=low=close=live, volume=last bar volume, bid/ask = live ± half)`,
     where `half` is the last close's relative half-spread times live, or the model bid/ask for options.
  4. `trading._fill(order, sec, bar, base, half, "LIVE"|"QUOTE_UPDATE", cause, note, extra_detail)` → TRADE_EXECUTED.
  5. Futures and options then get `recompute_margin` / `sweep_margin`.

**Replay.** All steps VERIFIED (`world.py:707-719, 275-278, 313-316`; `market.py:1090-1137`).
- `World.load` → `migrate` → `replay(events)`.
- WORLD_CREATED → `_setup_real_feeds()`. This constructs a `RealFeed`, which **reads the whole disk cache file** at
  construction, plus a `RealMacro` and a `RealNews`.
- REAL_HISTORY_LOADED → `apply_real_history` (recomputed deterministically).
- Each MARKET_CLOSE → `ingest_close` (stored bars; no network).

**State that is read and written.**
- Network caches on disk: `~/.finsim/realfeed-cache.json` and `~/.finsim/realmacro-cache.json`.
- Save events in the store: REAL_HISTORY_LOADED and REAL_MACRO_LOADED (large), MARKET_CLOSE (all bars every day),
  TRADE_EXECUTED (fill + quote).
- Memory only: `RealFeed.live_cache`, `LiveDesk.last_tick`, `scheduler_state`.

---------------------------------------------------------------------------------------------------------------

## 4. Findings for the repair

**F1. REAL mode still simulates; only the close is real.** VERIFIED `market.py:877-899` (seeded RNG, regime, macro
step), `1055-1057` (lending and dealer steps), `1081-1084` (vol surface step), `1059-1062` (pin). Open, high, low,
bid, ask and volume are simulated and scaled (VERIFIED `market.py:587-594`). The live-only rebuild must not keep
`generate_day` as the price source.

**F2. Positions are not marked at live prices.** The only marks are `pnl.mark_all` inside `run_daily_process`
(VERIFIED `simulation.py:120, 128`). `LiveDesk` writes fills only (VERIFIED `live.py:451-456`). For "current-day
live prices", NAV, P&L, the balance sheet and risk need an on-demand mark from live quotes, done once in the single
accounting module.

**F3. Live fills borrow the last simulated bar's microstructure.**
- The half-spread is the last bar's `(ask-bid)/2/close` applied to the live price (VERIFIED `live.py:405-407`).
- Volume for the participation cap comes from the last (simulated, scaled) bar (VERIFIED `live.py:408`).
- The service ticket preview does the same spread calculation separately (VERIFIED `service.py:1643-1648`). That is
  a **duplicate computation** of the live bid/ask.
- Yahoo spark meta gives `regularMarketVolume`, day high and day low, but no bid or ask (VERIFIED
  `realfeed.py:146-148`).
- A live rebuild needs a stated spread model, labelled "computed spread", or a source that provides bid/ask.

**F4. Bonds and the curve are a model, built two different ways.**
- The closing curve is a Nelson-Siegel fit (tau 2.0) to only the 13-week bill, 10y and 30y. `_curve_params` never
  reads `y5`, although `^FVX` is fetched as `RATE_SYMBOLS["y5"]` (VERIFIED `market.py:664-681`; `realfeed.py:28`).
- The live curve shifts the saved curve with all four yields, `^IRX/^FVX/^TNX/^TYX` (VERIFIED `live.py:136-151`).
  The same bond is therefore priced off two different curve constructions: one at the close, one intraday.
- The IG and HY spreads stay the **simulated** state's values (VERIFIED `market.py:627-628`, `ig`/`hy` from the sim).
- The curve's `policy_rate` is the NS value at 0.08y (VERIFIED `market.py:628, 758`). The macro screen's
  `policy_rate` is FRED DFEDTARU (VERIFIED `market.py:1327-1328`; `realmacro.py:212-218`). The FX model's USD rate
  is `curve.policy_rate` (VERIFIED `market.py:1006`).
- So a REAL save has **two different US policy rates**: a duplicate with conflicting values.

**F5. Futures and commodity "spot" are approximations.**
- The live futures price is the last close × (live ref / ref close) (VERIFIED `live.py:227, 238, 241`).
- For Treasury futures the reference is Yahoo `ZN=F` etc. and `ref_spot = prev_close` from the spark meta (VERIFIED
  `live.py:228-238`). Non-front contracts therefore move by the front month's percentage change.
- `SPOT:CL` and the other `SPOT:` ids are the front-month continuous **future** labelled as spot (VERIFIED
  `live.py:117-125`; module docstring :21).
- Yahoo has a quote for each individual contract (`CLX26.NYM` etc.), which the Desk already fetches (VERIFIED
  `finsim2/data/futcurve.py:9-12, 29`).

**F6. Listed options are not real chains.** The live option price is the model quote at the live underlying
(VERIFIED `live.py:206-215`). FinSim has no real chain source (VERIFIED: no Cboe or Yahoo options endpoint in
`finsim/`; only a UI link at `service.py:1772`). The Desk fetches the Cboe delayed-quotes JSON (VERIFIED
`finsim2/data/cboe.py:44`), but stores only features (iv30/60/90, skew), not contracts.

**F7. Rule conflicts on User-Agent.**
- `realnews.py` fetches **www.federalreserve.gov (a .gov host) with UA "Mozilla/5.0"** (VERIFIED `realnews.py:22-23,
  28-29, 47`). Iron rule 6 (CLAUDE.md) requires `LoganTerminal/1.0 (loganshaffer87@gmail.com)` on .gov and forbids
  a browser UA there.
- `realmacro.py` uses `"FinSim/1.0 (local simulator; standard library)"` for FRED (VERIFIED `realmacro.py:42, 128`).
  The Desk uses GOV_UA for FRED (VERIFIED `finsim2/data/fred.py:49`), so FinSim and the Desk differ.
- The Desk's `cboe.py` sends **BROWSER_UA to cdn.cboe.com** (VERIFIED `cboe.py:41, 65`). The owner rule allows a
  browser UA for Yahoo only. Reusing that module would carry this over; it should send an honest UA.

**F8. Suspected stale-"close" bug: a REAL save created or switched during market hours.** (Code path VERIFIED;
Yahoo behaviour INFERRED.)
1. `create_world` runs `feed.refresh("1y", force=True)` and stores `history(cal_sd-420d, latest)` with
   `latest = max(SPY dates)` (VERIFIED `service.py:121-131`; `realfeed.py:183-186`).
2. If Yahoo's daily series includes today's in-progress bar, today's mid-session price is stored in
   REAL_HISTORY_LOADED as today's "close". The Desk's own refresher assumes Yahoo does this: "the last stored bar
   may have been a partial (live) bar" (VERIFIED `finsim2/data/refresh.py:141`). That this happens for the spark
   endpoint is INFERRED.
3. After 16:00, `real_market_ready(today)` → `real_targets(today)` → `targets_from_history(real_history, today)`
   finds that date first (VERIFIED `market.py:563-569`; `realfeed.py:232-248`).
4. The session is then pinned to the intraday price and stored permanently in MARKET_CLOSE.
5. `switch_to_real` limits the history to `end = min(current_date, latest)` (VERIFIED `service.py:206-207`), so it
   is safer. `World.create`'s own fetch uses `latest_date()` too (VERIFIED `world.py:683`).
Not relevant once the day advance is removed, but it shows that "close" in these saves can be wrong.

**F9. The scheduler loads every save every minute.** `Scheduler.tick` calls `catch_up_one` for every stored world
(VERIFIED `server.py:412-416`). `catch_up_one` → `self.world(world_id, at, catch_up=False)` → `World.load`
(a full event replay) for any save not yet in memory, whatever its clock mode (VERIFIED `service.py:251-256,
258-269`). This contradicts the comment in `work_live_loaded` that "a save nobody has opened this run is not loaded
just for this" (VERIFIED `service.py:226-227`). On this memory-tight machine every save, with its large
REAL_HISTORY_LOADED payload, stays resident (INFERRED). It goes away with the day advance; keep it in mind for
whatever replaces it.

**F10. Read requests can trigger network fetches.**
- `world_info` computes `real_market.waiting` with `real_market_ready` (VERIFIED `service.py:306`). After the close,
  that can call `feed.refresh("1mo")` across every symbol (VERIFIED `world.py:350-353`).
- The housing page can call `real_macro_feed.refresh()` (VERIFIED `service.py:1350-1352`).
- **Every API request except `/api/health` runs under one global `router.lock`** (VERIFIED `server.py:353-355`).
  The scheduler takes the same lock around each `catch_up_one` and the live sweep (VERIFIED `server.py:415, 426`).
- So every network fetch made inside a request blocks the whole app while it runs. That includes:
  - `/live` quotes: up to 250 ids, 20 per batch, up to 4 attempts with 30 s timeouts (VERIFIED `service.py:187`;
    `realfeed.py:81-108`);
  - `real_market_ready` refreshes;
  - the housing refresh;
  - `create_world`'s one-year fetch of every symbol;
  - `switch_to_real`.
- A live-prices app polls quotes all the time, so its quote fetches must run outside this lock, for example as a
  background quote cache that requests only read (INFERRED design note).

**F11. Cache files are not written atomically and are shared.**
- `RealFeed._save_cache` and `RealMacro._save` write the target file in place with `open("w")` + `json.dump`
  (VERIFIED `realfeed.py:68-74`; `realmacro.py:118-124`).
- A torn write is silently read back as an empty cache (VERIFIED `realfeed.py:59-66`; `realmacro.py:110-116`).
- Several `RealFeed` instances (one per loaded REAL world, plus temporary ones in `create_world` and
  `switch_to_real`) write the same file, and the last writer wins (INFERRED).
- Saves themselves do not depend on these caches; replay uses events (VERIFIED `realfeed.py:3-7`; `world.py:676-678`).
- Relevant to the directive's "atomic save/load": use a temp file + `os.replace`.

**F12. Large payloads live inside the save.** REAL_HISTORY_LOADED holds about 420 days × every symbol (VERIFIED
`service.py:131`). REAL_MACRO_LOADED holds about 800 days of every FRED series (VERIFIED `service.py:138`). Every
MARKET_CLOSE stores every bar of every security, simulated or pinned (VERIFIED `simulation.py:44`). Without a day
advance, a live-only design needs only fills, cash events and, optionally, end-of-day mark snapshots (INFERRED).

**F13. Order execution is implemented twice.** LIVE orders and 15-minute instructions are worked by
`LiveDesk._work_one`, which has its own LIMIT / STOP / STOP_LIMIT / TRAILING_STOP / TAKE_PROFIT trigger logic
(VERIFIED `live.py:411-444`). The daily update works the same orders through `trading.work_orders` (VERIFIED
`simulation.py:96`; that module is outside my area). That makes two implementations of the trigger rules
(**duplicate computation**). A live-only app needs one.

**F14. The macro, news and calendar data are approximate.**
- Release dates are approximations, for example CPI on about the 12th and GDP on about the 28th (VERIFIED
  `realmacro.py:9-11, 78-96`).
- FOMC dates are hard-coded only for 2025-2026 (VERIFIED :44-45).
- There is no consensus, so surprise = 0 (VERIFIED :244-276).
- The Desk has a better calendar: FRED release dates via the API and the federalreserve.gov FOMC pages, both with
  GOV_UA (VERIFIED by grep, `finsim2/data/calendar.py:35-37, 71, 148-156`).

**F15. A REAL session silently falls back to pure simulation when it has no targets.** If `real_targets(d)` is None,
`generate_day` simply does not pin (VERIFIED `market.py:1059-1065`). The only guard is `real_market_ready` in
`advance` and `catch_up` (VERIFIED `world.py:1086, 1107`). Low risk today, but it is the kind of silent
simulated-for-real substitution the owner does not want.

**F16. What depends on the day advance.** Everything in `run_daily_process` (VERIFIED `simulation.py:34-139`), and
all of it must be re-homed or dropped:
1. **Prices:** MARKET_CLOSE, bars, curve, the vol surface, commodity curves, FX, regime.
2. **Corporate and macro events:** releases, earnings, ratings, defaults (simulated earnings jumps still apply in
   REAL mode: `market.py:911-916` runs regardless, VERIFIED; whether `macro.step` emits earnings in REAL mode is
   INFERRED, not checked).
3. **Settlement and lifecycle:** `process_lifecycle`, `process_due`, fails.
4. **Corporate actions:** dividends, coupons, maturities; MBS.
5. **Futures:** expiry and variation margin, the margin sweep.
6. **Financing desks:** sec-lending, lending desk, repo, funding, private credit, PE, IB, FX settlement, OTC,
   options expiry, prime, accruals (interest), the commodity desk, treasury, investors (fees accrued daily).
7. **Career and reporting:** careers limits, missions and reviews; the risk snapshot; `pnl.snapshot` (NAV
   history); the expiry of DAY orders; the briefing; client RFQs; the AI institutions.
8. **Order flow:** instruction execution (`trading.work_orders`).

In a live-only app these become one of two kinds of job:
- calendar-driven jobs that run on **real dates** (settlement T+1, coupons, dividends, expiries, interest accrual,
  borrow fees), with no simulated prices;
- or features to delete (regimes, invented desks).

**F17. The 15-minute "quote updates" are themselves a mini day-advance.** Instructions entered while ticks are
active get `executes_at = next_quote_tick` (VERIFIED `world.py:786-787`) and are worked once per tick (VERIFIED
`live.py:329-331, 353-356`). The owner's "current-day live prices" points to simple market or limit orders against
the live quote. The tick machinery (`clock.py:90-126`, `LiveDesk.instructions`, `Order.executes_at/last_tick`)
becomes optional.

**F18. Lead for the "10,000,000 in Treasury" bug** (outside my area; passed on, VERIFIED lines):
- The HTTP route `POST /worlds/{id}/portfolios` defaults `capital` to **10_000_000** when the body omits it (VERIFIED
  `finsim/api/server.py:131`).
- `Service.create_portfolio` turns that into fresh money with `contribute_capital`. This also applies to a
  `portfolio_type: "TREASURY"` book (VERIFIED `service.py:342-344`; `world.py:746-747`).
- `World.create_portfolio` also defaults `capital=D(10_000_000)` (VERIFIED `world.py:732`).
- In `_capital`, a capital of `"0"` or `""` maps to the job's **standard** capital, not zero (VERIFIED
  `service.py:52-60`). Only a numeric `0` gives an empty book (VERIFIED `service.py:338, 343`).
- So a front end that sends no capital, or the string "0", when creating a Treasury or book creates 10M (or the
  job's standard amount) out of nothing. INFERRED as the likely cause; the front-end call was not checked.

**F19. Things that are already right and can be reused.**
- The quote dict carries source, time and age (VERIFIED `live.py:171-176`).
- Fills record quote provenance in TRADE_EXECUTED, so replay needs no network (VERIFIED `live.py:446-451`).
- A symbol Yahoo does not quote is negative-cached (VERIFIED `realfeed.py:160-162`).
- The batch splitter keeps the good symbols (VERIFIED `realfeed.py:92-98`).
- The Desk's `env_key`/`http_get` never put key values or URLs in errors (VERIFIED `finsim2/data/__init__.py:29-36,
  45-49, 136-140`).
- The Desk's hedge market returns None past its staleness limits instead of guessing (VERIFIED
  `finsim2/hedge/market.py:34, 54-69`).

**F20. A REAL save still produces invented earnings, rating changes and issuer DEFAULTS.**
- `macro.step` runs for REAL saves (VERIFIED `market.py:897`). It generates seeded earnings reports (VERIFIED
  `macro.py:234-239`), monthly rating migrations (VERIFIED `macro.py:250-266`) and a daily seeded default hazard:
  `if allow_defaults and r3.random() < p_default` (VERIFIED `macro.py:267-272`). `allow_defaults=(d >= self.start)`
  has no real-mode check (VERIFIED `market.py:897`).
- `_pin_real_macro` replaces the releases, shocks, news and state, but **does not clear `earnings`, `ratings` or
  `defaults`** (VERIFIED `market.py:1331-1341`).
- `run_daily_process` then emits EARNINGS_REPORTED and RATING_CHANGED and calls `w.issuer_default(...)` for each
  invented default (VERIFIED `simulation.py:81-86`).
- In REAL mode the simulated news list is replaced (VERIFIED `market.py:1058, 1337`), so such a default would hit
  the books (bonds to recovery, CDS settlement) **with no headline explaining it**.
- Impact: `issuer_default` emits ISSUER_DEFAULTED. For a CORP_BOND reference it calls
  `otc.credit_event(reference, ev, recovery)` (CDS settlement), and it always calls
  `corporate.default_bond(reference, recovery, ev)` (VERIFIED `world.py:964-971`). What `default_bond` does to the
  holdings was not read (INFERRED: it writes them down to recovery). The issuer's equity is shocked only for
  `forced` defaults (VERIFIED `world.py:971-976`), and in REAL mode the equity is re-pinned to the real close anyway.
- This is one more reason to retire `generate_day` completely rather than patch it.

**F21. Some futures have no live quote at all.**
- `_financial_ref(code, feed)` returns a reference only when the code is in `YAHOO_FUTURES` (ZT, ZF, ZN, ZB) or when
  its `FINANCIAL_SOURCES` source is in `feed.equities` (VERIFIED `live.py:44-52`).
- `feed.equities` holds only equity-like classes and indices that have a Yahoo symbol (VERIFIED
  `realfeed.py:256-264`).
- So futures whose source is a bond or that have no source get **no live quote**, and a LIVE ticket raises "no live
  quote … send it as an instruction" (VERIFIED `live.py:309-311`). This covers SR3 (source None), TN and UB (UST-10Y
  and UST-30Y), and FGBS, FGBM, FGBL, FGBX, GLT and JGB (DE- and GB-/JP- bonds) (VERIFIED `commodities.py:230-236`).
- Under "live prices only" each needs its own Yahoo contract symbol (for example `SR3`, `TN=F`, `UB=F` on Yahoo;
  INFERRED availability) or must be shown as "computed" (cost of carry off the live curve).

**F22. The option quote cache grows with every distinct live underlying price.**
- `OptionsEngine.quote(sec, S, session)` caches on `(sec.id, current_date, f"{session}:{S}")` (VERIFIED
  `options.py:367-370, 384`).
- The cache is cleared only in `ensure_listings` (VERIFIED `options.py:228, 269`) and on splits (VERIFIED
  `options.py:981, 1054`).
- Each live refresh with a new `S` adds an entry for every option quoted (VERIFIED `live.py:213`). Today it is
  bounded by the day advance; **with no day advance it would grow without limit** (INFERRED).
- The same function seeds open interest with a per-date RNG (VERIFIED `options.py:379-380`), so the OI shown is
  synthetic.

**F23. A SIMULATED (sandbox) save starts from a month-old snapshot and then walks randomly.**
- The universe file says so: "The game starts from that snapshot and simulates from there; nothing here is a live
  feed" (VERIFIED `market.py:111-113`).
- `_anchor_to_snapshot` rescales the seeded prehistory so it ends at the snapshot (VERIFIED `market.py:800-804`).
- The first session is pinned to the snapshot with `_anchor_start_day` (VERIFIED `market.py:576-579, 1063-1065`).
- The snapshot is dated 2026-09-18 (VERIFIED in `universe.json`).
- So whatever start date it is given, a sandbox shows 2026-09-18 prices on day one and random prices afterwards.
  This is presumably what the owner is objecting to.

---------------------------------------------------------------------------------------------------------------

## 5. What "current-day live prices, no simulation, fake cash" needs, by asset class

Delay labels below are what the code or the Desk docstrings state. "Allowed" means the host appears in
`docs/RUNBOOK.md:31-66`. The rules applied: $0 data; never Stooq; never bypass anti-bot walls; a browser UA only for
Yahoo; .gov hosts get `LoganTerminal/1.0 (loganshaffer87@gmail.com)`.

| Asset class | FinSim today (REAL save) | Best $0 source already used or allowed | Delay / rate limits | Live quote or "computed"? |
|---|---|---|---|---|
| **US equities / ETFs** | Yahoo spark meta `regularMarketPrice`, `method: direct` (VERIFIED `live.py:245-251`; `realfeed.py:135-149`) | Yahoo `query1/query2.finance.yahoo.com` (allowed); spark v7 in FinSim, chart v8 in the Desk (VERIFIED `finsim2/data/yahoo.py:15, 33-36`). Fallbacks with free keys: Finnhub `FINNHUB_KEY`, Twelve Data `TWELVE_DATA_KEY`, Alpha Vantage `ALPHA_VANTAGE_KEY` (allowed; key names VERIFIED in the RUNBOOK) | Label "up to 15 minutes delayed" (VERIFIED `live.py:37`). Unofficial endpoint with no published quota. FinSim paces 0.2 s between 20-symbol live batches and keeps a 60 s memory cache (VERIFIED `realfeed.py:27, 113, 151-163`). The Desk retries 429 with 1.5 s doubling back-off (VERIFIED `yahoo.py:16-18`). Finnhub free pacing in the Desk is 1.05 s per call with a 61 s wait (VERIFIED `analyst.py:32, 132`) | **Live** (delayed quote). Bid/ask are not in spark meta, so the spread is **computed** (see F3) |
| **ADRs** | Same as equities (`ADR` is in `EQUITY_CLASSES`, VERIFIED `live.py:41`) | Yahoo US listing | As above | **Live** |
| **REITs** | Same (VERIFIED `live.py:41`) | Yahoo | As above | **Live** |
| **Preferreds** | Same, using Yahoo's hyphen symbols such as `BAC-PL` (VERIFIED `realfeed.py:261` comment) | Yahoo | As above; thin issues may have a stale `regularMarketTime`, so show `age_s` (the field exists, VERIFIED `live.py:175`) | **Live**, with the quote age shown |
| **London lines** (if kept) | Pence → pounds via `yahoo_scale` (VERIFIED `realfeed.py:49, 207, 251-253`; `live.py:250`) | Yahoo | Delay depends on the exchange (INFERRED) | **Live** |
| **Crypto** | Yahoo `{id}-USD` (VERIFIED `realfeed.py:261`) | Yahoo; CoinGecko `COINGECKO_KEY` (allowed) | 24/7 market | **Live** |
| **Treasuries and the curve** | Curve at the close: NS fit to ^IRX/^TNX/^TYX (VERIFIED `market.py:660-682`). Intraday: the save's curve shifted by ^IRX/^FVX/^TNX/^TYX (VERIFIED `live.py:128-153`). Each Treasury is priced off the curve (VERIFIED `live.py:178-199`) | Intraday: Yahoo `^IRX ^FVX ^TNX ^TYX` (the only free intraday yields found in either app). End of day: Treasury daily par curve at `home.treasury.gov` (allowed, .gov UA); FRED `DGS1MO…DGS30` (allowed; the Desk uses them, VERIFIED `finsim2/hedge/market.py:16-17`), with about one day of lag (INFERRED) | Yahoo yields are delayed (INFERRED, same label). The FRED CSV needs no key; the API needs `FRED_API_KEY`, with 429 back-off (VERIFIED `fred.py:40, 225`) | Curve: **live** at 4 tenors, otherwise **computed** by interpolation. Bill, note and bond prices: **computed** from the live curve (no free per-CUSIP quotes). Label "computed from live Treasury yields" |
| **Corporate bonds** (and munis, agencies) | Last close + the change in the live Treasury curve; the credit spread is the **simulated** one (VERIFIED `live.py:17-19, 194`; `market.py:627-628`) | No free live corporate bond prices. TRACE is not used and not allowed; `cdn.finra.org` is used only for Reg SHO short volume (VERIFIED `finra.py:18`). Spread inputs: FRED ICE BofA OAS indices (INFERRED series such as `BAMLC0A0CM` and `BAMLH0A0HYM2`; the repo's newspaper keeps `state/market-history/hy-oas.csv`, so HY OAS is already in use, INFERRED) | Daily, about one day of lag (INFERRED) | **Computed**: live Treasury curve + rating or index OAS + issuer spread. Label "model price" |
| **FX spot** | Yahoo `EURUSD=X`, `JPY=X` (inverted)… (VERIFIED `realfeed.py:32-35`); `fx_mid` and LIVE spot deals (VERIFIED `live.py:254-273`; `fx.py:69-125`) | Yahoo (allowed). End of day: `api.frankfurter.dev` (ECB reference rates, allowed) (INFERRED: daily, around 16:00 CET) | FinSim labels it "up to 15 minutes delayed"; true FX latency is INFERRED | **Live** |
| **FX forwards** | FX model forwards with FRED policy rates pinned in (VERIFIED `market.py:1296-1313`); USD leg = `curve.policy_rate`, the NS model rate (VERIFIED `market.py:1006`) | No free forward points. Inputs: live spot (Yahoo) plus short rates from FRED: `DFEDTARU`, `ECBDFR`, OECD `IRSTCI01…` (VERIFIED `realmacro.py:27, 33-36`). The Desk uses `DGS3MO`, `ECBDFR`, `IUDSOIA` (VERIFIED `hedge/market.py:31-32`) | Daily or monthly rates (OECD is monthly, VERIFIED `realmacro.py:31-32` comment) | **Computed** by covered interest parity from live spot and published rates. Label "computed" and show each rate's as-of date |
| **Commodities** (futures-based) | Front-month continuous `CL=F`…; `SPOT:` ids are this front-month quote (VERIFIED `live.py:117-125`); cents-quoted grains and softs divided by 100 (VERIFIED `realfeed.py:31`) | Yahoo `=F` continuous and per-contract symbols `CLX26.NYM`, `ZCZ26.CBT`, `GCZ26.CMX` (VERIFIED `futcurve.py:9-12, 29`). Not allowed: `api.eia.gov` (the Desk uses it with `EIA_API_KEY`; its curve series ended 2024-04-05, VERIFIED `futcurve.py:6-8`) | Delayed (exchange-dependent, INFERRED); the Desk paces 0.4 s per contract request (VERIFIED `futcurve.py:31`) | Futures: **live** per contract. Physical "spot": **computed** or clearly labelled "front-month future" |
| **Listed option chains** | **No real chains.** Model vol surface (seeded `vol.step`) repriced at the live underlying (VERIFIED `live.py:206-215`; `market.py:1081-1084`) | Cboe delayed-quotes JSON `cdn.cboe.com/api/global/delayed_quotes/options/{SYM}.json` (allowed host; the Desk reads it, VERIFIED `cboe.py:44`). It carries bid, ask, IV and greeks per contract (INFERRED from the Desk computing skew from "Cboe's deltas", VERIFIED `cboe.py:15`). Do **not** copy the Desk's `BROWSER_UA` there (F7). Vol-index fallback: FRED `VIXCLS`, `VXVCLS`, `VXNCLS`… (VERIFIED `hedge/market.py:19-29`) | About 15 minutes delayed (VERIFIED `cboe.py:23`); the Desk paces 1.0 s per request (VERIFIED `cboe.py:48`). The RUNBOOK says another Cboe path returned 403 for weeks (VERIFIED `RUNBOOK.md:73-74`); if this path is blocked, do not work around it (owner rule) | **Live** (delayed) where Cboe quotes the contract; otherwise **computed** with Black-Scholes or Black-76 from the live underlying and the vol index, labelled "computed" |
| **Futures** (equity index, rates, commodities) | Last close × the live reference ratio (VERIFIED `live.py:216-244`). References: SPY for ES, the coin for BTC, the VIX for VX, and Yahoo `ZT=F…ZB=F` for Treasury futures (VERIFIED `live.py:38-40`) | Yahoo per-contract symbols and `ES=F`, `NQ=F`, `ZN=F` (allowed) | Delayed (INFERRED) | **Live** per contract. Fair value from the cost of carry is **computed**, for display only |
| **OTC options and swaps** | Simulated OTC engine and dealers (`market.dealers.step`, VERIFIED `market.py:1057`); `otc.process_day` (VERIFIED `simulation.py:118`) | No market exists. Inputs: live curve, live spot, vol indices or Cboe IV; SOFR or swap rates from FRED (INFERRED availability) | — | **Computed** (mark-to-model) from live inputs, labelled "computed". Dealer quotes, if kept, are a stated spread over the model |
| **Private credit** | Simulated engine `pcredit.process_day` (VERIFIED `simulation.py:114`) | No market. Base rate: policy or SOFR (FRED); the spread is a model input | — | **Computed** mark only. Interest accrues on real calendar days, not on a day advance |
| **Policy rate** | FRED `DFEDTARU/DFEDTARL` as known on the date (VERIFIED `realmacro.py:27, 212-218`); conflicts with `curve.policy_rate` (F4) | FRED (allowed); `www.federalreserve.gov` FOMC statements and calendar (allowed, .gov UA; the Desk parses the calendar, VERIFIED `calendar.py:36-37`) | Changes only at FOMC decisions. FRED posts the daily target the next business day (INFERRED) | **Live enough** (a step function): one value, shown with its as-of date |
| **Indices** (VIX, DXY, foreign) | Yahoo `^VIX`, `DX-Y.NYB`, `^STOXX50E`… (VERIFIED `realfeed.py:36`) | Yahoo; VIX also on `cdn.cboe.com` | Delayed | **Live** (reference only, not tradable) |

**Common requirements for the live rebuild** (INFERRED design notes drawn from the above):
1. One quote service: batch requests, cache 30-60 s in memory, and store a quote only when it becomes a fill or a
   mark. Tag every number with source, time and age; `LiveDesk` already returns `source`, `time`, `time_ny`,
   `age_s`, `method` and `ref`.
2. A "computed" label wherever `method` is not `direct`. That covers the rows marked computed above: bonds, FX
   forwards, OTC, private credit, options Cboe does not quote, and spreads.
3. Off-hours behaviour: outside 09:30-16:00 NY, Yahoo's `regularMarketPrice` is the last regular-session price.
   Show it as "last close (time)". Whether fills are allowed then is an owner decision (INFERRED).
4. The User-Agent fixes from F7 before any code is reused.
5. Atomic writes for any cache (F11).

---------------------------------------------------------------------------------------------------------------

## 6. Game modules (high level)

All of them are driven by `run_daily_process`. The call order (VERIFIED `simulation.py:93-135`) is:
1. `clients.process_day` and `institutions.process_day` (C2), before `trading.work_orders`;
2. `pequity.process_day`, `ibank.process_day` and `investors.process_day` (F/G);
3. `careers.check_limits`, then `process_missions` and `maybe_review` (J).

| Module | What it is | Tied to the day advance? | Holds cash or positions? |
|---|---|---|---|
| `finsim/careers.py` (565 lines) | The job registry. `Job(key, title, description, capital, benchmark, allowed_classes, max_gross_leverage, max_position_pct, max_drawdown, ladder, status, capital_step)` (VERIFIED :24-36). `JOBS` standard capitals (VERIFIED :40-96): SANDBOX 1B; PORTFOLIO_MANAGER 100M; GLOBAL_MACRO 250M; COMMODITY_TRADER 100M; FIXED_INCOME_PM 250M; HEDGE_FUND 500M (step 0.3); PRIVATE_EQUITY 2B (0.5); INVESTMENT_BANKER 500M (0.4); BANK_TRADER 50M (0.4); DERIVATIVES_TRADER 50M (0.4); SEC_LENDING 100M; REPO_TRADER 100M; TREASURY_MANAGER 120M; RISK_MANAGER 0; AI desks MOMENTUM, VOL and COMMODITY 100M each, CARRY 200M (status AI). Steps not listed default to 0.5. `CareerEngine`: `check_limits`, `start_missions`, `process_missions`, `maybe_review`, `_career_decision` (VERIFIED :319-545) | **Yes.** Mission deadlines are counted in business days (VERIFIED :373). Reviews run at month, quarter and year ends from `nav_history` (VERIFIED :416-423) | **Injects cash**, which matters for the unexplained-cash bug: a mission completion emits `CAPITAL_CONTRIBUTED` = **5% of the job's capital** (VERIFIED :388-393); a promotion emits `CAPITAL_CONTRIBUTED` = `job.capital × capital_step` (0.3-0.5) (VERIFIED :536-541). These deposits come from the game, not from the player |
| `finsim/engines/investment_banking.py` (627) | Mandates (M&A, IPO, blocks, bond and loan underwriting), pitches decided in three sessions, engagements with session timelines (VERIFIED docstring :1-16). A block is "resold into the market … at the simulation's own prices" (VERIFIED :13-14) | **Yes.** `process_day(cause, prev_date)` (VERIFIED :262); timelines use `add_business_days` (VERIFIED :236, :311) | Yes: fees and block positions. Its own `_cash()` sets `ca.balance += amount; ca.base_value += amount` directly, then `record_cash_movement` (VERIFIED :455-459), inside event handlers (VERIFIED :98-102, :462-504). **Refuses all commands in REAL saves** (VERIFIED :195-198) |
| `finsim/engines/private_equity.py` (979) | Seeded monthly deal flow, auctions, LBO debt, quarterly portfolio-company reports, covenants, exits (VERIFIED docstring :1-14) | **Yes.** `process_day` (VERIFIED :466); bids decide in `BID_DAYS` sessions (VERIFIED :350); quarterly reports (VERIFIED :514) | Yes: equity cheques, fees, company marks. A second, identical `_cash()` helper (VERIFIED :715-719) is a **duplicate cash path** outside the ledger engine. INFERRED: `base_value += amount` without an FX conversion would be wrong if the book's currency is not USD. **Refuses all commands in REAL saves** (VERIFIED :295-298) |
| `finsim/engines/institutions.py` (274) | AI desks: MOMENTUM, CARRY, VOL_SELLER, COMMODITY_TREND (VERIFIED :1-14, `AI_JOBS` :26). Each is an ordinary portfolio trading through the same order engine; created only when a RISK_MANAGER book is opened (VERIFIED `world.py:754-757`) | **Yes.** `process_day(cause)` (VERIFIED :53); weekly or monthly rebalance by weekday or day of month (VERIFIED :112, :136, :178) | Yes: full portfolios with their own capital (100-200M each, VERIFIED careers capital list) |
| `finsim/engines/investors.py` (215) | Hedge-fund investor base. Subscriptions and redemptions are notified on the 15th and dealt at month end as `CAPITAL_CONTRIBUTED` / `CAPITAL_WITHDRAWN`. Management fee 1.5% a year accrued daily (account 5900); performance fee 15% at year end (5910) (VERIFIED :1-11) | **Yes.** `process_day(cause, prev)` (VERIFIED :58); a seeded RNG per date (VERIFIED :106) | **Moves cash in and out** of the fund book: `CAPITAL_CONTRIBUTED` from invented investors (VERIFIED :136). Another game-driven source of deposits |
| `finsim/engines/playbook.py` (465) | Named multi-leg strategies (stock, listed options, futures, TRS): preview as one package, execute legs as separate instructions, including a locate-and-borrow before a short sale (VERIFIED :1-10) | Not a daily process (no `process_day` found by grep, VERIFIED). Resolves tenors from `w.current_date` (VERIFIED :252) and quotes legs off the model chain (INFERRED) | No holdings of its own; it places orders and RFQs (VERIFIED docstring) |
| `finsim/engines/clients.py` (210) | Client RFQs for the bank-trader jobs: the player quotes, and the client deals at the update if the quote beats the street (the OTC engine's dealer mid and widths) (VERIFIED :1-11) | **Yes.** `process_day(cause)` (VERIFIED :55); RFQs expire the next business day (VERIFIED :103) | Books won trades against the client's ISDA or as a block (VERIFIED docstring). No direct cash code in the module (grep count 0, VERIFIED) |

**Effect of the live-only change on these modules** (INFERRED):
- Every one of them depends on the daily cycle and on invented counterparties, deal flow or investors.
- IB and PE already refuse to run in REAL saves.
- Careers, investors and AI desks inject or move cash that the player did not ask for: mission bonuses, promotion
  capital, investor subscriptions, AI desk books. A "fake cash, real prices" app must either remove them or record
  each one as an explicit, labelled ledger entry.

---------------------------------------------------------------------------------------------------------------

## 7. Not read in this pass (for the next mapper)

- `finsim/engines/trading.py` (`_fill`, `work_orders`, `_bar`, `_ref_value`): the second execution path in F13.
- `finsim/engines/vol.py` and `options.py` (the model chain behind live option quotes).
- `finsim/engines/commodities.py` (`FINANCIAL_SOURCES`).
- `finsim/engines/pricing.py` (`BondPricer`).
- `finsim/world.py:issuer_default` (F20 impact).
- `finsim/app.py`: the lines that touch live data. A grep for live/real hooks found only two `time.sleep(0.25)` calls
  at :223 and :583 and no `RealFeed`/`LiveDesk` references (VERIFIED by grep). Not read further.
- `finsim2/data/store.py` method bodies: only the path, WAL setup and method list were read.
- `finsim2/data/eulerpool.py` beyond its host, key name and pacing.
- `finsim2/data/refresh.py` lines 307-409 (the SEC loop, `last_business_day`, `stale`, `add_symbol`).
- The front end (`static/app.js`): how it polls `/live` and whether it computes live P&L itself.
- `finsim/world.py` and `finsim/api/service.py` outside the line ranges cited above.
- The game modules beyond their docstrings, method lists and the lines cited (the task asked for a high-level map).

STATUS: PARTIAL - not yet read: finsim/engines/trading.py (_fill, work_orders), vol.py, options.py (beyond quote()),
commodities.py (beyond FINANCIAL_SOURCES), pricing.py, world.py:issuer_default and world.py/service.py outside the
cited ranges, finsim2/data/store.py method bodies, finsim2/data/eulerpool.py bodies, finsim2/data/refresh.py:307-409,
static/app.js live polling, game-module bodies beyond the cited lines. Everything in the four live/real modules
(live.py, realfeed.py, realmacro.py, realnews.py) and finsim2/hedge/market.py + finsim2/data/__init__.py was read in full.

