# FinSim map part: MARKETS, TRADING AND RISK

Mapper: read-only subagent, 2026-10-20 (started 12:30 ET). Package root below = `finsim/finsim/`.
Tags: **VERIFIED** = I read the code at the cited path:line. **INFERRED** = deduced, not read end to end.

Context for the repair: the owner (2026-10-20 ~12:25) wants "current day live prices, no simulation, real but fake
cash" — so everything below that generates prices per simulated day is to be replaced. Section 17.1 lists each such
tie, 17.2 the live-price machinery that already exists, 17.3 suspected bugs, 17.4 duplicate computations.
Sections: 1 market.py, 2 daily cycle + order entry flow, 3 trading.py, 4 pricing.py, 5 risk.py, 6 quant/ + performance
stats, 7 FX, 8 commodities, 9 macro, 10 vol, 11 global_rates, 12 corporate_events, 13 corporate_actions,
14 commodity_desk, 15 briefing, 16 live.py, 17 findings.

---

## 1. engines/market.py (1,371 lines) — the simulated market and the "real tracking" overlay

### 1.1 What the file is (VERIFIED, market.py:1-16)
Docstring: "Generates a fictional but economically coherent market": equity factor model (market + sector + idiosyncratic),
Nelson-Siegel curve + IG/HY spread indices shocked off the same market factor, Markov regimes, dividend price drops.
"Every random draw is seeded from (world_seed, date), so the engine is a pure function of the seed: replays and
pre-history regeneration are exact." -> determinism is the basis of the replay/save model (see 4.x).

### 1.2 Module-level data (all computed at IMPORT time)
- `TENORS = [0.25,0.5,1,2,3,5,7,10,20,30]`, `TRADING_DAYS = 252.0` (VERIFIED market.py:45-46).
- `Regime` frozen dataclass: name, label, mkt_drift, mkt_vol, idio_mult, spread_mult, depth_mult, rate_level_drift_bp,
  rate_vol_bp, stock_rate_corr, slope_drift_bp, ig_spread_target, hy_spread_target, spread_vol, description, growth
  (VERIFIED market.py:53-70).
- `REGIMES` (5): NORMAL_GROWTH, RATE_HIKING, RECESSION, LIQUIDITY_STRESS, RATE_CUTTING (VERIFIED market.py:73-84).
- `TRANSITIONS`: monthly Markov transition table (VERIFIED market.py:87-93).
- `SECTOR_VOL`: daily sector-factor vol per sector name (VERIFIED market.py:100-108).
- `UNIVERSE = json.load(finsim/data/universe.json)` at import; `UNIVERSE_AS_OF` (VERIFIED market.py:114-117). Comment
  market.py:110-113: the universe is a snapshot of real listed companies/ETFs/bonds refreshed by
  `tools/refresh_universe.py` from Yahoo Finance prices + SEC EDGAR; "The game starts from that snapshot and simulates
  from there; nothing here is a live feed."
- `SHARES_FALLBACK_M` hard-coded share counts (VERIFIED market.py:123-133). NOTE: dict literal has duplicate key "SHOP"
  (1,300.0 at line 126 and again at line 130) — harmless (same value) but a sign of copy/paste (VERIFIED).
- `_adjusted_beta(raw)` Blume shrink `max(0.1, 0.67*raw+0.33)` (VERIFIED market.py:136-139).
- `_tier(rec)` LARGE/MID/SMALL by cap >=30bn / >=5bn; ETF=LARGE, PREFERRED=SMALL (VERIFIED market.py:142-152).
- `_spread_bps(rec, tier)`: ETF 1bp, PREFERRED 20bp, CRYPTO 4/12/40, INDEX 0, equities 2/6/30 (VERIFIED market.py:155-164).
  These are SYNTHETIC full bid/ask spreads used for every simulated quote.
- `_index_rows(...)`: adds DXY, VIX and the GLOBAL_INDICES cash indices (Euro Stoxx, DAX, FTSE, Nikkei, HSI...) as
  asset_class INDEX rows (VERIFIED market.py:167-188).
- `EQUITY_SEED`: list of 16-tuples (ticker, name, asset_class, sector, country, ccy, price, beta, sigma, adv, spread_bps,
  tier, dividend_yield, shares_m, yahoo, yahoo_scale); drops prices < 0.01 (SHIB/PEPE) (VERIFIED market.py:194-206).
- `REAL_FUNDAMENTALS` (US only) (VERIFIED market.py:207).
- Import side effects: `_apply_commodity_snapshot(UNIVERSE["commodities"])`, `_apply_fx_snapshot(UNIVERSE["fx"])`
  mutate module globals in commodities.py / fx_market.py (VERIFIED market.py:208-211).
- `INITIAL_POLICY_RATE` (bill_13w, default 0.0435), `INITIAL_LONG_RATE` (y30, 0.0475), `INITIAL_10Y` (y10, 0.0425)
  (VERIFIED market.py:212-215).
- `BOND_SEED`: UST bills 1M/3M/6M/1Y, notes 2/3/5/7/10/20/30Y, STRIPS 10/30Y, then corporate bonds from
  `UNIVERSE["bonds"]` (VERIFIED market.py:224-239). `_coupon(y)` rounds to 1/8% (market.py:218-220).
  NOTE: UST-2Y coupon is derived from the 5Y rate minus 12.5bp, UST-3Y from 5Y minus 5bp (VERIFIED market.py:233,235) —
  approximation, not a real 2Y yield.
- `BOND_ISSUER_TICKER` corp bond -> equity ticker (VERIFIED market.py:241).

### 1.3 Instrument universe — `build_universe(start: date, seed: int) -> Dict[str, Security]` (VERIFIED market.py:258-367)
- Equities/ETFs/ADRs/REITs/PREFERRED/CRYPTO/INDEX from EQUITY_SEED. Market code: CRYPTO, INDEX, US_EQUITY for USD, else
  MARKET_BY_COUNTRY (VERIFIED market.py:294-300). Quarterly dividend per share = px*dy/4 (market.py:265). Dividend anchor
  month and business-day are RANDOM per seed (`div_anchor_month=rng.randint(0,2)`, `div_day_bd=rng.randint(3,15)`)
  (VERIFIED market.py:297) -> ex-dates are invented, not real.
- Fundamentals: random (rng) unless REAL_FUNDAMENTALS has revenue (EDGAR) (VERIFIED market.py:266-291). ETFs/crypto/
  index: none (market.py:292-293).
- Crypto qty_step 0.0001, unit "coin" (market.py:299).
- Global cash indices get `index_level_source` (the ETF) and `index_factor` = index level / ETF price at snapshot
  (VERIFIED market.py:304-308).
- TIPS-5Y/10Y/30Y (real coupon = nominal - 2.25% breakeven), UST-FRN-2Y (13w bill + 15bp, freq 4) (VERIFIED market.py:310-326).
- Foreign sovereigns from `global_rates.SOVEREIGN_SPECS` priced at par on `foreign_curve(...)` (VERIFIED market.py:327-338).
- Structured products (ABS/CLO tranches) from `structured.STRUCTURED_SEED` (VERIFIED market.py:339-348).
- BOND_SEED bonds; issue dates SYNTHETIC: "issued four months before the world starts (on the 15th)"; bills issued the
  15th of start month (VERIFIED market.py:349-366). -> maturities/coupons are relative to world start, not real CUSIPs.
- ISIN/CUSIP are FAKE sequence numbers (`_isin`: `US{seq:09d}X`, `_cusip`) (VERIFIED market.py:250-255).

### 1.4 Curve: `nelson_siegel(level, slope, curv, tau, t)` (VERIFIED market.py:374-378); tau always 2.0 in callers.
Policy rate on every curve = NS at t=0.08 (VERIFIED market.py:949).

### 1.5 `MarketState` dataclass (VERIFIED market.py:381-392): date, regime, level, slope, curv, ig_spread, hy_spread,
mkt_factor, sector_factors. Docstring: "recomputed, never persisted" — but `state_dict()` (market.py:1262-1265) IS stored
per day by the caller and fed back via `ingest_close` (market.py:1109) (INFERRED: stored in the day's event by world.py).

### 1.6 `class MarketEngine` (VERIFIED market.py:395-437)
`__init__(seed, start, calendar, securities, prehistory_days=260, initial_regime="NORMAL_GROWTH")`. State it owns:
`history: Dict[sid, List[Bar]]`, `curves: List[YieldCurve]`, `regime_history`, `state: MarketState`, `_prev_close`
(set in bootstrap, market.py:547), sub-models `commodities` (CommodityModel), `lending` (LendingMarket), `fx` (FXModel),
`vol` (VolSurfaceModel), `dealers` (DealerModel), `macro` (MacroModel), `cevents` (CorporateEventModel), `mbs` (MBSModel);
`_forced_returns`, `_forced_rate_bp` (queued sandbox shocks); `bar_provider` (set by world: options bars);
`player_on_loan`; `vol_index_history`; `day_news`; `mbs_holder` callback; `real_history` / `real_feed` /
`real_macro` / `real_macro_feed` (the "world that tracks the market" mode).

Key methods (all VERIFIED):
- `dividend_ex_dates(sec, year)` quarterly ex-dates from the random anchor (market.py:440-455); `dividend_schedule`
  declared = ex-20bd, record = ex ("T+1: record = ex"), pay = ex+10bd (market.py:457-463); `dividend_on(sec,d)` (465-468).
- `_regime_for_month(prev, y, m)`: one Markov draw per calendar month seeded `"{seed}|regime|YYYY-MM"` (471-479).
- `ensure_listings(d)`: lists futures contract months, flags expired futures (`expiry < d`), lists TBA months, delists
  MBS pools >400 days old that nobody holds (482-514). Called TWICE inside generate_day (954 and 1004) and in ingest_close (1095).
- `front_contract(code)`, `spot(code)`, `curve_for(code)`, `vol_index()` (516-529).
- `_initial_state()`: d0 = start - prehistory_days - 1 business days; regime NORMAL_GROWTH; level=y30; slope = bill-y30
  clamped +-3%; curv .004; IG 105, HY 360 (532-535).
- `bootstrap()`: generates 260 business days of PRE-HISTORY by calling `generate_day` for each day before start, then
  `_anchor_to_snapshot()` (537-553). This is a random path rescaled so its last close = snapshot price (800-871).
- `_rescale_history(sid, factor)` (555-561).
- `real_targets(d)`: real closes for d from `real_history` else from live `real_feed.targets_for(d)` (563-569).
- `_snapshot_targets()` (572-574), `_anchor_start_day` (576-579) -> `_pin_day(d, bars, curve, cpayload, level, slope,
  curv, ig, hy, R, targets)` (581-657): rescales a simulated session's bars so closes equal targets; equities, commodity
  spot+curve+contracts, derived indices, FX spot, curve refit (`_curve_params`), every bond re-priced off the pinned
  curve, financial futures rescaled by their source's factor.
- `_curve_params(rates, curv)`: 3x3 Cramer solve of NS level/slope/curv to hit bill_13w, y10, y30; clamp slope/curv
  +-3%; re-set level so 30Y is exact (660-682).
- `apply_real_history(h)`: overlays REAL closes on the generated prehistory, date by date with carry-forward
  (`carry()` 690-697), equities, commodities, FX, curves (bond prices re-derived), financial futures (684-798).
  Line 798: `self._bar_cache.clear() if hasattr(self, "_bar_cache") else None` — `_bar_cache` is defined only on the
  options engine (options.py:137), so this line never runs (VERIFIED grep).
- `_anchor_to_snapshot()` (800-871): resets issuers to base rating/snapshot spread (849-855).
- `generate_day(d) -> (bars, curve, regime_change)` — THE DAILY PRICE GENERATOR (873-1087). Detailed in 1.7.
- `ingest_close(d, bars, curve, state, commodities, lending, fx, vol, dealers, macro, cevents)` (1090-1137): replay path:
  "adopt stored bars instead of regenerating them". Contains a pence->pounds repair for saves before 2026-09-24
  (1099-1105).
- `last_bar(sid)` = `history[sid][-1]`; options via `bar_provider`; KeyError otherwise (1140-1146). `curve()` = curves[-1] (1148).
- `curve_for_ccy(ccy, base, country)`: foreign curve from USD curve + FX policy rate; in real mode the 10Y point anchored
  to FRED monthly yield (1151-1170). `yield_anchor` (1172-1182). `curve_for_security` (1184-1188).
- `reset_floaters(curve)`: FRN coupon = 3M rate + spread every DAY ("weekly and quarterly resets rolled into a daily
  one"); TIPS index ratio = model CPI/100 (1190-1202). MUTATES Security objects in place.
- `breakeven(t)`, `real_curve`, `tips_clean_price`, `bond_clean_price` (1204-1228).
- `derive_indices(d, bars, skip)`: index level = ETF close x factor (1230-1242).
- `realized_vol(sid, window=20)`: annualised stdev of last 20 log returns, sqrt(252); falls back to sigma_annual
  (1253-1260). NOTE: `self.history[security_id]` raises KeyError for an unknown id (VERIFIED 1254).
- payload getters (1267-1283); `real_macro_series` (1286-1294); `_pin_fx_rates` (1296-1313); `_pin_real_macro`
  (1315-1344): replaces seeded macro with FRED state; zeroes rate/equity/vol shocks; news = FOMC only.
- `force_return(sid, log_return)` / `force_rate_shock(bp)`: queue shocks for NEXT session (1352-1358).
- `list_security(sec, price, day)`: spin-off listing (1360-1371).

### 1.7 How a simulated day's prices are generated — `generate_day(d)` step by step (VERIFIED market.py:873-1087)
1. RNG `random.Random(f"{seed}|day|{d}")` (877).
2. Regime: on `d == start` the chosen `initial_regime` takes effect; else on a month change draw `_regime_for_month`
   (881-890). `R = REGIMES[regime]`, `dt = 1/252` (891-892).
3. Macro: `self.macro.step(d, prev_bd, regime, oil20, prev.hy_spread, 0.0, allow_defaults=d>=start, live=d>=start,
   short_rate=NS(prev,0.08))` (895-899). In real mode (`real_history is not None`) and d>=start -> `_pin_real_macro` (900-902).
4. Corporate events: only `d >= start and not real_mode`: `cevents.step(...)` returns (events, news, shocks) -> extra
   returns (903-910). Earnings jumps from macro_out["earnings"] (911-912). Defaults: issuer equity gets -1.5 log return
   (913-916). Forced returns/rate shocks consumed (917-923).
5. Market factor: `mkt = (mu - 0.5 sigma^2) dt + sigma sqrt(dt) z_m + macro equity_shock`; sector factors
   `gauss(0, SECTOR_VOL[s]) * idio_mult` (926-928).
6. Curve: level shock correlated with z_m via stock_rate_corr, regime drift, macro rate_shock_bp, pull toward
   macro policy rate after start (5%/day), clamp [0.1%, 15%]; slope random walk clamp +-3%; curvature clamp +-2%
   (931-940).
7. Credit: IG mean-reverts 5%/day to regime target, -25bp x mkt, floor 40; HY -90bp x mkt, floor IG+120 (942-945).
8. Curve object (947-949).
9. Per security (955-1000): skip futures/options/delisted/PHYSICAL/INDEX. Defaulted bond = recovery*100 flat bar.
   Bond: clean off curve, bid/ask = clean -/+ half of spread_bps*spread_mult, volume = adv*depth_mult*lognormal; OPEN =
   previous close (968). Equity: `r = beta*mkt + sector + idio`, crypto scales dt by calendar days since last session
   ("Monday carries the weekend") (974-976); stablecoins pulled to 1.0 (978-980); Government ETFs carry at short rate
   (981-983); clamp r to [-45%, +35%] then add unclamped exogenous jumps (984); close = prev*exp(r) minus dividend on
   ex-date, floor 0.05 (985-987); synthetic open gap, high/low range, volume (988-994); bid/ask = close -/+ half of
   spread_bps*spread_mult (995-999).
10. `derive_indices` (1002); `ensure_listings` again (1004); FX step `self.fx.step(d, mkt, policy_rate, dlevel)` (1006);
    real mode -> `_pin_fx_rates` (1007-1008).
11. VIX = 100*(0.5*R.mkt_vol + 0.5*realized_vol("SPY"))*(1+vol_bump); DXY from FX spots (1009-1015).
12. Financial futures carry inputs: rate futures S = 100 - 100*fwd(0,0.25); others S = source close, q = coupon or
    dividend yield; DX q = sum of basket rates (1016-1034).
13. Commodities: `self.commodities.step(d, prev_bd, R.growth, mkt, level, dlevel, financials, depth_mult, spread_mult)`
    -> payload + news; contract bars via `bars_for_contracts`; PHYSICAL priced at spot (1035-1054).
14. Securities lending step, dealers step (1055-1057). `day_news` = all simulated news, or only macro news in real mode (1058).
15. REAL MODE PIN: `real = self.real_targets(d)`; if not None -> `_pin_day(...)` rescales the simulated bars to the real
    closes (1059-1062). Else if d == start -> pin to snapshot (1063-1065).
16. MBS step + reset_floaters off the FINAL curve (1068-1069); `self.state = MarketState(...)` (1071); append bars to
    history, curve to curves (1072-1074); VIX history (1075-1078); vol surfaces step per underlying (1080-1084);
    regime history (1085-1086).

KEY FACT for live prices: even "real mode" runs the whole simulator first and then RESCALES the simulated bar so its
close equals the real close (`_pin_day`, 590-595). Open/high/low/volume/bid/ask in real mode are therefore SIMULATED
shapes scaled by the pin factor, not real (VERIFIED 587-595). Instruments without a real target (bonds except via curve,
futures except via source/commodity spot, options) remain model prices.

---

## 2. The daily cycle that drives prices and orders — engines/simulation.py (cited for context; owned by the world/sim map)
`SimulationEngine.run_daily_process(d)` (VERIFIED simulation.py:34-139), in order:
DAY_STARTED -> `w.apply_scenario(start)` (38) -> `w.market.generate_day(d)` (41) -> MARKET_CLOSE event carrying ALL bars
`[o,h,l,c,v,bid,ask]`, curve, `state_dict()`, and commodity/lending/fx/vol/dealer/macro/corporate payloads (42-49) ->
REGIME_CHANGED + NEWS (50-56) -> real news (career world) (57-76) -> releases/earnings/ratings/defaults (78-89) ->
cevents.announce, corporate.declare_upcoming (90-91) -> clients/institutions (93-94) -> **`w.trading.work_orders(mkt)` (96)**
-> DAY_CLOSING -> settlement lifecycle + due (99-100) -> `treasury_cover_overdrafts` (101) -> corporate actions (104-106) ->
`futures.expire_contracts` + `daily_settlement` (108-109) -> financing desks (110-118) -> `options.process_day` (119) ->
`pnl.mark_all` (120) -> `futures.sweep_margin` (121) -> `treasury_cover_overdrafts` again (122) -> prime, accruals, cdesk,
treasury, investors (123-127) -> `pnl.mark_all` again (128) -> careers.check_limits, `pnl.snapshot`, **`risk.process_day`**
(130-132) -> **`trading.expire_day_orders(closing)` (133)** -> missions/review -> **`briefing.build(closing)` (137)** -> DAY_CLOSED.
`advance_one_day()` = next business day (141-143). Replay: world.py:606 calls `market.ingest_close(...)` with the stored
MARKET_CLOSE payload (VERIFIED grep world.py:606) instead of `generate_day`.
Other callers: `world.py:276 market.bootstrap()`, `world.py:316 market.apply_real_history(h)`, `world.py:372
market.force_rate_shock(bp)`, `world.py:975/1002 market.force_return(...)` (VERIFIED grep).

Order entry data flow (VERIFIED world.py:775-805, api/server.py:154, api/service.py:353-377): HTTP body (default
`time_in_force "DAY"`) -> `service.place_order` -> `World.place_order(..., execution="NEXT_UPDATE"|"LIVE", at)` ->
LIVE requires `self.live.available()` and no condition (781-784); quote-tick saves stamp `executes_at =
next_quote_tick(...)` (786-787); `trading.enter_order(..., execute_at=self.instruction_session(at), ...)` (789-791);
LIVE -> `self.live.fill(order, self.events[-1])` (792-793); `finally: self.flush()` (794-795).
SURPRISE (VERIFIED code path): if `live.fill` raises "no live quote ... send it as an instruction" (live.py:309-311),
the ORDER_ENTERED event has already been emitted and is flushed by the `finally`, so the user gets an error while a
WORKING LIVE order now exists and will rest against the quote stream and the next daily update.
`World.work_live(max_age_s, at)` -> `live.work` (798-805) is the quote-refresh sweep. `World.quote_ticks_active()` =
`market_source == "REAL"` and `live.available()` (806-808); `World.instruction_session(at)` -> clock.instruction_session
decides OPEN vs CLOSE for a new instruction (821-823).

---

## 3. engines/trading.py (826 lines) — order model and fills

### 3.1 Module docstring = the intended rules (VERIFIED trading.py:1-29)
"Orders entered by the player are instructions for the *next* daily update" except a LIVE ticket in a save that tracks
the real market, which "fills immediately at the latest quote (engines/live.py) through the same fill logic".
Constants (VERIFIED trading.py:45-53): `PARTICIPATION_CAP=0.20`, `IMPACT_K=0.6`, equity commission $0.005/sh min $1.00,
bonds 0.5bp of face, futures $2.50/contract, options $0.65/contract, `OPTION_SIGMA_DAILY=0.03`,
`ORDER_TYPES = MARKET, LIMIT, STOP, STOP_LIMIT, TAKE_PROFIT, TRAILING_STOP`.

### 3.2 `class TradingEngine(world)` — functions (all VERIFIED)
- `register()` binds handlers for ORDER_ENTERED/REJECTED/CANCELLED/STATUS_CHANGED, TRADE_EXECUTED/STATUS_CHANGED (60-67).
- `enter_order(portfolio_id, security_id, side, quantity, order_type, limit_price, stop_price, time_in_force,
  strategy_tag, trail_pct=None, condition=None, execute_at="OPEN", execution="NEXT_UPDATE", executes_at=None) -> Order`
  (70-96). Quantity quantized to `qty_step`; TRAILING_STOP initial level = last close x (1 -/+ trail_pct) (81-83);
  `execute_at` OPEN|CLOSE; `execution` LIVE|NEXT_UPDATE. Validation failure emits ORDER_REJECTED and raises CommandError.
- `_norm_condition` -> {ref, op, value} (98-101). `cancel` only WORKING/PARTIALLY_FILLED/ENTERED (103-112).
- `_validate(...)` (114-191): side, type, TIF in {DAY, GTC} ("DAY (good for the next session)"), qty>0, no INDEX,
  bond lot multiples, limit/stop present, trail 0-50%, condition refs (ticker, `CURVE:10Y`, `SPOT:CL`), bond matured,
  delisted, future expired or in last session, career mandate (`w.careers.job_for`), `w.risk.blocks_order` (151),
  options -> `_validate_option`; futures -> initial margin financeable via `w.prime.affordable` unless reducing (156-165);
  **SELL without inventory: rejected "nothing to deliver ... request a locate and borrow the shares on the
  securities-lending desk first"** (168-181) — deliverable = `collateral.available_quantity` + pending_receive - working
  sells; TBA sells exempt (166-167). So "short sales must borrow first" is ALREADY enforced for player tickets at entry
  (VERIFIED). BUY: affordability `w.prime.affordable(pf, est, haircut, ccy)` at limit or last bar ASK (182-190).
- `_validate_option` (193-230): expiry; expiring-today blocked; premium affordability; written-option margin
  `options.incremental_margin`.
- `_bar(sec)` = "Today's session bar": `market.last_bar` or `options.option_bar` (232-238).
- `_class_key`, `_unit` (bond 1/100, futures/options multiplier) (240-261). `_estimate_cost` (263-270).
- `projected_cash(pf, ccy)` (272-304): settled cash + DVP receivables - RVP payables - working buys (limit or ask) +
  working sells (limit or bid) - futures margin + pending spot FX in/out. A cash projection consumed by
  service.py:921/1145/1153/1684/1689, world.py:465, fx.py:125, prime.py:77, repo.py:80, settlement.py:101 (VERIFIED grep)
  -> candidate for the single accounting module.
- `_commission(sec, q)` (307-314). `_ref_value(ref)` for conditions (316-325).
- `work_orders(cause)` (327-347): every WORKING/PARTIALLY_FILLED order -> `_evaluate`; strategy legs all-or-none via
  `_strategy_ok` (349-366: every leg within 20% of session volume; package net within limit).
- `_evaluate(order, cause)` (368-464) — execution rules against "today's session bar":
  * futures/options expired -> CANCELLED; delisted -> CANCELLED (372-377).
  * `half = (ask - bid)/2` (385); session OPEN unless `execute_at == "CLOSE"` (386).
  * `w.quote_ticks_active()`: career save with live quotes -> orders work at the 15-minute quote updates; the daily
    update is a "safety net": only the CLOSE counts; orders with `executes_at` date > today wait (387-393).
  * condition gate evaluated at the close; then session forced to CLOSE (396-409).
  * TRAILING_STOP: hit if low<=level (SELL) / high>=level (BUY); else ratchet `trail_level` from the CLOSE (413-428).
  * STOP / STOP_LIMIT: trigger on range; gap-through fills from the open; STOP_LIMIT also needs limit reachable (429-446).
  * LIMIT / TAKE_PROFIT: at CLOSE needs close through limit; at OPEN needs low<=lim (BUY) / high>=lim, base =
    min(open, lim) / max(open, lim) (447-460). MARKET: base = open (or close) (461-462).
- `_fill(order, sec, bar, base, half, session, cause, forced=False, note="", fixed_price=None, commission_free=False,
  extra_detail=None) -> Trade` (466-541):
  * RNG `random.Random(f"{seed}|exec|{order.id}|{date}")` (472).
  * **20% rule**: `cap = session bar.volume * 0.20` (lot-rounded, >= qty_step); `fill_qty = min(remaining, cap)` unless
    forced/fixed (474-479). Partial fill -> order PARTIALLY_FILLED (657), keeps working next session (GTC) or is expired
    by `expire_day_orders` (DAY).
  * impact: sigma_daily (bonds 0.3%, futures sigma/sqrt252, options 3%, else realized_vol/sqrt252 floored 0.2%);
    `participation = fill_qty / (adv * regime.depth_mult)`; `impact_frac = 0.6 * sigma_daily * sqrt(participation) *
    U(0.8,1.2)`; bonds x0.5; forced +50bp; fixed_price -> no spread/impact (480-496). NOTE the 20% cap uses SESSION
    volume while impact participation uses ADV — two different liquidity measures (VERIFIED 475 vs 488). Impact uses
    `w.market.regime()` — a simulated regime (473).
  * BUY px = (base + half)*(1+impact) capped at limit; SELL px = (base - half)*(1-impact) floored at limit; futures
    rounded to tick (497-507).
  * gross/accrued/commission/net; futures net = commission only; settlement date via `settlement_date(calendar,
    settlement_config, market, current_date)`; TBA settles on delivery (509-527).
  * emits TRADE_EXECUTED with `execution_detail` (reference OHLC, spread_cost, impact_cost, impact_bps,
    participation_of_adv, session_volume, partial, regime, forced) (529-541).
- `_note` (543-545) mutates order.reason/history WITHOUT an event (VERIFIED) -> such notes are not replayable state
  (INFERRED: lost on reload; display text only).
- **DAY/GTC expiry**: `expire_day_orders(cause)` (552-561): a DAY order still WORKING/PARTIALLY_FILLED/ENTERED expires
  when `executes_at[:10] <= today` (career quote-tick saves) else when `entered_date < today`. Called once per day at
  simulation.py:133 AFTER work_orders. GTC never expires (no code path in trading.py).
- `system_order(pf, sec, side, qty, cause, reason, forced, fixed_price=None, allow_short=False, commission_free=False)`
  (563-577): system MARKET order filled at the CLOSE (or fixed price). `allow_short` is ACCEPTED BUT NEVER READ in the
  body (VERIFIED 563-577) — system orders bypass `_validate` entirely, so they can go short without a borrow. Callers
  (VERIFIED grep): futures.py:89/156/167, prime.py:171/182/203, options.py:687/692 (exercise/assignment,
  allow_short=True), seclending.py:227 (recall buy-in), corporate_events.py:244/252/263/370, commodity_desk.py:149-155,
  clients.py:160.
- Handlers (580-818): `_order_from_payload` (580-593); `_h_trade_executed` (626-755) builds Trade, updates order fill /
  average price, then:
  * FX: `k = payload fx_rate`; gross/accrued/commission converted to BASE ccy for book/ledger/lots (648-650), but
    `Lot.cost_per_unit = px` stays in the security's currency while `Lot.cost_total` is base ccy (691-692, 736-737)
    (VERIFIED) — mixed units in one Lot.
  * BUY: cover shorts first via FIFO (670-686), rest opens a long lot (687-702).
  * SELL: sell long first via FIFO with bond accrual true-up (704-732); rest opens a short lot (733-745).
  * posts journal `w.post(...)`, derives SETTLEMENT_INSTRUCTION_CREATED (RVP/DVP, cash_amount = net in security ccy),
    then `w.pnl.mark_position(pf, sec.id, ev)` (747-755).
  * Futures: `_apply_futures_trade` (757-787): signed position, average cost, commission paid immediately — USD path
    mutates `pf.cash_account(ccy).balance -= commission` directly (783) plus record_cash_movement + journal.
- `_relieve_fifo(pos, q, ev)` (789-812) raises RuntimeError if lots are short.

---

## 4. engines/pricing.py (240 lines) — bond maths and the valuation facade (all VERIFIED)
- `interp_rate(curve, t)`: linear interpolation on the zero curve, flat beyond the ends (28-38).
- `class BondPricer` (static; per 100 face; Actual/Actual-style accrual):
  `coupon_dates(sec)` back from maturity by 12/freq months to issue (53-63); `period(sec, asof)` (65-75);
  `accrued_per_100(sec, asof)` = coupon/freq x days since prev / days in period, 6 dp (77-87);
  `forward_rate(curve, t1, t2)` (89-92); `cash_flows(sec, asof, curve=None)` (94-113); `dirty_price_from_yield` (115-122);
  `clean_price_from_curve(sec, curve, asof)` = sum cf/(1+(z+spread)/f)^(f t) with z = interp zero, t = days/365.25,
  minus accrued (124-134) — this is the price every simulated bond bar uses (market.py:964, 1228);
  `yield_to_maturity` Newton, numeric derivative, 60 iterations (136-149);
  `risk_metrics(sec, asof, clean, curve=None)`: ytm, Macaulay, modified, convexity = (up+dn-2P)/(P*1e-8), DV01 per 100
  = (P(y-1bp) - P(y+1bp))/2; FRN rate duration capped at time to next reset; with curve: benchmark yield,
  spread_to_curve_bps, credit spread, `default_probability` = spread/(1-recovery) capped 0.99 (151-197).
- `class Instrument(sec, mark, asof, curve=None, beta_hint=None)` (200-240): `price`, `accrued`, `market_value(quantity,
  include_accrued=False)` — bonds qty*px/100; everything else `qty * mark` with NO futures/option multiplier (215-219);
  `risk_metrics(quantity)` adds `position_dv01 = dv01_per_100 * qty / 100` (226-232); `next_events` (234-240).
  `beta_hint` is accepted and ignored (203-207).
- SUSPECTED BUG (VERIFIED code, effect INFERRED small): `cash_flows` docstring says a floater's already-fixed period
  uses the current coupon, but the code (103-112) sets `prev` to the last PAST coupon date, so the first future period
  also takes `forward_rate(curve, t1<0, t2)` (with t1 floored to 0.02 inside forward_rate, but the exponent still uses
  the negative t1). Only FRNs/floating structured notes are affected.

---

## 5. engines/risk.py (453 lines) — exposures, Greeks aggregation, DV01, VaR, stress, liquidity, limits (all VERIFIED)
Docstring (1-21): linear sensitivities; 1-day historical-simulation VaR over 250 factor changes; parametric VaR;
10-day = sqrt(10) scaling; stress through the same sensitivities (not full revaluation); Euler component VaR;
liquidity at 20% of ADV; "Nothing here changes the ledger".
Data: `WINDOW=250`; `STRESS_SCENARIOS` (11 named: EQUITY_CRASH_20, RATES_UP_100, RATES_DOWN_100, CREDIT_WIDENING_200,
VOL_SPIKE, COMMODITY_SELLOFF_25, OIL_SPIKE_40, USD_STRONG_10, GFC_2008, PANDEMIC_2020, STAGFLATION) (36-48);
`LIMITS_BY_JOB` soft/hard limits per career job (50-58); `DEFAULT_LIMITS` (59); `ZN_DURATION = 6.5` (60).
- `exposures(pf) -> List[row]` (75-151). Row = {id, kind, name, bucket, quantity, market_value, equity:{sid:$},
  dv01, cs01, commodity:{code:$}, fx:{ccy:$}, vega, gamma_usd, underlying, rate_vega}.
  * Futures: notional = qty x last close x multiplier; COMMODITY* -> commodity delta; RATES -> `dv01 = -notional x 6.5 x
    1e-4` for EVERY rates future (SR3, ZT, ZN, ZB alike); **everything else (equity index, BTC, VIX, DX, Nikkei ...)
    is booked as SPY equity exposure** (86-95).
  * Options (Greeks aggregation): reads `pos.greeks` (set elsewhere — INFERRED options.py/pnl.py); delta$ = delta x
    qty x multiplier x S into the underlying (or the future's commodity); `gamma_usd = 0.5 x gamma x n x S^2`;
    `vega = vega x n` (96-108).
  * Bonds: `Instrument(sec, pos.mark, current_date, curve).risk_metrics(qty)` -> `dv01 = -position_dv01`; CORP_BOND
    `cs01 = dv01` (spread duration assumed = rate duration) (109-114). Foreign-currency bonds use the USD curve
    passed in and the USD 5Y rate change in VaR (INFERRED from 79, 200).
  * PHYSICAL -> commodity; other securities -> equity[sid] = market_value (115-118).
  * OTC rows from `t.analytics` (dv01, cs01, vega; TRS/XCCY/COMMODITY_SWAP deltas; CDS rating) (120-136).
  * Non-base cash and FX forwards -> fx exposure (137-150). FX risk of a FOREIGN-CURRENCY SECURITY position is NOT
    put in `fx` (only cash, forwards, XCCY) -> VaR/stress omit the currency risk of e.g. HK/EU equities and Bunds/JGBs
    (VERIFIED by absence in 80-118).
- `factor_summary(pf, rows)` (153-173): equity $, beta-dollar (security beta), DV01, CS01, vega, rate_vega, gamma, commodity,
  fx, gross commodity/fx.
- `factor_changes(window=250)` (176-202): from `market.curves`, `market.history` closes, commodity spot history, FX
  history, vol history — i.e. from the SIMULATED (or real-overlaid) day series. rates_bp = change of the 5Y point
  (`rates[5]`), IG/HY bp changes, log returns, vol points x100.
- `pnl_under(rows, shock)` (205-245): linear + gamma; stress `equity_all` scaled by each security's beta; HY vs IG by
  rating string.
- `var(pf, rows)` (248-282): needs >= 20 days; VaR95 = -sorted[floor(.05 n)], VaR99 = -sorted[floor(.01 n)], ES97.5 =
  mean of the floor(.025 n) worst; parametric = 2.326 sd; component VaR Euler; 10-day = sqrt(10). Efficiency: the
  covariance loop recomputes `sum(series)/n` inside the per-day generator (274) -> O(n^2) per row.
- `stress(pf, rows, custom)` (285-298): IG shock = half the spread shock, HY = full.
- `liquidity(pf)` (301-321): days to liquidate = |qty| / (adv x regime.depth_mult x 0.2) — uses the SIMULATED regime's
  depth multiplier; NAV from `w.pnl.compute_summary(pf)["nav"]`.
- `cash_ladder(pf)` (323-365): 20 business days of known flows. SUSPECTED BUG: `running` starts from the BASE currency
  cash balance and adds settlement/dividend/coupon amounts in their own currencies without FX conversion (329-343,
  360-364) — wrong for any non-base-currency flow.
- `limits(pf, var_, expo, liq)` (368-396): VaR/ES/counterparty/illiquidity vs NAV; leverage and drawdown via a SECOND
  `w.pnl.compute_summary(pf)` call (391) — NAV is computed twice per portfolio per day (303 and 391) = duplicate computation.
  Drawdown uses `pf.peak_nav` (394).
- `hard_breaches`, `blocks_order(pf, sec, side)` (398-414): during a HARD breach only "reducing" orders pass, judged by
  direction only — a SELL larger than the long position (flip to short) still counts as reducing (411).
- `report(pf)` (417-428); `process_day(cause)` (430-446) emits RISK_SNAPSHOT + RISK_BREACH per portfolio (called from
  simulation.py:132 once per simulated day); `_h_snapshot` keeps the last 260 snapshots in `pf.risk_history` (448-453).
- Concentration: there is NO single-name concentration metric in risk.py; the only concentration-like limits are
  "Largest counterparty exposure / NAV" and gross leverage (VERIFIED 386-393).

---

## 6. quant/ (stats, portfolio, asset_metrics, timeseries, regression) and portfolio performance statistics

### 6.1 Who uses quant/ — VERIFIED by grep over `finsim/` and `finsim/finsim/`
The FinSim app runtime does NOT import `finsim.quant` anywhere (no hits in api/, app.py, world.py, engines/). Users are:
`tests/test_quant.py:8-11`, `tests/test_fs2_equations.py:9`, and the separate Desk package (`finsim2/engine/equations.py:27-40`,
`finsim2/engine/features.py:329/360/478`, `finsim2/engine/portfolio.py:841/864`, `finsim2/engine/scenario.py:18`,
`finsim2/server.py:416-419`). `quant/catalog.py:19/521` imports by dotted path for the equation catalogue.
=> For the FinSim repair the quant package is an unused (by FinSim) equation library; it is a good home for the
single tested implementation of Sharpe/Sortino/beta/drawdown, but today FinSim computes those INLINE elsewhere (6.3).

### 6.2 Functions (VERIFIED)
- `quant/stats.py` (241): `norm_pdf/norm_cdf/norm_ppf` (Acklam + Halley) (10-54); returns: `simple_return(s)`,
  `log_return(s)`, `cumulative_return`, `cumulative_log_return`, `annualized_return(returns, periods_per_year=252)`
  (59-102); moments `mean`, `geometric_mean_return`, `variance(ddof=1)`, `stdev`, `covariance` (truncates both to the
  FIRST n), `correlation`, `zscore` (107-149); probability helpers (154-199); `skewness`, `excess_kurtosis`,
  `standard_error` (204-224); `sharpe_ratio(returns, rf=0.0, periods_per_year=None)` = (mean-rf)/stdev x sqrt(A)
  (227-230) — raises ZeroDivisionError on constant returns; `downside_deviation(returns, target)` RMS of shortfalls over
  ALL n (233-235); `sortino_ratio` (238-241) — ZeroDivisionError when no return is below target.
- `quant/portfolio.py` (92): `portfolio_variance`, `mean_variance_objective`, `mean_variance_weights(_budget)`,
  `is_fully_invested`, `frontier_weights`, `gmv_weights`, `risk_contributions`, `market_beta(asset, market)` (aligns the
  LAST n), `capm_expected_return`, `jensens_alpha`, `parametric_var(mu_loss, sigma_loss, alpha)`, `expected_shortfall`
  (8-92).
- `quant/asset_metrics.py` (242): `asset_metrics(closes, market_closes=None, rf=0.04, dividends=None,
  periods_per_year=252, fast=True) -> dict` never raises (88-99); `_fill` (109-237) computes 1d/1m/3m/1y returns,
  12-1 momentum, max drawdown of PRICE, z-score, annualised return, vol, t-stat, skew, kurtosis, Sharpe (rf/252 per
  day), Sortino, ACF1, normal VaR/ES, EWMA vol, GARCH, AR(1) half-life, ADF, beta/corr/R2/Jensen alpha/CAPM vs market.
  Market series aligned to the asset "from the end" by POSITION, not by date (213-221) (VERIFIED) — misaligns when
  calendars differ (crypto 7-day vs equities). It re-implements inline the same Sharpe/Sortino/skew/kurtosis/beta as
  stats.py/portfolio.py (178-181, 176-177, 229) = duplicate computation. `rf=0.04` hard default (88). `score_table` (240-242).
- `quant/timeseries.py` (259): `fit_ar(x, p)` OLS (14-28), `fit_ar1(x)` closed form -> (c, phi, sigma2) (31-44),
  `ar1_forecast` (47-52), `arma_residuals` (57-71), `ma_filter` (74-76), `simulate_arma`/`simulate_ma` (79-100, seeded
  RNG), `_css` objective (103-112), `fit_arma` Hannan-Rissanen start + Nelder-Mead CSS (115-145), `fit_ma` (148-151),
  `diff`, `diff_n`, `fit_arima` (156-172), `autocorrelation`/`acf` (177-192), `stationarity_check` split-half
  heuristic (203-218), `adf_test(x, lags=1, trend="ct")` with fixed 5% critical values -3.41/-2.86 (221-259). Pure
  functions; no state.
- `quant/regression.py` (126, read in full): `add_constant`, `linear_model`, `ols` = solve(X'X, X'y), fitted/residuals,
  SSE/MSE/RMSE/MAE, `r_squared`, `adjusted_r_squared`, `coef_standard_errors`, `t_statistic`, `coef_t_stats`, `ols_fit`,
  `log_linear_regression`, `add_interaction`, `interaction_model`, `simple_regression` (7-126). Pure functions;
  `simple_regression` divides by sxx without a guard (123).

### 6.3 Portfolio performance statistics actually used by FinSim — duplicated (VERIFIED)
- `careers.py:434-483 build_review(pf, start, end, period)`: return = (nav1 - flows - nav0)/nav0; daily returns =
  `day_pnl / previous NAV`; Sharpe = (mean - policy_rate/252)/sd x sqrt(252) using TODAY's simulated
  `w.market.curve().policy_rate` for the whole period (455-456); max drawdown over the period's snapshots (457-461);
  "alpha" = ret - benchmark (simple difference, NOT Jensen) or ret - policy_rate x days/252 (477).
- `api/service.py:647-684 career(...)`: a SECOND copy: Sharpe since inception (655-664), max drawdown (665-668),
  return since inception = (nav - contributed_capital)/contributed_capital (670) — a different return definition from
  careers.py:444; current drawdown from `pf.peak_nav`; max single position / NAV (679).
- `risk.py:394` drawdown from `pf.peak_nav` (a third drawdown).
- Concentration (single position / NAV) exists in three places: `careers.py:358-359` (CONCENTRATION breach),
  `api/service.py:679`, `engines/briefing.py:356` (comment "concentration"); none in risk.py.
- Not found in FinSim runtime: Sortino, beta/alpha of the PORTFOLIO vs a benchmark regression (only security betas
  from the snapshot and the `w.careers._benchmark_return` difference).

---

## 7. FX — engines/fx_market.py (121) and engines/fx.py (298) (all VERIFIED)

### 7.1 fx_market.py — the simulated FX market
- `CURRENCIES` (26) (13-14); `CcySpec(code, spot0 USD per unit, rate0, vol, mkt_beta, spread_bps, inverse_quote)` (17-25);
  `SPECS` hard-coded starting spots/rates/vols/betas/spreads (28-54); `DXY_WEIGHTS`, `DXY_SCALE` (56-57);
  `dollar_index(spots)` ICE geometric basket (60-69); `apply_snapshot(spots)` re-anchors `spot0` from universe.json at
  import (72-77, called from market.py:211).
- `class FXModel(seed)` (80-121): `spot` (USD per unit), `rate` (policy rate per ccy; USD default 0.0425 hard-coded at 84),
  `history[ccy] = [(date, spot, rate)]`, `rate_source`.
  `step(d, mkt_factor, usd_rate, usd_rate_change)` (88-102): foreign rate follows 0.5 x USD change + mean reversion +
  noise; spot log-return = -0.3 x carry + beta x market factor + vol noise + pull to spot0, clamped +-8%/day; seeded
  `"{seed}|fx|{ccy}|{date}"`. `ingest` (104-109) replay. `cross(buy, sell)` (111-113). `forward(buy, sell, d, maturity)`
  CIP with SIMPLE interest (115-118). `spread_bps` = max of the two currencies' spreads (120-121).

### 7.2 fx.py — `class FXEngine(world)`: multi-currency cash, spot deals, forwards, translation
- Helpers: `usd(ccy)` = model spot (40-41); `to_base(ccy, amt)` = amt x USD spot (43-44); `k(ccy)` = 1 if ccy == "USD"
  else USD spot (46-48). NOTE: "base" is hard-wired to USD in `k`/`to_base` even though portfolios carry
  `pf.base_currency` (INFERRED: base is always USD in practice).
- `quote(buy, sell, amount, amount_ccy="BUY", mid=None)`: rate = mid x (1 + spread/2) (50-61). `direct_pair` (64-67);
  `cross_quote(...)` routes crosses through USD; with `live=True` reads `w.live.fx_mid(c, "USD")` (69-96).
- `spot(pf, buy, sell, amount, amount_ccy="BUY", execution="NEXT_UPDATE", tag=None, ...)` (98-135): LIVE execution uses
  `w.live.fx_mid(buy, sell)` (115-121) — i.e. FX already has a live-quote path; availability check uses
  `trading.projected_cash` for the base currency (124-126); settles T+`settlement_config.cycle_for("FX_SPOT")` (131);
  emits FX_TRADE_EXECUTED.
- `forward(pf, buy, sell, buy_amount, maturity, tag=None)`: maturity > T+2, rolled; forward rate = model CIP x (1+spread)
  (137-157). `forward_value(f, asof)` simple-interest PVs at MODEL rates (160-165).
- `process_day(cause)` (168-194) — DAILY: settles due spot deals, settles/marks forwards (FX_FORWARD_MARKED when MTM
  changes), and retranslates every non-base cash balance (FX_TRANSLATED -> 4600 unrealised FX). Called from
  simulation.py:117 once per simulated day.
- `exposures(pf)` per-currency net (196-209) — a second FX exposure computation besides risk.py:137-150.
- `_adjust_cash(pf, ccy, local_delta, ev, kind, ref, base_value_hint=None)` (212-224): moves local balance and base
  carrying value (average-cost relief on outflows) and calls `w.record_cash_movement`. Handlers `_h_executed` (226-234,
  dr 1250 / cr 2350), `_h_settled` (236-252), `_h_translated` (254-262), `_h_fwd_opened` (264-270), `_h_fwd_marked`
  (272-280, dr 1600 / cr 4700), `_h_fwd_settled` (282-298). I checked the journals in `_h_settled` and `_h_fwd_settled`
  balance (debits = credits) (VERIFIED arithmetic).

---

## 8. engines/commodities.py (422) — commodity fundamentals, curves, futures listings (VERIFIED unless noted)
- Docstring (1-20): per-commodity supply/demand state (demand_z, supply_shock, inventory_z); spot responds to changes
  in balance, market factor, seasonality; curve = cost of carry with inventory-driven convenience yield; financial
  futures use their own carry; "Every draw is seeded by (world_seed, commodity, date)".
- `CommoditySpec` (39-60+) code, group (COMMODITY_ENERGY/METAL/AG/LIVESTOCK, EQUITY_INDEX, RATES, ...), unit, spot0, vol,
  storage, conv_base, inv_sens, demand_beta, mkt_beta, seasonality, listed months, n_listed, multiplier, tick,
  margin_pct, adv, expiry_rule ... `SPECS` list (lines ~62-135, values NOT read line by line). The 57 codes (VERIFIED by
  grep): energy/metals/ags/livestock CL BRN NG RB HO GC SI HG PL PA ALI NI ZNC PB SN TIO TTF JKM EUA ZC ZW ZS KC SB CT CC
  LE GF HE; financial ES NQ RTY YM ZT ZF ZN TN ZB UB SR3 BTC ETH DX VX FESX FDAX FTSE NKD HSIF MME MFS FGBS FGBM FGBL FGBX
  GLT JGB. (Values read: SPECS 68-135; e.g. CL 1,000 bbl, ES x50, ZN x1000, SR3 x2500 "100 - rate", TTF/EUA/FESX/FDAX/
  FGB* in EUR, FTSE/GLT in GBP, HSIF in HKD, JGB in JPY; financial contracts have spot0 = 0 and no supply/demand state.)
  `SPEC_BY_CODE` (136),
  `apply_snapshot(spots)` (139), `EVENT_TEMPLATES` news templates (150), `CommodityState` (194), `contract_id` (202).
- `FINANCIAL_SOURCES` (230-237): financial futures ride a source security: ES/NQ/RTY/YM on SPY/QQQ/IWM/DIA; ZT/ZF/ZN/ZB/TN/UB
  on UST notes; SR3 = 100 - forward rate; BTC/ETH on the coins; DX on DXY; VX on VIX; Bund/Bobl/Schatz/Buxl, gilt, JGB
  on the foreign sovereigns; FESX/FDAX/FTSE/NKD/HSIF/MME/MFS on the derived cash indices. `GLOBAL_INDICES` (239-241).
- `expiry_for(spec, cal, y, m)` last-trade date rules (245-265).
- `class CommodityModel(seed, cal)` (268-422): `state`, `spot_history`, `curve_history`; `listed_months` (277-286);
  `make_contract` builds a FUTURE Security with expiry, multiplier, tick, margin_pct (288-295).
  `step(d, prev_bd, regime_growth, mkt_factor, rate_level, rate_change, financials, depth_mult, spread_mult)` (303-376):
  physical commodities: demand AR(1) on regime growth; random supply events with news (prob x calendar days, so
  Monday covers the weekend) (320-326); scheduled inventory reports (WEEKLY_WED / MONTHLY_10) with surprise + news
  (329-335); inventory accumulation (337-338); spot log-return from balance changes + pull to spot0 + beta x market +
  vol noise, gold -2.5 x rate change, clamp +-25% (343-349); convenience yield from inventory (350); curve (351).
  Financial futures: curve per listed month from S (carry `S e^{(r-q)T}`, VIX mean-reversion to 19.5, SR3 from forwards)
  (357-375). `curve(spec, st, d, r)` cost-of-carry + seasonality (378-386). `ingest` replay (388-395).
  `bars_for_contracts(...)` (397-422): futures bars = model curve price as close, OHLC shape copied from spot path,
  volume = adv x liquidity decay by month index x depth_mult x lognormal, half-spread max(tick/2, 2bp x ...).
- => Every futures price in FinSim is a MODEL price off the (pinned) spot/curve; no real futures quotes are used in
  generate_day (VERIFIED: bars come only from `bars_for_contracts`, market.py:1043-1054).

---

## 9. engines/macro.py (401) — simulated economy, central bank, earnings, credit migration, defaults (VERIFIED)
- Constants: `NEUTRAL_RATE`, `INFLATION_TARGET=2.0`, `RATING_ORDER`, `RATING_SPREAD`, `MEETING_MONTHS`, `REGIME_GROWTH`,
  `REGIME_INFL` (23-30). `MacroState`, `IssuerState` (34-53).
- `class MacroModel(seed, securities, cal)` (69-401): `real_series` (FRED, career world), `state`, `history`, `releases`,
  `cpi_index` (model CPI compounding daily; drives TIPS index ratios via market.reset_floaters and inflation swaps),
  `neutral`, `earnings`, `issuers` (one per CORP_BOND/STRUCTURED security), `rating_history` (70-90).
- Calendars: `meeting_dates` (third Wednesdays of MEETING_MONTHS, or real FOMC dates when real_series) (93-97);
  `release_calendar` PAYROLLS/PMI/CPI/RETAIL_SALES/GDP/FOMC on synthetic dates (106-113); `earnings_date` RANDOM day
  12-40 days into the following quarter (115-122); `upcoming` (124-141).
- `step(d, prev_bd, regime, oil_return_20d, hy_bps, mkt_hint, allow_defaults=True, live=True, short_rate=None)`
  (159-283): growth/inflation/unemployment drift to regime anchors with noise; at go-live calibrates r* (172-178);
  scheduled releases with random surprises -> `rate_shock_bp`, `equity_shock`, `vol_bump`, `fx_shock`, news (190-233);
  FOMC `_decide` Taylor-type reaction function with random "communication surprise" (285-310); earnings via `_report`
  (random EPS surprise, price `jump` clamp +-25%, MUTATES `sec.fundamentals` eps/revenue/net_income, may change the
  dividend) (312-337); issuer spread mean reversion + monthly rating migration + daily default hazard (241-276);
  then `_apply_payload` (282, 340-365) which writes `sec.fundamentals`, `sec.dividend_per_share`, `sec.rating`,
  `sec.spread_bps_credit` onto the shared Security objects.
- `ingest(d, payload)` replay (367-387); `mark_defaulted` (389-393); `dashboard(d)` (395-401).
- SUSPECTED BUG (VERIFIED by reading market.py:897-902, macro.py:282, market.py:1315-1344, simulation.py:81-86): in a
  "real tracking" save, `macro.step(..., allow_defaults=(d >= start))` still runs with SIMULATED earnings, ratings and
  defaults, and `_apply_payload` has ALREADY mutated fundamentals/dividends/ratings/spreads before `_pin_real_macro`
  runs; `_pin_real_macro` replaces only state, releases, shocks and news, NOT `macro_out["earnings"]`,
  `["ratings"]`, `["defaults"]`. So a real-price save can get an invented bankruptcy (`w.issuer_default` at
  simulation.py:86, bond priced at recovery, issuer equity -1.5 log return queued at market.py:913-916), invented
  EARNINGS_REPORTED events (simulation.py:81-82), invented downgrades, and EDGAR fundamentals overwritten. Equity jumps
  are later overwritten by `_pin_day` for names that have a real close; bonds/CDS are not.

---

## 10. engines/vol.py (163) — implied-vol surfaces (VERIFIED)
- Docstring (1-14): per-underlying ATM(30d)/skew/curvature/term state; IV(K,T) = ATM(T) exp(skew_T m + curv m^2);
  "State is stored in the MARKET_CLOSE payload so replay ingests it."
- `REGIME_IV`, `REGIME_SKEW`, `REGIME_TERM` regime-dependent targets (24-26); `BASE_T = 30/365` (27).
- `VolState(atm, skew, curv, term)` (30-35). `class VolSurfaceModel(seed)` (38-97): `init_underlying(sid,
  structural_vol, is_index)` (44-49); `step(d, sid, structural_vol, realized_20d, vol_index, vol_index_change, regime,
  is_index)` (51-63): ATM mean-reverts to 0.45 structural + 0.30 realized + 0.25 VIX-scaled + regime premium, reacts to
  VIX change, noise; skew/term to regime targets; seeded `"{seed}|iv|{sid}|{date}"`. `ingest` (65-73);
  `atm_for_tenor` (75-78); `iv(sid, strike, forward, T)` = ATM(T) x exp(skew_T m + curv m^2) (80-86); `iv_rank` (88-97).
- Option universe helpers: `OPTIONABLE_CLASSES`, index options SPX/NDX/RUT on SPY/QQQ/IWM x factor (100-116),
  `OPTION_VENUES`, `OPTION_LOT`, `has_listed_options`, `option_multiplier`, `option_venue`, `is_core_optionable`,
  `optionable_underlyings`, `structural_vol = sqrt((beta x 0.16)^2 + sigma^2)` (119-163).
- => All option prices are MODEL prices off this simulated surface (no real option quotes) (INFERRED from absence of any
  real-feed call in vol.py; options pricing itself is in options.py / options_pricing.py, not in my area).

---

## 11. engines/global_rates.py (75) — non-USD government curves (VERIFIED)
- Docstring (1-9): local curves are DERIVED from the dollar curve + policy-rate differential + term adjustment + country
  spread; "a save pinned to the real Treasury curve moves its Bunds with it".
- `CURVE_ADJ` (EUR -20bp, GBP +10bp, JPY -60bp at the long end) (18); `COUNTRY_SPREAD` (FR +60bp, IT +120bp) (20-21);
  `SOVEREIGN_SPECS` 17 bonds DE/FR/IT/GB/JP (24-42); `weight(t)` (47-49); `foreign_curve(ccy, usd, local_policy,
  usd_policy, country)` (52-64); `par_coupon(curve, years, freq)` (67-75).
- Under live prices these curves are synthetic; real mode only re-anchors the 10Y point to FRED monthly yields
  (market.py:1159-1170).

---

## 12. engines/corporate_events.py (390) — invented corporate events, fail charges, buy-ins (VERIFIED)
- Docstring (1-12): `CorporateEventModel` (market side, seeded announcements in the market close) and
  `CorporateEventsEngine` (world side: elections, effective dates, fails).
- `EVENT_TYPES`, `ANNUAL_PROB` (stock dividend 6%, special 8%, tender 5%, rights 5%, cash merger 3%, spin-off 3% per
  name per year), `FAIL_CHARGE_RATE=0.03`, `BUY_IN_AFTER_FAILS=5`, `BUY_IN_PENALTY=0.02`, `CALLABLE` (AAL-28, F-32) (27-32).
- `CorporateEventModel.step(d, securities, regime, prices, curve_level) -> (announcements, news, shocks)` (45-137):
  per EQUITY/ADR/REIT a seeded daily draw; effective 15-30 bd later; price shocks (tender +0.7 x premium, merger
  +0.85 x premium, rights dilution ...); bond calls when price > call price. DISABLED in real mode (market.py:904-909).
- `CorporateEventsEngine`: `announce` (162-167) -> CORPORATE_EVENT_ANNOUNCED -> `_h_announced` builds a
  CorporateAction with `currency="USD"` hard-coded (174-175) even for non-USD listings (INFERRED issue for HK/EU names);
  `elect` (180-212); `process_day` (220-279) executes tenders/rights/mergers via `trading.system_order(...,
  fixed_price=..., commission_free=True)`, spin-offs (`_spin_off` 281-298), bond calls (BOND_MATURED + delist);
  `_h_processed` spin-off cost split (300-327); `_h_listed` (329-338) -> `market.list_security`; `_h_delisted` (340-347).
- `process_fails(cause)` (350-373): daily fail charge 3%/360 on failed SIs; after 5 failed days a forced buy-in through
  `system_order(..., forced=True)` + 2% penalty.
- SUSPECTED BUG: `_h_fail_charge` (375-390) mutates cash directly (`ca.balance -= amt`); for a NON-base currency it reduces
  `ca.base_value` by the converted amount but posts the journal (`dr 5800 / cr 1010:CCY`) with the LOCAL amount
  (388-389), while 1010:CCY is carried in base currency (fx.py docstring 3-7) -> ledger vs cash-account mismatch for any
  non-USD fail charge.

## 13. engines/corporate_actions.py (305) — cash dividends, coupons, maturities, defaults (VERIFIED where cited)
- `declare_upcoming(cause)` (37-56): for every security with `dividend_per_share > 0`, emits DIVIDEND_DECLARED for the
  synthetic ex-dates of this and next year (`market.dividend_ex_dates` = random anchor month/day, market.py:440-455) once
  `declared <= today`, plus news. Amount = snapshot price x yield / 4 (market.py:265) as modified by simulated earnings
  (macro.py:353-355). => dividends are INVENTED even in a real-price save (no real dividend feed).
- `process_day(cause)` (59-98): ex-date entitlement (`entitle_today` 100-118: holders DIVIDEND_ENTITLED, shorts
  DIVIDEND_OBLIGATION), pay date payment; bonds: recovery redemption for defaulted issues (83-88), coupons when `today in
  rolled coupon dates` (89-94), maturities (95-98). Coupons/maturities are only paid if the daily process runs ON that
  date (exact-date equality) (VERIFIED 91, 95).
- Coupons only for LONG bond positions (`pos.quantity <= 0` skipped, 81) — no manufactured coupon owed on a bond short
  (VERIFIED). Coupon amount uses the CURRENT `sec.coupon` (92); FRN coupons are reset daily (market.py:1198-1199), so an FRN
  pays the rate of the payment day, not the rate fixed at the period start (INFERRED consequence).
- `default_bond` (121-131); `_h_defaulted` (133-157): sets `sec.defaulted`, recovery, rating "D", MUTATES the last
  stored history bar in place (`b.close, b.bid, b.ask = rp`) and `_prev_close`, writes off accrued (dr 4300 / cr 1220).
- `_quantity_before_today(pf, sid)` (159-168): position minus today's trades from `pf.day_trade_ids` (record = ex).
- Handlers (171-305): `_h_declared`; `_h_entitled` (dr 1210 / cr 4200 at base amount); `_h_paid` (cash + dr 1010 /
  cr 1210, realised FX on non-base); `_h_obligation` (manufactured dividend dr 5400 / cr 2320); `_h_obligation_paid`;
  `_h_coupon` (true-up accrual to the full coupon, then cash; dr 1010 / cr 1220); `_h_matured` (principal, realized =
  principal - cost, reverse valuation adjustment 1150/4100).
- Cash maintenance style differs by handler (base-currency inflows `balance += amt` without `base_value`, e.g. 205, 271,
  299; some outflows also write `base_value`, e.g. 244-245). This is NOT a bug for the base currency: `CashAccount.base_value`
  is a property that returns `balance` when `is_base`, and its setter ignores writes for the base currency
  (VERIFIED domain/models.py:187-204). It is, however, 40+ separate direct cash mutations across engines (grep of
  `base_value +=/-=`: accruals, ccy_funding, collateral, world.py:418/428/593, commodity_desk, corporate_actions,
  corporate_events, fx, investors, otc, investment_banking, prime, private_credit, private_equity, repo, seclending,
  treasury) — the strongest argument for one cash module.
- SUSPECTED BUG (VERIFIED code): `_h_matured` never clears `pos.accrued_interest` / account 1220 (278-305). On a normal
  maturity the same-day coupon (process_day 91-94 runs before 95-98) clears it, but a BOND_CALL (corporate_events.py:277,
  "redeemed at call price plus accrued interest" per its news text) pays only `qty x call_px / 100` and leaves the
  accrual stranded in 1220.
- SUSPECTED BUG (VERIFIED code): short bond positions are skipped by the coupon/maturity loop (`pos.quantity <= 0`,
  line 81): a short bond never owes coupons and is never closed at maturity.

## 14. engines/commodity_desk.py (233) — calendar spreads, physical delivery, storage (VERIFIED where cited)
- Docstring (1-15). Constants `BUY_IN_PENALTY`, `SPREAD_MARGIN_RATE=0.35`, `PHYSICAL_SPREAD_BPS=50` (27-29).
- `place_spread` (60), `spread_analytics` (104) (not read); `deliver(pf, sec, pos, cause)` (117-155): at expiry in
  physical mode, closes the future at the settle via `system_order(..., allow_short=True)` (allow_short unused, see 3.2)
  and buys/sells PHYSICAL inventory at the settle; buy-in + 2% penalty when nothing to deliver.
- `process_day(cause, prev)` (158-173): storage cost = qty x last close x storage rate x calendar days/365 -> STORAGE_CHARGED.
- `_h_delivery` (206-220) and `_h_storage` (222-233) mutate cash directly (`ca.balance -= ...; ca.base_value -= ...`).
  SUSPECTED BUG: `_h_delivery` uses `p["currency"]` = the FUTURE's currency and subtracts the LOCAL penalty from
  `base_value` (214-216) -> wrong base value for a non-USD contract (e.g. Eurex/ICE/OSE futures).

## 15. engines/briefing.py (495) — the daily briefing (VERIFIED where cited)
- `build(cause)` (25-40): per portfolio, from `pf.nav_history[-1]`, emits DAILY_BRIEFING with nav, day_pnl,
  `day_return = day_pnl/(nav - day_pnl)` (32; ignores the snapshot's capital_flows -> wrong on a day with a
  contribution/withdrawal — INFERRED), P&L buckets, market summary, news, attention items, movers, regime label, orders
  summary, financing, collateral, short book, FX, derivatives, OTC, risk, macro, corporate, desk, missions, scenario.
  Called once per simulated day at simulation.py:137.
- `_market_summary` (85-105): SPY 1-day %, commodity front %, 10Y (`rates[7]`) and 2Y (`rates[3]`) changes, IG/HY, VIX —
  all computed from the last two entries of the per-day histories (`history[-1]` vs `[-2]`, `curves[-1]` vs `[-2]`).
- Section builders (136-240): `_collateral`, `_short_book`, `_option_events_today` (scans `w.events` backwards for
  today's sim_date), `_derivatives` (uses `w.options.aggregate_greeks(pf)` — a second Greeks aggregation besides
  risk.py), `_macro` (simulated macro state/releases/earnings/ratings), `_corporate`, `_risk` (reads
  `pf.risk_history[-1]`), `_otc`, `_fx` (balances + `fx.exposures`).
- `_attention(pf, snap)` (242-484): desk items, client RFQs, lending, treasury liquidity, collateral calls, recalls/buy-ins,
  borrow-rate spikes, repo maturities, negative cash, margin loan, FX forwards, failed settlements, breaches, expiring
  futures (331-336), settlements due next session (338-341: `net` sums `si.cash_amount` across ALL currencies without
  FX conversion — same mixed-currency flaw as risk.cash_ladder, VERIFIED), dividends/coupons (342-355; coupon estimate
  `qty x coupon / freq` at 355 duplicates corporate_actions.py:92), concentration > 25% of NAV for sandbox (356-362),
  working/expired orders (364-369), reviews/career, option expiries/assignments/margin moves (377-414), corporate events
  (415-429), macro releases/EARNINGS/ratings/defaults (430-452 — all SIMULATED content), OTC/CSA/counterparties
  (453-478), regime change (479-481).
- `_h_briefing` keeps the last 60 briefings in `pf.briefings` (486-495).
- The briefing is the natural source for the planned "Daily News" page but is tied to the day-close event and to the
  "next session" vocabulary throughout.

## 16. engines/live.py (457) — the EXISTING live-quote path (outside my file list; read because the owner wants live prices)
- Docstring (1-26): in a save that tracks the real market, LIVE tickets fill immediately at the latest Yahoo Finance
  quote ("up to 15 minutes delayed") through the ordinary `_fill`; resting live orders are swept on every quote refresh
  (`work()`); "What is stored is the fill itself (TRADE_EXECUTED carries the quote, its time and source), never a
  network result, so replay is exact and offline." Per-instrument quoting: equities direct; options = model surface at
  live underlying; futures = last close moved with the underlying's live quote; bonds = repriced off the live Treasury
  curve (13w, 5y, 10y, 30y yields shift the save's curve); DXY/VIX direct; commodity spot = front-month continuous; FX
  pair quote.
- `LiveDesk.available()` requires `w.market_source == "REAL"` and `market.real_feed` (65-67); `quotes(ids)` batched (98-126);
  `live_curve(raw)` (128-153); `quote(sec)` (155); `fx_mid` (254); `resting()`/`instructions()`/`pending()` (278-300);
  `fill(order, cause)` (302-312); `work(max_age_s, at)` sweeps resting live orders + due 15-minute instructions, emits
  LIVE_SWEEP as cause (314-367); `_work_one(order, q, cause, tick)` (388-457).
- `_quote_from(sec, raw)` (161-252) — how each class gets a live price (VERIFIED): bonds = last close + (price on live
  curve - price on save curve) (178-199, falls back to the last close when no yields); INDEX direct (200-205); options =
  `w.options.quote(sec, S_live, "live")` model price at the live underlying (206-215); futures = last close x (live
  underlying / reference spot) ratio — the basis is NOT live (216-244); equities/ETFs/ADRs/REITs/preferreds/crypto =
  Yahoo price x `yahoo_scale`, and the quote carries `day_volume`, `day_high`, `day_low` (245-251).
- KEY FACT: a live fill builds `Bar(current_date, live, live, live, live, volume=lb.volume, bid, ask)` where the spread is
  the LAST SESSION BAR's relative spread and the volume is the LAST SESSION BAR's volume (405-408, 445) — both SIMULATED
  quantities (synthetic spread_bps x regime spread_mult; simulated volume even in real mode, because `_pin_day` rescales
  prices but keeps `b.volume`, market.py:587-595). So the 20%-of-volume cap and the spread charged on a "live" fill are
  simulated, even though the live equity quote already carries Yahoo's `day_volume` (live.py:251) which is not used. The trade is booked with `trade_date = w.current_date` (trading.py:532) = the last processed session, not
  the wall-clock date (INFERRED: e.g. a Monday live fill before Monday's update is dated Friday).

---

## 17. FINDINGS FOR THE REPAIR

### 17.1 Everything tied to the simulated day advance (must be replaced or re-homed for "live prices, paper cash")
All VERIFIED at the cited lines unless marked.
1. Price generation: `MarketEngine.generate_day` (market.py:873-1087) — regimes (`REGIMES`, `TRANSITIONS`,
   `_regime_for_month`), factor model, curve random walk, spreads, dividends drop, synthetic OHLC/volume/bid/ask.
2. Pre-history: `bootstrap()` generates 260 business days of random path then rescales to the snapshot
   (market.py:537-553, 800-871); real saves overlay real closes (`apply_real_history`, 684-798). Risk VaR history
   (risk.py:176-202), realized vol (market.py:1253-1260), vol surfaces and the briefing all read these per-day series.
3. Universe snapshot: `finsim/data/universe.json` loaded at import (market.py:114-117); bonds/TIPS/FRN/sovereigns/
   structured get SYNTHETIC issue dates relative to world start and fake ISIN/CUSIP (market.py:250-366).
4. Sub-models stepped once per day inside generate_day: macro (`MacroModel.step`), corporate events
   (`CorporateEventModel.step`), FX (`FXModel.step`), commodities (`CommodityModel.step` + `bars_for_contracts`), securities
   lending (`LendingMarket.step`), dealers (`DealerModel.step`), MBS (`MBSModel.step`), vol surfaces (`VolSurfaceModel.step`),
   floaters/TIPS reset (`reset_floaters`), listings/expiries (`ensure_listings`). Each is seeded by (seed, name, date).
5. Order execution: `trading.work_orders` runs once per day against the day's bar (simulation.py:96); fills at the
   simulated OPEN unless `execute_at == "CLOSE"` (trading.py:386); DAY expiry by date comparison (trading.py:552-561,
   simulation.py:133); impact uses `w.market.regime().depth_mult` (trading.py:488) and realized vol of simulated history.
6. Daily batch jobs that must become on-demand/clock-driven: settlement (simulation.py:99-101), corporate actions incl.
   dividends/coupons/maturities on exact dates (corporate_actions.py:59-98), futures expiry + variation margin
   (simulation.py:108-109), FX settlement/forward marks/retranslation (fx.py:168-194), storage (commodity_desk.py:158-173),
   fail charges/buy-ins (corporate_events.py:350-373), `pnl.mark_all` + `pnl.snapshot` (simulation.py:120,128,131),
   `risk.process_day` (risk.py:430-446), briefing (briefing.py:25-40), reviews/missions.
7. Replay model: saves store the whole MARKET_CLOSE payload per day and replay through `ingest_close`
   (market.py:1090-1137, world.py:606). Under live prices the "market" becomes external; the event log would need to
   store the quotes used (live.py already stores fills with quote metadata, live.py:16-18, 446-447).
8. Invented content that would contradict a live market: simulated earnings + fundamentals edits, ratings, defaults
   (macro.py:234-276, 340-363), invented dividends (corporate_actions.py:37-56), corporate events (corporate_events.py:45-137),
   commodity supply events/inventory news (commodities.py:320-335), regime-change news (simulation.py:50-54), FOMC model.

### 17.2 What already exists for live prices (reuse candidates)
- `engines/live.py` LiveDesk (Yahoo quotes, live curve, LIVE fills via `_fill`, resting-order sweeps) — section 16.
- `MarketEngine.real_history` / `real_feed` / `real_targets` / `_pin_day` / `apply_real_history`, `_pin_fx_rates`,
  `_pin_real_macro`, `yield_anchor` (market.py:403-406, 563-569, 581-657, 684-798, 1151-1182, 1286-1344).
- FX LIVE spot deals via `w.live.fx_mid` (fx.py:69-96, 109-121).
- `trading._fill` is already price-source agnostic (takes a Bar + base + half) (trading.py:466-541).

### 17.3 Suspected bugs (evidence)
1. Real-mode leaks of simulated credit/earnings: section 9 (market.py:897-902 + macro.py:282 + market.py:1315-1344 +
   simulation.py:81-86). VERIFIED code path; effect INFERRED.
2. Live/real fills use simulated volume and spread (live.py:405-408, 445; market.py:587-595). VERIFIED.
3. `system_order(..., allow_short=...)` parameter is ignored and system orders skip all validation (trading.py:563-577).
   VERIFIED. Shorts can be created without a borrow by assignment, buy-in, liquidation paths.
4. Mixed currency units in `Lot`: `cost_per_unit` in security ccy, `cost_total` in base ccy (trading.py:648-650, 691-692,
   736-737). VERIFIED.
5. Bond short/cover accrued interest: in `_h_trade_executed` the short-sale branch credits only `gross_s - comm_s` to
   receivable 1200 (743) and the cover branch debits only `gross_c + comm_c` to payable 2100 (686), while the
   settlement instruction carries `net` incl. ALL accrued (trading.py:524, 750) and the long branch takes the full
   `accrued` (690). On a bond trade that is partly/wholly a short sale or a pure cover, 1200/2100 will not clear at
   settlement by the accrued amount. Now VERIFIED against settlement.py:154-183: settlement extinguishes 1200/2100 with
   `booked = SI cash_amount` (x trade fx_rate for foreign), i.e. the full net incl. ALL accrued, so 1200 is left with a
   credit residual (short sale) / 2100 with a debit residual (pure cover) equal to the short leg's accrued, and no
   accrued-interest liability is ever recognised for a short bond. Also INFERRED: for foreign-currency trades the trade
   books base amounts component by component (`money(gross*k)`, `money(accrued*k)`, `money(commission*k)`, trading.py:650)
   while settlement extinguishes `money(cash*k)` (settlement.py:159) -> possible 1-cent residuals in 1200/2100 — the
   planned ledger integrity check should tolerate or eliminate these.
6. Non-base-currency direct cash mutations with local amounts in base-carried ledger accounts:
   corporate_events.py:380-389 (fail charge journal), commodity_desk.py:214-219 (penalty base_value). VERIFIED.
7. `risk.cash_ladder` mixes currencies (risk.py:329-364). VERIFIED.
8. Foreign-currency security positions carry no FX factor in VaR/stress (risk.py:80-150). VERIFIED by absence.
9. Non-commodity, non-rates futures (BTC, VX, DX, Nikkei, FTSE ...) are all treated as SPY equity exposure
   (risk.py:93-94); every rates future uses a 6.5 duration (risk.py:60, 92). VERIFIED. Worked example (INFERRED
   arithmetic from the spec values): one SR3 at ~96.00 has notional 96 x 2,500 = 240,000, so risk.py books DV01 =
   -240,000 x 6.5 x 1e-4 = -156 per bp, whereas a real SR3 moves 25 per bp (0.01 x 2,500) — about 6x overstated; ZT
   (true duration ~1.9) is ~3.4x overstated; ZB/UB (duration ~15-20) are understated; non-USD bond futures are added to
   the USD rate factor.
10. FRN first-period cash flow uses a forward, contrary to its docstring (pricing.py:96-113). VERIFIED code.
11. `risk.blocks_order` treats any SELL against a long as risk-reducing even if it flips the book short (risk.py:411).
12. `briefing.build` day_return ignores capital flows (briefing.py:32). INFERRED from formula.
12a. Bond CALL leaves accrued interest stranded in 1220; short bond positions never pay coupons nor mature
    (corporate_actions.py:81, 278-305) — section 13. VERIFIED code.
12b. (Withdrawn after checking: USD `base_value` is a property equal to `balance`, models.py:196-204.) What remains is
    that cash is mutated directly in 40+ places (section 13), so the ledger integrity check must compare every
    `CashAccount` with its 1010:CCY ledger balance.
13. `quant/stats.sharpe_ratio` / `sortino_ratio` raise ZeroDivisionError on zero dispersion (stats.py:229, 240) —
    harmless today (not called by FinSim) but must be guarded if adopted.
14. `asset_metrics` aligns asset and market by position, not date (asset_metrics.py:213-221).
15. Pointer for the 10,000,000 bug (not my area): the default new-portfolio capital is `D(10_000_000)`
    (world.py:732; api/server.py:131 `body.get("capital", 10_000_000)`; demo.py:54) — a portfolio or desk created
    without an explicit capital gets exactly 10,000,000 (VERIFIED grep; link to the Treasury symptom INFERRED).
16. Pointer for the vanishing-trades bug (not my area): live fills are dated `w.current_date` (trading.py:532), the
    last processed session; `pf.day_trade_ids` is reset at world.py:624 and used by corporate_actions.py:164; how a
    reload treats events emitted between daily updates should be checked in world.py/store.py (INFERRED lead only).

### 17.4 Duplicate computations (consolidation candidates for the ONE accounting module)
- Cash projection: `trading.projected_cash` used in 10+ places (section 3.2) vs `risk.cash_ladder` (own projection) vs
  `fx.exposures`.
- NAV: `w.pnl.compute_summary(pf)` called twice per portfolio per day by risk (risk.py:303, 391) and again by the API.
- Sharpe/volatility/drawdown: careers.py:446-461, api/service.py:655-668, quant/stats.py:227-241,
  quant/asset_metrics.py:170-181; return definitions differ (careers.py:444 vs service.py:670 vs briefing.py:32).
- Drawdown from peak: risk.py:394, service.py:676/681, careers.py:457-461.
- Concentration: careers.py:358-359, service.py:679, briefing.py:356-362.
- FX exposure: risk.py:137-150 vs fx.py:196-209.
- Coupon amount: corporate_actions.py:92 vs briefing.py:355.
- Direct cash-balance mutations outside one cash module: trading.py:783, fx.py:212-224 (`_adjust_cash`),
  corporate_events.py:380-387, commodity_desk.py:214-217 and 227-231, corporate_actions.py:205/244/271/299,
  settlement.py:165/177, plus the 40+ `base_value` writes listed in section 13.
- Greeks aggregation: risk.py:96-108 (from `pos.greeks`) vs `w.options.aggregate_greeks(pf)` used by the briefing
  (briefing.py:168) — two aggregations (options.py not read).
- Bond pricing for prehistory, pinning and generation: three re-pricing loops (market.py:629-637, 761-781, 856-860, 963-971).
- `ensure_listings` called twice per generated day (market.py:954, 1004).

### 17.5 Surprising
- Even the "real tracking" mode is simulate-then-rescale: OHLC shapes, volumes and spreads are invented (market.py:587-595).
- `MarketState` docstring says "never persisted", but `state_dict()` is stored in every MARKET_CLOSE (simulation.py:42, 46).
- `apply_real_history` ends with `self._bar_cache.clear() if hasattr(self, "_bar_cache") else None` (market.py:798), but
  `_bar_cache` belongs to the OPTIONS engine (options.py:137, 268, 390-402, 1053), never to MarketEngine — so the line is
  dead code and the options bar cache is not invalidated by the real-history overlay (VERIFIED grep; any staleness
  effect INFERRED, likely nil because the overlay runs at load, world.py:316).
- `SHARES_FALLBACK_M` has a duplicate "SHOP" key (market.py:126, 130).
- The quant/ package is not used by FinSim at all (section 6.1).

Coverage: every file in my area was read in full — market.py, trading.py, pricing.py, macro.py, global_rates.py, fx.py,
fx_market.py, commodities.py, commodity_desk.py, corporate_actions.py, corporate_events.py, vol.py, risk.py, briefing.py,
quant/asset_metrics.py, quant/portfolio.py, quant/stats.py, quant/timeseries.py, quant/regression.py. Outside my list I also read for context: simulation.py 1-144, live.py 1-457 (except 1-58 header
partly), world.py 774-805, careers.py 425-484, api/service.py 640-684, settlement.py 150-189, domain/models.py 186-206.

STATUS: COMPLETE
