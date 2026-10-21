# FinSim map part: DERIVATIVES

Scope: listed options, futures, OTC (dealer RFQ, FX/equity options, forwards, IRS/TRS/XCCY, resets, payments),
structured products, MBS, and the quant pricing helpers they use. READ-ONLY map, written 2026-10-20 from 12:30 ET.
Paths are relative to `C:\Users\logan\intelligence-terminal\finsim\finsim\` unless stated.
Each claim is tagged VERIFIED (code read at path:line) or INFERRED.

(This file is written incrementally; the STATUS line at the end says what was and was not read.)

## Index - files in my area and what each does (VERIFIED, all read in full)

| file | lines | role | section |
|---|---|---|---|
| `engines/futures.py` | 255 | futures VM, initial margin sweep to 1300, expiry auto-close / delivery, (dead) margin calls | 1 |
| `engines/options.py` | 1,054 | listed options: listings, model quotes, synthetic bars, Greeks, margin, exercise/expiry/assignment, strategies, splits | 2 |
| `engines/options_pricing.py` | 155 | BSM, CRR American tree, Greeks, bounds, implied vol (unused) | 2.4 |
| `engines/otc.py` | 1,589 | OTC desk: RFQ, booking, valuation, lifecycle, CSA, exposure, termination, credit events, dealer default | 3 |
| `engines/otc_extra.py` | 851 | 12 extra OTC products (var/vol/div/inflation swaps, FX swap, exotics, perps, notes) | 3.6 |
| `engines/otc_pricing.py` | 283 | curve utilities, swaps, FRA, Black-76 caps/swaptions, CDS, TRS, commodity swap pricers | 3.4 |
| `engines/mbs.py` | 362 | agency MBS TBAs/pools: prepayment model, pricing, monthly paydowns | 4 |
| `engines/structured.py` | 55 | static seed of 25 CLO/CMBS/RMBS/ABS tranches (data only) | 5 |
| `quant/derivatives.py`, `swaps.py`, `fixed_income.py`, `volatility.py`, `credit.py`, `stochastic.py` | 675 | reference equation library; NOT used by FinSim engines (used by tests and by finsim2) | 6 |

Data flow in one line: user command (order / RFQ / exercise / strategy) -> engine emits an event -> handler mutates portfolio state
and posts ledger lines via `w.post` -> end of each simulated day `simulation.run_daily_process` runs the lifecycle steps in section 0
(expiry, VM, OTC lifecycle and marks, option expiry/assignment, MTM, margin sweep). Marks always read the simulated market objects
(`w.market.last_bar`, `w.market.vol`, `w.market.curve()`, `w.market.fx`) - see 7.4/7.5.

---------------------------------------------------------------------------------------------------

## 0. The day-advance spine that drives every derivative lifecycle (read for context)

`engines/simulation.py` `run_daily_process(d)` (lines ~98-139) is the end-of-day pipeline; `advance_one_day()` (141-143)
calls it for `calendar.next_business_day(current_date)`. VERIFIED. The derivative steps, in order:

| step (simulation.py line) | call | what it does |
|---|---|---|
| 108 | `w.futures.expire_contracts(closing)` | auto-close / physically deliver futures whose `expiry == today` |
| 109 | `w.futures.daily_settlement(closing)` | variation margin on every futures position |
| 118 | `w.otc.process_day(closing, prev)` | OTC resets, payments, margin, expiries (see section 3) |
| 119 | `w.options.process_day(closing)` | listed-option expiry, random early assignment, margin recompute, strategy status |
| 120, 128 | `w.pnl.mark_all(closing)` | MTM of options (model mid) and cash securities; futures skipped |
| 121 | `w.futures.sweep_margin(closing)` | moves cash <-> account 1300 to match futures IM + options margin |
| 101, 122 | `w.treasury_cover_overdrafts(closing)` | (not my area) covers negative cash; see finding F-FUT-1 |
| 105 | `w.mbsdesk.process_day(closing)` | MBS factor/paydown processing (section 5) |

Other callers outside the day loop (VERIFIED by grep):
- `world.py:610` `self.options.ensure_listings(d)`; `engines/market.py:954, 1004, 1095` call `self.ensure_listings(d)` (market's own listing refresh, not read).
- `engines/live.py:455-456` `w.options.recompute_margin(pf, ev())` + `w.futures.sweep_margin(ev())` (live mode path).
- `engines/prime.py:184-185` same pair (recompute margin then sweep).

---------------------------------------------------------------------------------------------------

## 1. FUTURES - `engines/futures.py` (255 lines, read in full)

### 1.1 What the file does
Daily settlement (variation margin, VM), initial margin (IM) sweep into ledger account 1300, expiry auto-close or
physical delivery, and an (unwired - see F-FUT-1) margin-call / forced-liquidation path. VERIFIED futures.py:1-13.

### 1.2 Key functions and data
- Constants: `MARGIN_CALL_GRACE_DAYS = 3` (25); `REGIME_MARGIN_MULT` keyed by the SIMULATED regime
  {NORMAL_GROWTH 1.0, RATE_CUTTING 1.0, RATE_HIKING 1.15, RECESSION 1.4, LIQUIDITY_STRESS 2.0} (26). VERIFIED.
- `FuturesEngine.register()` (33-39): handlers FUTURES_SETTLED -> `_h_settled`, MARGIN_SWEPT -> `_h_swept`,
  MARGIN_CALL -> `_h_margin_call`; FORCED_LIQUIDATION and CONTRACT_EXPIRED are no-ops. VERIFIED.
- `margin_multiplier() -> float` (42-43): `REGIME_MARGIN_MULT[w.market.state.regime]`. VERIFIED. Tied to simulated regime.
- `initial_margin_per_contract(sec) -> Decimal` (45-47): `last_bar(sec.id).close * multiplier * sec.margin_pct * regime_mult * fx.k(ccy)`. VERIFIED.
- `required_margin(pf) -> Decimal` (49-68): sum of IM over open, unexpired futures; commodity long/short pairs on the same
  `sec.underlying` get the calendar-spread offset `paired * min_im * (2 - SPREAD_MARGIN_RATE)` subtracted
  (`SPREAD_MARGIN_RATE` imported from `commodity_desk`). VERIFIED.
- `total_required(pf)` (70-72) = `required_margin(pf) + pf.options_margin` (listed-options margin is held in the same
  clearing account 1300). VERIFIED.
- `expire_contracts(cause)` (75-89): for each future with `sec.expiry == current_date`: physical commodity + `pf.physical_delivery`
  -> `w.cdesk.deliver(...)`; else emit CONTRACT_EXPIRED and `w.trading.system_order(pf, sec, opposite side, |qty|, ...)`. VERIFIED.
- `daily_settlement(cause)` (91-110): for each futures position with qty != 0 or day fills:
  `settle = last_bar(sec).close`; `carried = pos.quantity - sum(day_fill qty)`;
  `VM = carried*(settle - pos.settlement_price)*mult + sum(fill_qty*(settle - fill_price)*mult)`; emits FUTURES_SETTLED with
  `variation_margin`, `notional`, `contracts_after`. VERIFIED.
- `sweep_margin(cause)` (112-121): `delta = total_required(pf) - ledger.balance("1300")`; emits MARGIN_SWEPT(amount=delta). VERIFIED.
- `_check_margin_call(pf, cause)` (123-143), `_force_liquidate` (145-179), `_settle_position_now` (181-193): margin call after cash < 0,
  forced liquidation after 3 days. **Never called** - see F-FUT-1. VERIFIED by grep (only definition at futures.py:123).
- Handlers:
  - `_h_settled(ev)` (196-226): sets `pos.settlement_price = pos.mark = settle`, clears `pos.day_fills`, adds VM to
    `pos.variation_margin_total`, `pos.day_variation_margin`, **`pos.realized_pnl`**; `pos.market_value = 0`; `pos.notional`.
    Base ccy: `pf.cash_account(ccy).balance += vm`, `record_cash_movement(... "VARIATION_MARGIN")`, ledger Dr 1010:ccy / Cr 4400 (gain)
    or Dr 4400 / Cr 1010:ccy (loss). Foreign ccy: `w.fx._adjust_cash(pf, ccy, vm, ...)` returns base amount; realized/VM totals
    trued up by `base - vm`; ledger lines post the BASE amount against `1010:{ccy}`. VERIFIED.
  - `_h_swept(ev)` (228-243): `pf.cash_account(ccy).balance -= amt`; recomputes each futures `pos.initial_margin` from the
    CURRENT `last_bar` (not from the payload); cash movement "INITIAL_MARGIN"; ledger Dr 1300 / Cr 1010:ccy (post) or reverse (release). VERIFIED.
  - `_h_margin_call(ev)` (245-255): creates/updates `MarginCall` in `pf.margin_calls`. VERIFIED.

### 1.3 Trade side (in `engines/trading.py`, read only the futures part)
- Order validation (trading.py:146-147): a future with `expired` or `expiry <= current_date` is rejected
  ("has expired / is in its last session"). Futures margin check (156-165): `initial_margin_per_contract(sec) * q` must pass
  `w.prime.affordable` unless the order reduces the position. VERIFIED.
- `_apply_futures_trade(pf, pos, sec, trade, ev)` (trading.py:757-787): signed qty; average cost `pos.average_cost_future`;
  `pos.settled_quantity = new` immediately; first fill sets `pos.settlement_price = trade.price` if it was 0;
  appends `{"quantity", "price", "trade_id"}` to `pos.day_fills`; trade status CLEARED; ONLY the commission hits cash/ledger
  (Dr 5000 / Cr 1010:ccy). No realized P&L at trade time - all P&L comes through VM. VERIFIED.
- `pnl.mark_position` returns immediately for futures (pnl.py:38-39); NAV/MV sums exclude futures (pnl.py:142, 160-161, 174). VERIFIED.

### 1.4 Data flow
`order -> trading._apply_futures_trade` (day_fills, settlement_price seed) -> day close: `expire_contracts` (system_order close)
-> `daily_settlement` (FUTURES_SETTLED -> `_h_settled`: cash, 1010/4400, pos.realized_pnl) -> `options.process_day` ->
`pnl.mark_all` -> `sweep_margin` (MARGIN_SWEPT -> `_h_swept`: cash, 1300). VERIFIED (simulation.py:108-121).

---------------------------------------------------------------------------------------------------

## 2. LISTED OPTIONS - `engines/options.py` (1,054 lines, read in full) + `engines/options_pricing.py` (155 lines, read in full)

### 2.1 What the files do
- `options.py`: contract listing (chains), quotes (theoretical value from the SIMULATED vol surface + a spread), synthetic
  session bars, position Greeks, transparent margin, exercise / expiry / random early assignment, multi-leg strategies,
  cash-merger settlement, split adjustment. VERIFIED options.py:1-21.
- `options_pricing.py`: BSM (European), CRR binomial with control variate (American), Greeks, no-arbitrage bounds,
  bisection implied vol. Pure functions. VERIFIED options_pricing.py:1-8.

### 2.2 Contracts and chains
- Constants: `MULTIPLIER=100`, index `SPX` built from `SPY` x 10 (`INDEX_SOURCE`, `INDEX_FACTOR`), `CRYPTO_MULTIPLIER=1`,
  `STRIKE_RANGE=6` (13 strikes) / `LITE_STRIKE_RANGE=4`, `OPTION_COMMISSION=0.65`, options `REGIME_MARGIN_MULT`
  (1.0/1.0/1.1/1.25/1.5), `SPREAD_BY_TIER`, 16 `STRATEGY_TEMPLATES` (options.py:39-68). VERIFIED.
- Contract id = `under + yymmdd + C|P + strike` with "." replaced by "_" (`contract_id`, 129-131); parsed back by `CONTRACT_RE` (299). VERIFIED.
- `optionable()` (154-158): listed-option names from `vol.has_listed_options`, large USD crypto with ADV x price >= 1e9,
  the `INDICES`, plus `optionable_futures()` (160-174: front two unexpired contracts per commodity / financial code with
  >= 20 business days left). VERIFIED.
- `expiries(d)` (204-226): next 4 monthly third Fridays (rolled back to a business day) + 2 quarterlies. VERIFIED.
- `ensure_listings(d) -> int` (228-270): for each optionable underlying, ATM from `underlying_level` (last bar close),
  strikes `atm +/- srange*step`; creates `Security` objects in `w.securities` via `_contract` (272-297) and calls
  `w.market.vol.init_underlying(...)` when the vol state is missing; marks contracts with `expiry < d` as `expired`. VERIFIED.
  Called from `world.py:610` and `market.py:954/1004/1095`. VERIFIED (grep).
- `_contract(...)` (272-297): equity/ETF = AMERICAN/PHYSICAL, deliverable `{security_id, quantity: multiplier}`; index =
  EUROPEAN/CASH with `index_level_source`/`index_factor`; options on futures deliver 1 future (`deliverable.future=True`),
  multiplier = futures multiplier; currency = the underlying's currency. VERIFIED.
- `list_contract(cid)` (301-322): re-lists a contract from its id if a position/order refers to one not in `w.securities`. VERIFIED.
- `_listing_volume` (324-334) and quote volume / OI (379-381) are `random.Random(seed|...)` values - SIMULATED. VERIFIED.

### 2.3 Pricing used for marks (the chain, the ticket, MTM, margin)
- `model_inputs(sec, S=None) -> (S, T, r, q, sigma)` (342-350): `S = underlying_level` (= `w.market.last_bar(under).close`, x10 for
  SPX; options.py:180-185), `T = days/365` to expiry, `r = interp_rate(curve, max(0.02,T))` from `w.market.curve()` or
  `curve_for_ccy(ccy)` (337-340), `q = r` for options on futures else `securities[src].dividend_yield` (static field),
  `F = S*exp((r-q)T)`, `sigma = w.market.vol.iv(under, K, F, T)` (SIMULATED vol surface state in `engines/vol.py`, not in my
  file list). VERIFIED.
- `theo(sec, S)` (352-359) -> `options_pricing.greeks(...)` + S, T, r, q. `quote(sec, S, session)` (367-385): mid = theo price,
  half-spread = `max(tick/2, mid*spread_pct/2)`, `spread_pct` (361-365) = tier base x (1+6|ln K/S|) x (1+0.5 min(1,T)) x
  `w.market.regime().spread_mult` capped 0.6; cached per (id, date, session:S). VERIFIED.
- `option_bar(sec)` (387-403): synthetic OHLC bar from the underlying's open/high/low/close bar - this is what orders fill
  against. VERIFIED.
- MTM: `pnl.mark_position` uses `w.options.quote(sec)["mid"]` (pnl.py:46-48) and stores Greeks on the position (pnl.py:49-50). VERIFIED.
- Live mode: `engines/live.py:206-215` prices an option as `w.options.quote(sec, S_live, "live")` - i.e. still the model mid on the
  simulated vol surface, only the spot is live. VERIFIED.

### 2.4 Greeks and signs (`options_pricing.py`)
- `bsm(type,S,K,T,r,q,sigma)` (25-33): standard BSM with continuous yield q; T<=0 returns intrinsic. VERIFIED.
- `bsm_greeks` (90-109): call delta = `e^{-qT}N(d1)` (0..+1); put delta = `-e^{-qT}N(-d1)` (-1..0); gamma = `e^{-qT}pdf/(S sigma sqrtT)` (>0);
  vega = `S e^{-qT} pdf sqrtT / 100` (>0, per 1 vol point); theta per CALENDAR day (/365, normally <0); rho per 1bp (/10000;
  call >0, put <0). VERIFIED.
- `crr_tree` (36-74): 60-step CRR, early exercise, control variate `cv = bsm - euro_tree`; delta/gamma read from steps 1-2;
  theta `(lvl2[1] - price0)/(2dt)/365`. VERIFIED. See F-OPT-5.
- `price(type, style, ...)` (81-87): AMERICAN call with q<=1e-9 -> BSM closed form; other AMERICAN -> tree; EUROPEAN -> BSM. VERIFIED.
- `greeks(...)` (112-129): American (non-trivial) = tree delta/gamma/theta + finite-difference vega (+/-1 vol pt)/2 and rho (+/-1bp)/2;
  returns `price, delta, gamma, vega, theta, rho, intrinsic, extrinsic, iv` where `iv` is just the INPUT sigma. VERIFIED.
- `bounds_ok` (132-144) no-arbitrage check; `implied_vol` (147-155) bisection 0.01..3.0, 60 iterations. VERIFIED.
- Position Greeks: `position_rows` (460-477) multiply per-share Greeks by signed contracts x multiplier, so a SHORT position
  shows negated delta/gamma/vega/theta/rho; `dollar_delta = delta*n*M*S`. `aggregate_greeks` (479-488) sums by underlying. VERIFIED.

### 2.5 Margin (listed options)
- `margin_requirement(pf, extra=None) -> (total, detail)` (495-584): per underlying, in order: (1) covered calls against
  settled+pending shares less pledged (requirement 0); (2) call spreads vs long calls of the SAME expiry: width x contracts x
  multiplier x regime; (3) naked call: `max(prem + 20%S - OTM, prem + 10%S)`; puts: put spreads by width, naked put
  `max(prem + 20%S - OTM, prem + 10%K)`; all x `REGIME_MARGIN_MULT[w.market.state.regime]`; non-USD converted by `fx.k(ccy)`. VERIFIED.
- `_futures_option_margin` (586-625): short call covered 1:1 by long futures (short put by short futures); spreads by width;
  naked = `(premium*M + futures IM) x left x regime`. VERIFIED.
- `incremental_margin` / `incremental_margin_release` (627-636), `recompute_margin` (638-646) emits OPTIONS_MARGIN_COMPUTED only when
  total or detail changed; handler `_h_margin` (955-964) sets `pf.options_margin`, `pf.options_margin_detail`,
  `pos.margin_requirement`, `pos.covered_by_shares`. The cash movement happens in `futures.sweep_margin` (account 1300). VERIFIED.

### 2.6 Exercise, expiry, assignment (all trades go through `trading.system_order`)
- `trading.system_order(pf, sec, side, qty, cause, reason, forced, fixed_price=None, allow_short=False, commission_free=False)`
  (trading.py:563-577) emits ORDER_ENTERED and fills IMMEDIATELY at the bar close +/- half spread, or at `fixed_price`. VERIFIED.
- `exercise(pf, contract_id, quantity)` (659-679): long AMERICAN only, not expired; emits OPTION_EXERCISED; handler
  `_h_exercised` (934-947, skipped on replay) -> cash-settled: close at intrinsic; physical: `_deliver` (system_order in the
  underlying at the STRIKE, allow_short) + `_close_option` at price 0, commission free. VERIFIED.
- `process_day(cause)` (695-724): (a) every option position with `sec.expiry == today` -> `_expire`; (b) every SHORT AMERICAN
  position not expiring today -> `_maybe_assign`; (c) `recompute_margin`; (d) strategy status FILLED/EXPIRED. VERIFIED.
- `_expire` (726-750): intrinsic from `underlying_level` (last close); < 0.01 -> WORTHLESS, close at 0; CASH style -> close at
  intrinsic (premium flows T+1 via normal settlement); physical -> long AUTO_EXERCISED / short ASSIGNED_AT_EXPIRY:
  `_deliver` at strike then close at 0. VERIFIED.
- `_maybe_assign` (760-794): RANDOM early assignment. Probability: puts `0.01 + 0.15*min(1, 5*depth)*(1 or 0.2)`, calls
  0.02/0.005, 0.85 when a cash dividend with ex-date = next business day exceeds extrinsic; draw from
  `random.Random(seed|assign|id|date)`; partial quantity from `rng.choice([0.25,0.5,1.0])` unless deep. VERIFIED.
- `settle_underlying_at(under, price, ...)` (909-927): cash merger -> all contracts settle at intrinsic, delisted. VERIFIED.
- `_h_split` (981-1054): rescales the underlying's bar HISTORY, `_prev_close`, positions, lots, loans, pledges, lends,
  open settlements; option strikes /ratio; whole ratio -> contracts x ratio, else deliverable/multiplier adjusted. VERIFIED.

### 2.7 Packages (strategies) - all-or-none is a PRE-CHECK, not an atomic fill
- `place_strategy(pf, type, underlying, expiry, strikes, quantity, net_limit=None, expiry2=None, tif="DAY")` (797-841): builds
  legs from `STRATEGY_TEMPLATES`; checks contracts are listed; checks shares for COVERED_CALL/COLLAR; affordability of
  `incremental margin + net debit (ask/bid)` via `w.prime.affordable`; emits STRATEGY_ENTERED then enters EACH LEG AS A
  SEPARATE MARKET ORDER (`w.trading.enter_order(... "MARKET" ...)`, 839-840). VERIFIED. Legs link back through ORDER_ENTERED ->
  `link_leg` (648-656). Status becomes FILLED only when all leg orders are FILLED, EXPIRED when all EXPIRED/CANCELLED (717-724) -
  there is no state for "some legs filled, others not". VERIFIED.
- Execution: `trading.work_orders` (trading.py:327-347) groups working orders by `strategy_tag`; `_strategy_ok` (349-366) checks
  every leg's session depth (`PARTICIPATION_CAP`) and the package net at the OPEN against `net_limit` ("all-or-none" in its
  messages); if OK, each leg is then `_evaluate`d ONE BY ONE (346-347). VERIFIED. Whether a later leg can still be rejected by
  `_evaluate` after an earlier leg filled was not traced (INFERRED risk).
- `strategy_net_at` (851-861) uses `option_bar` open/close +/- half spread; `strategy_analytics` (863-906): payoff grid 0.4S..1.6S
  (401 points), max gain/loss, breakevens, greeks, margin detail. VERIFIED.

---------------------------------------------------------------------------------------------------

## 3. OTC DERIVATIVES - `engines/otc.py` (1,589 lines, read in full), `engines/otc_extra.py` (851, read in full), `engines/otc_pricing.py` (283, read in full)

### 3.1 What the files do
- `otc.py` `OTCEngine`: RFQ to dealers, trade booking under ISDA/CSA netting sets, valuation, daily lifecycle (fixings,
  coupons, resets, expiries), CSA VM/IM, exposure/PFE, termination, credit events (CDS), dealer default. Core products:
  IRS, OIS (=IRS 12m/12m), FRA, CAP/FLOOR, SWAPTION, XCCY, TRS, CDS (single name + CDX/iTraxx), COMMODITY_SWAP,
  EQUITY_OPTION, FX_OPTION, EQUITY/COMMODITY/FX_FORWARD, NDF (=FX_FORWARD for NDF currencies). VERIFIED otc.py:1-17, 36-55.
- `otc_extra.py` `ExtraProducts`: VARIANCE_SWAP, VOL_SWAP, DIVIDEND_SWAP, INFLATION_SWAP, FX_SWAP, BARRIER_OPTION,
  DIGITAL_OPTION, ASIAN_OPTION, CRYPTO_PERP, PPN, REVERSE_CONVERTIBLE, AUTOCALLABLE (fair/norm/terms/value/lifecycle/describe/upcoming);
  the core engine routes them (`otc.py:152-154, 383-384, 536-537, 648-649, 724-725, 940-942`). VERIFIED.
- `otc_pricing.py`: pure pricing functions "over the simulated curves" (docstring, line 1). VERIFIED.

### 3.2 Accounting model (as written in the code)
- Each trade carried at dirty PV: positive PV in 1800 (asset), negative in 2800 (liability); daily change to 4900
  (`_h_marked`, otc.py:1411-1431, which also REPLACES `t.analytics` and `t.state` from the payload). VERIFIED.
- Every cash flow: `_h_cashflow` (1438-1462): per leg `cash_account(ccy).balance += amt`, `base_value += base`
  (`fx.to_base` for non-base), `record_cash_movement(... "OTC" ...)`, Dr `1010:ccy` (base amount) / Cr 4910 (net base);
  `t.realized += net_base`; appended to `t.cashflows`. VERIFIED. Premiums, upfronts and notional exchanges at inception
  are booked through this same path as "initial_cashflows" (`_h_opened`, 1405-1407, derived events). VERIFIED.
- Close / maturity / expiry: `_h_terminated` (1464-1479) reverses the trade's 1800/2800 balance against 4900 and sets
  `t.mtm = 0`; the settlement cash (if any) is a separate OTC_CASHFLOW. VERIFIED.
- CSA: `_h_csa_moved` (1481-1505): VM_RECEIVED -> cash += amt, Dr 1010 / Cr 2450; VM_POSTED -> `w.collateral.move_cash(pf, amt,
  "CSA:<dealer>")`; IM_POSTED -> `move_cash(... "IM:<dealer>")`; IM_RECEIVED -> memo only (`csa.im_received`). VERIFIED.
  (`collateral.move_cash` is outside my area - INFERRED to post 1400 vs cash per the docstring otc.py:13-16.)

### 3.3 RFQ and booking
- `fair(product, params) -> {unit, mid, pv, ...}` (379-480): IRS par rate (bp), FRA forward (bp), cap/floor & swaption PV
  (Black-76 on `rate_vol()`), XCCY market basis, TRS base spread from `TRS_BASE_SPREAD_BPS[regime]`, CDS upfront at
  `reference_spread_bps`, commodity strip average, equity option BSM (EUROPEAN) with `_eq_vol`, FX option Garman-Kohlhagen
  with `fx_vol`, forwards from `_forward_level`. VERIFIED.
- `request_quote(pf, product, params) -> RFQ` (489-530): mandate + hard-risk checks; `_norm_params`; dealer quotes from
  `quote_half_width(product, dealer, regime, stress)` plus dealer skew; in LIQUIDITY_STRESS a stressed dealer declines with
  probability 0.5 using `random.Random(seed|rfq|pf|product|date|n)`; RFQ `expires = next_business_day(current_date)`;
  emits RFQ_REQUESTED. VERIFIED.
- `execute_rfq(pf, rfq_id, dealer) -> OTCTrade` (686-720): `_build_terms`; independent amount `im = csa.im_pct[product] * notional`
  plus positive upfront must pass `w.prime.affordable`; emits OTC_TRADE_OPENED then RFQ_EXECUTED. VERIFIED.
- `_build_terms` (722-810) -> `(terms, notional, ccy, start, maturity, initial_cashflows)`: premiums for cap/floor/swaption/
  equity & FX options; XCCY initial NOTIONAL_EXCHANGE (pay USD notional, receive foreign, or reverse; 755-758); CDS upfront. VERIFIED.
- `open_client_trade` (812-825) books a client-won trade at the desk's level with no RFQ. VERIFIED.

### 3.4 Valuation (`value(pf, t, asof=None) -> (pv_base, analytics, state)`, otc.py:141-209) and its live-data inputs
| product | pricer | inputs today (all simulated) | VERIFIED at |
|---|---|---|---|
| IRS/OIS | `px.swap_pv`, `swap_dv01`, `par_rate` | `w.market.curve()` zero curve; past fixings in `t.fixings` | otc.py:155-162; otc_pricing.py:81-122 |
| FRA | `px.fra_pv` | curve, fixing | otc.py:163-168 |
| CAP/FLOOR | `px.cap_pv` (Black-76 caplets) | curve, `rate_vol()` = `px.rate_vol(regime, vol_index)` | otc.py:96-97, 169-174; otc_pricing.py:164-188 |
| SWAPTION | `px.swaption_pv` (Black-76 on annuity) | curve, `rate_vol()` | otc.py:175-182 |
| XCCY | `_xccy_value` | curve (USD), `fx.spot[ccy]`, flat `fx.rate[ccy]`, `market_basis(ccy)` = hard-coded base x regime multiplier | otc.py:307-346 |
| TRS | `px.trs_pv` | `last_bar(security).close`, reset price, dividends/financing accrued in `t.state` | otc.py:185-190 |
| CDS | `px.cds_pv` flat hazard; PV adjusted by accrued premium | `reference_spread_bps` = issuer bond spread x (current IG/HY spread / FIRST curve's spread `w.market.curves[0]`) or index fraction | otc.py:99-112, 191-200 |
| COMMODITY_SWAP | `px.commodity_swap_pv` | `w.market.curve_for(code)` futures strip, spot, realised averages in state | otc.py:348-376 |
| EQUITY_OPTION | `options_pricing.bsm` (European) | `_eq_inputs`: S = `options.underlying_level` (last close), r from curve DF, q = static `dividend_yield`; sigma = `w.market.vol.iv` (simulated surface) or `sigma_annual` | otc.py:212-247 |
| FX_OPTION | `bsm` with r_f as yield (Garman-Kohlhagen) | `fx.spot`, `fx.rate`, `fx_vol(ccy)` = static `FX_VOL` table x `FX_VOL_REGIME[regime]` | otc.py:249-271 |
| FORWARDS | `_forward_value` = units x (F-K) x DF | equity: S e^{(r-q)T}; commodity: futures curve month; FX: `fx.forward` (CIP) | otc.py:273-305 |
| extras | see 3.6 | surface ATM vol, simulator CPI, macro inflation, futures curve, last closes, MC | otc_extra.py:487-627 |

`otc_pricing` conventions (VERIFIED otc_pricing.py:29-77): `df` from semiannually-compounded zero rates; `fwd_rate` simple ACT/360;
`yearfrac` "30/360" (actually caps both days at 30 = 30E/360, see F-OTC-8), ACT/360, ACT/365; `schedule` rolls payment dates and
avoids a sliver stub; `years = days/365`. Black-76 (137-161), `rate_vol` (164-167), CDS legs with half-period accrual on default
and mid-period protection (210-232), `hazard_from_spread = s/(1-R)` (206-207), `trs_pv` (254-257), `commodity_swap_pv` (260-270).
Signs: `swap_pv` is receiver-fixed positive, negated for payer (107-110) - payer DV01 > 0 (self-check line 282). VERIFIED.

### 3.5 Daily lifecycle (`process_day(cause, prev)`, otc.py:890-903) - entirely day-advance driven
Order per portfolio: expire OPEN RFQs whose `expires <= today` -> `_lifecycle` for each open trade -> `mark` each open trade
(OTC_TRADE_MARKED) -> `_margin` (CSA). VERIFIED. `_lifecycle` (931-1147), `days = max(1, (today - prev).days)`:
- IRS: fix every float period with `s <= today` not yet fixed at `term_rate(float_months)` (= today's zero curve point, NOT a
  historical fixing); pay every FLOAT/FIXED period with `pay_date <= today` not in `t.state["paid"]`; MATURED at maturity. (943-965)
- FRA: on/after start: fix, settle discounted `N(f-K)tau/(1+f tau)`, MATURED. (966-975)
- CAP/FLOOR: fix, pay caplet intrinsic at pay date. (976-992)
- SWAPTION: on/after expiry: ITM -> cash settle (or if we are SHORT, always cash) or open a new IRS via OTC_TRADE_OPENED
  (physical); else EXPIRED. (993-1018)
- XCCY: fix USD leg at `term_rate`, foreign leg at flat `fx.rate[ccy]`; pay two-currency XCCY_INTEREST; final NOTIONAL_EXCHANGE at maturity. (1019-1044)
- TRS: financing accrues `notional x (policy_rate + spread) x days/360` (skipped on trade date); dividends added when a
  CASH_DIVIDEND `ex_date == today`; reset cash flow `units(price - reset_price) + divs - financing` at each reset date;
  then reset price/notional. (1045-1075)
- CDS: premium accrues `N x running x days/360`; quarterly CDS_PREMIUM cash flows; accrued reduced by the paid amount. (1076-1091)
- COMMODITY_SWAP: adds ONE spot observation per processed day to the current period's running average; settles each period
  at its pay date. (1092-1118)
- EQUITY/FX_OPTION: at maturity, cash settle intrinsic vs last close / FX spot. (1119-1137)
- FORWARDS: at maturity, cash settle `units(settle - K)` (equity/FX at spot, commodity at the curve). (1138-1147)
- Extras (`otc_extra.lifecycle`, 630-800): variance/vol swaps add one squared log return of the last two closes per processed
  day; dividend swap realises dividends whose `ex_date == today`; inflation swap settles on simulator CPI; FX swap far leg;
  barrier touch checked on the day's close only; digital/asian settle at maturity (asian adds one spot per processed day);
  CRYPTO_PERP pays funding `units x spot x 0.0003 x days` EVERY processed day; PPN/RC/autocallable observations and redemption.
- `t.state` / `t.fixings` are MUTATED IN THE COMMAND PHASE (e.g. 963, 971, 1048-1073, 1086-1089, 1096-1116; extras 647, 667, 701-706,
  736, 755, 764, 770-771) and persisted only through the next OTC_TRADE_MARKED payload `state` (comment 963). VERIFIED.

### 3.6 Extras pricing summary (otc_extra.py, VERIFIED)
- VARIANCE/VOL: fair strike = surface ATM vol x 1.03 / x 0.98 (257-263); PV blends realised variance so far with implied for
  the remaining sessions (495-518).
- DIVIDEND_SWAP: expected = count of simulated ex-dates x `dividend_per_share` (334-339).
- INFLATION_SWAP: simulator CPI `w.market.macro.cpi_index`; breakeven `0.5*macro inflation + 0.5*2.5%` (140-142, 341-343, 529-541).
- FX_SWAP: near leg exchanged at inception (437-447), PV of the far leg `sign*amt*(F-K)*DF` (542-550).
- BARRIER (Reiner-Rubinstein, 47-81), DIGITAL (84-88), ASIAN (Turnbull-Wakeman, sigma/sqrt3, 91-109).
- CRYPTO_PERP PV `units*(spot-entry)` (602-609). PPN = zero + participation x call; RC = bond + coupon - down-and-in put;
  AUTOCALLABLE = Monte Carlo 1,500 paths, monthly steps, seeded by `seed|autocall|sid|date|round(S,2)` (345-391).

### 3.7 CSA margining and default (otc.py:1150-1223, 1507-1579)
- `required_im(pf, dealer, extra)` = sum `im_pct[product] x notional` over open trades with that dealer. VERIFIED.
- `_margin(pf, cause)`: per ACTIVE CSA: net MTM; target VM received `max(0, net - threshold)`, posted `max(0, -net - threshold)`;
  moves if `|delta| >= MTA` (or target 0); IM_POSTED to `required_im`; IM_RECEIVED memo; posting must pass `prime.affordable`,
  otherwise a collateral call (`w.collateral.issue_or_update_call`); after `CSA_CALL_GRACE_CYCLES = 1` unmet cycle the dealer
  closes out the netting set at 1.5x unwind cost and all CSA balances are returned. VERIFIED.
- `terminate(pf, trade_id, ...)` (850-865): PV via `value()`, less `unwind_cost` (828-848: dealer half-width x DV01/CS01/PV/notional);
  emits OTC_TRADE_TERMINATED then a TERMINATION cash flow of `pv - cost`. VERIFIED.
- `_h_credit_event` (1507-1523, live only): CDS protection `N(1-R) - accrued` cash flow, SETTLED_DEFAULT. VERIFIED.
- `_h_cpty_defaulted` (1525-1579): terminate all trades with the dealer at mid, claim = net - VM received + VM posted; recover
  `claim x R` if positive; VM balances consumed (Dr 2450 / Cr 4910; Dr 4910 / Cr 1400); on replay only CSA state is reset. VERIFIED.
  Hand check: P&L = -(1-R) x claim when claim > 0, and 0 when claim <= 0 (VERIFIED by algebra on 1543-1575).
- Dealer defaults are triggered by the simulated dealer model (`simulation.py:87-89`, `w.market.dealer_payload()`). VERIFIED.

---------------------------------------------------------------------------------------------------

## 4. AGENCY MBS - `engines/mbs.py` (362 lines, read in full)

- What: TBAs (forward on a generic pool) that become delivered pools on the SIFMA settle day; prepayment model and pricing
  (`MBSModel`, owned by the market engine) and book-side paydowns (`MBSDesk`). VERIFIED mbs.py:1-27, 166-167, 303-304.
- Data: `ProgramSpec` (48-62) and 4 `PROGRAMS` FNCL/FGLMC/G2SF/FNCI with coupon ladders, servicing, turnover, refi multiplier,
  OAS, ADV, dealing spread (65-70); TBA id `CODE-coupon-YYYY-MM` (74-75); `PoolState(factor, age_months, last_cpr)` (159-163). VERIFIED.
- Model: `mortgage_rate(curve) = 10y zero + PRIMARY_SPREAD 1.70%` (43, 126-127); `cpr(spec, wac, mrate, age)` turnover ramp +
  refi S-curve + burnout, capped 85% (130-137); `cash_flows(coupon, wac, months, cpr)` level-pay pool per 100 face (140-156). VERIFIED.
- `MBSModel.step(d, curve, securities)` (196-221): on each delivered pool's coupon date, factor *= (1 - scheduled - prepaid),
  age += 1, stores `paydowns[id] = fraction`; appends `(date, mortgage rate)` to history. Called by `engines/market.py:1068, 1096`
  (VERIFIED by grep) - i.e. inside the simulated market's daily step.
- Pricing: `dirty_price(sec, curve, d, shift, oas)` (231-246): flows discounted at zero + shift + OAS, monthly compounding; a TBA
  is discounted from delivery and carried at the 3m rate + 15bp to delivery (dollar-roll drop). `clean_price` (248-252),
  `metrics` (254-288: +/-50bp effective duration/convexity, IRR yield, WAL, CPR, factor), `projected_flows` (290-293),
  `ingest/payload` (295-300) persist pool state in the market-close payload. VERIFIED.
- `MBSDesk.process_day(cause)` (312-332): for every position in a security with a paydown today, emits MBS_PAID_DOWN with
  `face_paid = qty x fraction`, `cost_removed = cost_basis x fraction`, `principal = face_paid` (at par), `realized = principal - cost_removed`.
  `_h_paid_down` (334-362): reduces quantity / settled quantity / cost basis / lots, cash += principal, custody movement,
  ledger Dr 1010:USD / Cr cost account / Cr 4000 realized. VERIFIED. (Short positions pay the principal: negative face_paid.)
- Inputs after the change: the live Treasury curve (10y) drives the mortgage rate; pool factors remain MODEL output (no real
  factor feed in this code) - VERIFIED that factors come only from `step`; INFERRED that no real factor source exists elsewhere.

## 5. STRUCTURED CREDIT - `engines/structured.py` (55 lines, read in full)

- Static seed only: `STRUCTURED_SEED` 25 tranches (CLO, CMBS, RMBS, consumer ABS) as tuples
  `(id, name, deal type, tranche, rating, coupon or spread, floating, years, market spread bps, recovery, ADV face, sector)`
  (17-48) and `DEAL_TYPES` text (50-55). VERIFIED. Consumed by `engines/market.py:39, 339` (VERIFIED grep) which turns them into
  bonds (pricing/spread moves live in the market/bond engines, not in this file). Note: the `10_000_000` at structured.py:43 is the
  SUBAUTO-2026-1-A ADV (face traded per day), not a cash amount. VERIFIED.

## 6. QUANT LIBRARY - `quant/derivatives.py` (156), `swaps.py` (57), `fixed_income.py` (91), `volatility.py` (166), `credit.py` (73), `stochastic.py` (132) - all read in full

- Pure "equation" functions: forwards/futures P&L/payoffs/parity/BSM/Greeks/implied vol (derivatives.py D1-D20); swap legs,
  par rate, TRS cash flows (swaps.py S1-S9); bond price/YTM/durations/convexity/DV01/zero/forward (fixed_income.py 111-118);
  EWMA/ARCH/GARCH(+fit)/GJR/realised vol/rolling beta (volatility.py 44-50); LGD/EL/survival/credit triangle/CDS legs/par
  spread/flat-hazard bootstrap (credit.py C1-C9); Brownian/GBM/OU/Vasicek/CIR/Heston (stochastic.py 51-62). VERIFIED.
- NOT used by any FinSim engine for marks or bookings: no `engines/*`, `world.py`, `app.py` or `api/*` import them (grep for
  `quant` in `finsim/finsim/*.py` finds only `quant/catalog.py` and `quant/asset_metrics.py -> volatility`). Users found by grep:
  `finsim/tests/test_quant.py`, `quant/catalog.py` (dotted-path registry), and the separate Desk app (`finsim2/engine/equations.py`,
  `finsim2/engine/portfolio.py`, `finsim2/engine/scenario.py`, `finsim2/engine/features.py`, `finsim2/server.py`). VERIFIED.
  => Removing or rewriting them for the FinSim repair would break the Desk app (finsim2) and test_quant.py.
- Conventions DIFFER from the engine pricers: quant vega per unit vol (not /100), theta per YEAR (not per day), rho per unit (not per bp)
  (derivatives.py:114-136 vs options_pricing.py:101-108); quant CDS ignores accrual on default and uses a flat continuous rate
  (credit.py:30-51) vs `otc_pricing.cds_legs` half-period accrual and the zero curve (otc_pricing.py:210-232). VERIFIED.

---------------------------------------------------------------------------------------------------

## 7. FINDINGS FOR THE REPAIR

### 7.1 Suspected bugs (with evidence)
- **F-FUT-1 Margin call / forced liquidation is dead code.** `FuturesEngine._check_margin_call` (futures.py:123) has no caller
  anywhere in `finsim/finsim` (VERIFIED by grep: only the definition); so `_force_liquidate` (145) and `_settle_position_now` (181)
  never run, although the module docstring (futures.py:7-9) promises them. Negative cash created by VM (futures.py:221), the IM sweep
  (234), OTC cash flows/notional exchanges (otc.py:1451) and option premium is instead covered by `w.treasury_cover_overdrafts(closing)`,
  which the day loop calls right after settlement and again right after the margin sweep (simulation.py:101, 122). VERIFIED call order.
  What it does (read at world.py:474-493): for every non-Treasury book and EVERY currency with `balance < 0`, `treasury_backstop`
  emits TREASURY_ALLOCATED (direction TO_BOOK, a capital allocation, not a loan) for the overdraft, but only if the Treasury has
  enough spare cash in THAT currency; otherwise it returns False and the caller ignores it (world.py:480-481, 493). VERIFIED.
  Consequences: (a) derivative losses are silently refilled from Treasury capital as "allocations" instead of producing a margin
  call or a borrowing; (b) an overdraft the Treasury cannot cover (e.g. a JPY or EUR VM loss when the Treasury has no spare JPY/EUR)
  stays negative with no call and no liquidation, because the margin-call path is dead. INFERRED link to the "~10,000,000 in Treasury"
  symptom: unproven - this path moves money OUT of the Treasury; the reverse flows (book closed -> TO_TREASURY, world.py:580) were not traced.
- **F-FUT-2 Expiry depends on processing exactly the expiry day.** Futures close only when `sec.expiry == today` (futures.py:81); options
  expire only when `sec.expiry == today` (options.py:704). Orders on a future are rejected from its expiry day on (trading.py:146-147),
  and expired options are skipped by the marker (pnl.py:44). VERIFIED. INFERRED impact: if the expiry date is ever not processed
  (a world that stops advancing, a catch-up gap, or the new no-advance model), the position is stranded at its last mark with no way to
  close it. The repair needs an `expiry <= today and still open` sweep.
- **F-OPT-1 Non-USD listed options are marked in LOCAL currency against a BASE-currency cost.** `pnl.mark_position` computes option
  `mv = qty x mid x multiplier` with no FX (pnl.py:48), while the non-option branch converts (pnl.py:57-58) and every fill converts the
  cost basis with `fx_rate = w.fx.k(ccy)` (trading.py:533, 648-650). Contracts take the underlying's currency (options.py:278, 289-290),
  foreign names are optionable (options.py:46, 79-83) and so are EUR/GBP/JPY futures options (FGBL, GLT, JGB in
  `FINANCIAL_OPTION_CODES`, options.py:122). Hand example: buy 1 JPY contract at a mid of 1,000 x 100 = JPY 100,000 with k = 0.0067
  -> cost basis USD 670; next mark unchanged -> `target = 100,000 - 670 = +99,330` booked to 4100 as unrealized gain. VERIFIED code
  path; reachability VERIFIED: `vol.has_listed_options` returns True for foreign stocks whose country is in `OPTION_VENUES`
  (engines/vol.py:127-134), with the home-exchange lot size (`option_multiplier`, vol.py:137-141). Margin IS converted (options.py:513, 580-582).
  Fill side VERIFIED: an option fill's `gross = qty x px x unit` in local currency (trading.py:518-519) with `fx_rate = w.fx.k(ccy)`
  on every fill (533), converted to base before it enters `pos.cost_basis` (648-650, 695).
- **F-OPT-2 Split adjustment breaks contract ids.** `_h_split` divides `c.strike` but keeps the id that encodes the old strike
  (options.py:1031-1032); `ensure_listings` then skips any new contract whose id already exists (options.py:259-261). Worked
  example (grid rules options.py:78-96, 239-241): a $100 stock lists 70..130 in 5s; after a 3-for-2 split spot is 66.67, the new
  grid is 52.5..82.5 in 2.5s, but ids "...C70", "...C75", "...C80" are still held by the adjusted contracts (strikes now 46.67,
  50, 53.33), so the real new 70/75/80 strikes are never listed and those ids carry the wrong strike. `place_strategy` builds ids
  from strikes (options.py:813) and `list_contract` re-derives the strike from the id (options.py:315), so both can pick the wrong
  contract. VERIFIED by reading; not executed.
- **F-OPT-3 Random early assignment.** `_maybe_assign` (options.py:760-794) assigns short American options with seeded random
  probabilities every processed day. In a live paper book this invents assignments that did not happen. VERIFIED.
- **F-OPT-4 Packages are only pre-checked as all-or-none.** `place_strategy` sends each leg as an independent MARKET order
  (options.py:839-840); `trading._strategy_ok` checks depth and net limit for the group (trading.py:349-366) and then legs are
  evaluated one at a time (346-347); the strategy status has no partial state (options.py:717-724). VERIFIED. An atomic
  multi-leg fill (all legs or none, one event) would be needed for the directive's "one ticket with a hedge section". The leg-by-leg
  failure case is INFERRED (not traced through `_evaluate`).
- **F-OPT-5 American theta bias.** `crr_tree` theta subtracts the control-variate-corrected `price0` (options_pricing.py:64) from the
  UNcorrected node `lvl2[1]` (73), so theta carries `-cv/(2dt)/365` per day. VERIFIED code; magnitude INFERRED (e.g. T = 0.25,
  60 steps, cv = 0.005 -> about -0.0016 per share per day). Needs a hand-worked test against a fine tree.
- **F-OTC-1 Fixings are synthetic.** IRS/cap float fixings use today's zero-curve point `term_rate(months)` (otc.py:92-94, 948, 980),
  FRA fixes at `term_rate(round(tau*12))` (969), XCCY foreign leg at the flat `fx.rate[ccy]` (1028). VERIFIED. Not SOFR/term-SOFR.
- **F-OTC-2 State persisted only through mark payloads.** `_lifecycle`/extras mutate `t.state` and `t.fixings` in the command phase
  (otc.py:963, 971, 1048-1073, 1077-1089, 1093-1116; otc_extra.py:647, 667, 701-706, 736, 755, 764-771), relying on the next
  OTC_TRADE_MARKED to carry `state` (comment otc.py:963; `_h_marked` replaces `t.state`, 1418-1419). A trade that matures in the same
  pass is not marked (`if t.is_open`, 900-902), so its last state change reaches no event. VERIFIED code; INFERRED impact on load
  (cash flows themselves are events, so balances survive; schedule/`paid` state may not). Relevant to "atomic save/load".
- **F-OTC-3 XCCY and FX_SWAP move the full notional through cash.** Initial NOTIONAL_EXCHANGE legs (otc.py:755-758;
  otc_extra.py:445-446) go through `_h_cashflow` (otc.py:1445-1453): USD cash falls by the USD notional while foreign cash rises.
  NAV is about unchanged, but the USD cash account can go deeply negative and then the day loop's `treasury_cover_overdrafts`
  (simulation.py:101, 122) acts on it. VERIFIED mechanics; INFERRED link to the Treasury symptom.
- **F-OTC-4 CDS spreads anchored to the world's FIRST curve** (`w.market.curves[0]`, otc.py:107-111). VERIFIED. Meaningless once
  the market is "today only".
- **F-OTC-5 Autocallable marked by 1,500-path Monte Carlo** seeded by date and spot (otc_extra.py:371-391): MC noise flows into 4900
  every day and the cost is paid on every mark and RFQ. VERIFIED.
- **F-OTC-6 30/360 is really 30E/360.** `yearfrac` (otc_pricing.py:50) caps the end day at 30 in both branches of its conditional.
  VERIFIED (both branches give 30 when `d2.day == 31`). Minor, but hand tests must pin the intended convention.
- **F-OTC-7 Asian average counts the trade date unconditionally** (`if t.trade_date != iso or True`, otc_extra.py:732) while the
  session count `n_total` was set from `business_days_between(trade date, maturity)` (316); depending on whether that count includes
  the trade date (calendar code not read), the realised count can end at `n_total + 1`. VERIFIED code; impact INFERRED.
- **F-MISC-1 `pnl.compute_summary` writes state.** It sets `p.notional` for futures while computing a summary (pnl.py:166-167). VERIFIED.
  (Accounting area; noted because futures notional is also set by `_h_settled`, futures.py:209.)

### 7.1b Pointers OUTSIDE my area seen while tracing (VERIFIED lines, not analysed further)
- `world.create_portfolio(..., capital: Decimal = D(10_000_000), ...)` (world.py:732): the default capital of a new book is exactly
  10,000,000. Worth checking against the "about 10,000,000 appears in Treasury" symptom (e.g. a book or Treasury created or
  re-created on load/catch-up with the default capital). Not traced.
- Load path: `w.replay(events)` then `w._relist_orphans()` then `check_integrity()` (world.py:715-718). `_relist_orphans`
  (721-730) re-lists only OPTION ids via `options.list_contract`, which returns None for anything else (options.py:307-309), so an
  order/trade/position on a missing non-option security (e.g. an expired future or a TBA month not regenerated) stays unresolved.
- `_h_market_close` (world.py:600-610) re-ingests the close bars, curve, vol and dealer state from the event payload on replay and
  re-runs `options.ensure_listings(d)` - option chains are rebuilt from replayed prices on every load.
- `trading.system_order` fills immediately at the close or a fixed price (trading.py:563-577); `trading.work_orders` handles
  strategy groups (327-366).

### 7.2 Double-posting candidates checked (results)
- Futures VM then expiry: NO double posting. Trade-time booking is commission only (trading.py:757-787); expiry close is a
  `system_order` at close +/- half spread (futures.py:88-89, trading.py:563-577) whose fill joins `day_fills` and is settled once by
  `daily_settlement` at the same close (futures.py:100-106), so the only extra P&L is the half-spread. VERIFIED by code path.
  Hand check: long 2 ES (x50) carried at prev settle 5,000; close 5,010; expiry sell fills 5,009.75 -> VM = 2x10x50 + (-2)(5,010-5,009.75)x50 = 1,000 - 25 = 975.
- Futures VM vs `pos.realized_pnl` vs ledger: VM goes to cash + 4400 and to `pos.realized_pnl` (futures.py:206-208, 221-226);
  `compute_summary.realized = 4000 + 4400` (pnl.py:180); futures market_value forced to 0 and excluded from MV/unrealized
  (futures.py:210, pnl.py:142, 174). Consistent - but `pos.realized_pnl` is a SECOND copy of the same number (duplicate store). VERIFIED.
- Listed option exercise/expiry: the premium was paid at trade (cost basis 1100/2xxx accounts via trading.py:663-747); at expiry the
  option is closed at 0 (physical) or at intrinsic (cash) through `system_order` with `commission_free=True` and the stock trade is
  booked at the strike (options.py:681-692, 726-750). Realized P&L comes from the closing trade's FIFO relief; no separate payoff
  posting. VERIFIED. `_h_exercised` skips on replay (936-937) so the derived trades are not doubled on load. VERIFIED.
- OTC termination: `_h_terminated` reverses the LAST MARK against 4900 and `terminate` pays `pv_now - cost` to 4910
  (otc.py:858-864, 1464-1479). Hand check: last mark 1,000, pv now 1,050, cost 20 -> 4900 -1,000, 4910 +1,030, net +30 = 50 - 20. No double count. VERIFIED.
- OTC premium: booked as a 4910 realised loss (inception cash flow) plus an inception mark to 4900 (otc.py:1405-1409). Total = PV -
  premium = -(cost vs mid): correct in sum, but "realized" vs "unrealized" are both off by the premium. VERIFIED.
- Dealer default: P&L = -(1 - R) x claim when claim > 0, 0 otherwise (algebra on otc.py:1543-1575). VERIFIED.
- CDS accrued premium: in PV (otc.py:196-197) and reduced when paid (1086) - consistent. VERIFIED.

### 7.3 Duplicate computations (for the "one accounting module" directive)
- Two BSM engines + two implied-vol solvers: `engines/options_pricing.py` (bsm, greeks, implied_vol) and `quant/derivatives.py`
  (bsm_call/put, greeks, implied_vol); NEITHER implied-vol solver is called by FinSim (grep for `implied_vol(` finds only the two
  definitions). VERIFIED.
- Two option pricers for the same underlying: listed (`options.model_inputs`: `r = interp_rate(curve, T)`, CRR for American;
  options.py:337-350) vs OTC (`_eq_inputs`: `r = -ln(df)/T` from the semiannual curve, always European BSM; otc.py:212-247). VERIFIED.
- Option intrinsic computed in at least 7 places: options.py:729, 888, 919; options_pricing.py:127; otc.py:240, 262, 1128;
  otc_extra.py:710, 741. VERIFIED.
- Futures VM formula twice (futures.py:100-106 and 184-190, the second dead). VERIFIED.
- Initial margin per contract recomputed for display in api/service.py:433, 507, 607, 1668 and in `_h_swept` (futures.py:237). VERIFIED (grep).
- Futures notional computed in `daily_settlement` payload (futures.py:109) and again in `compute_summary` (pnl.py:166). VERIFIED.
- CDS legs: `otc_pricing.cds_legs` vs `quant/credit.py` (different conventions). TRS cash flow: otc.py:1052-1066 vs quant/swaps.py:39-57. VERIFIED.

### 7.4 Everything in my area that is tied to the simulated day advance / simulated market
- Day-loop entry points (simulation.py): `mbsdesk.process_day` (105), `futures.expire_contracts` (108), `futures.daily_settlement` (109),
  `otc.process_day(closing, prev)` (118), `options.process_day` (119), `futures.sweep_margin` (121). Market side: `market.mbs.step`
  (market.py:1068, 1096), `options.ensure_listings` (world.py:610 on every MARKET_CLOSE, market.py:954/1004/1095). VERIFIED.
- `prev`-date accruals: OTC `days = (today - prev).days` (otc.py:939) for TRS financing, CDS accrual, perp funding. VERIFIED.
- Once-per-processed-day observations: commodity-swap averages (otc.py:1096-1101), variance/vol swap returns (otc_extra.py:642-646),
  asian averages (730-736), barrier touches on closes (691-706), dividend capture by `ex_date == today` (otc.py:1055-1058,
  otc_extra.py:663-666, options.py:778-781), MBS paydowns on coupon dates (mbs.py:206-207). VERIFIED.
- Seeded randomness keyed by date: option listing volume/OI (options.py:325, 379-381), early assignment (784), RFQ dealer declines
  (otc.py:505, 512), autocallable MC (otc_extra.py:371). VERIFIED.
- Simulated regime multipliers: futures margin (futures.py:26, 42-43), options margin (options.py:48, 498), option spreads
  (`regime().spread_mult`, 364), FX vol (otc.py:51, 249-250), TRS spread (52, 424), XCCY basis (307-312), rate vol
  (otc_pricing.py:164-167), dealer half-width (`quote_half_width`, otc.py:511, 831). VERIFIED.
- Simulated state objects read: `w.market.vol` (surface, `init_underlying`, `iv`, `atm_for_tenor`, `iv_rank`, history),
  `w.market.curve()/curves[0]/curve_for_ccy`, `w.market.fx.spot/rate/forward`, `w.market.curve_for(code)/spot(code)`,
  `w.market.macro.cpi_index/state.inflation`, `w.market.dealers.state`, `w.market.last_bar`, `w.market.history`. VERIFIED by the reads above.
- Quote/bar caches keyed by `current_date` (options.py:368, 389) - stale within a live day unless S changes the key. VERIFIED.
- RFQ expiry = next business day processed by the loop (otc.py:527, 894-896). VERIFIED.
- Fills (trading area, seen while tracing): option and futures fills take the session bar close/open +/- half spread plus a
  RANDOM market-impact term `IMPACT_K x sigma_daily x sqrt(participation) x rng.uniform(0.8, 1.2)` (trading.py:480-502), options use
  `OPTION_SIGMA_DAILY`; option "bars" are themselves model prices (options.py:387-403). VERIFIED. Live paper fills would use the live bid/ask.

### 7.5 Inputs that would come from live data after the change
| input | used by | today (simulated) | live replacement needed |
|---|---|---|---|
| underlying spot / index level | options, OTC equity, TRS, perps, barriers, autocall | `last_bar(...).close`; SPX = SPY x 10 (options.py:39-42, 180-185) | real last/mid; real index levels (live.py already has INDEX_SYMBOLS) |
| implied vol per strike/expiry | listed quotes/marks/margin, OTC equity options, var swaps, notes | `w.market.vol.iv` surface | live option-chain IVs (or IV solved from live bid/ask with `implied_vol`) |
| option bid/ask, volume, OI, strikes, expiries | chains, fills, analytics | model mid +/- spread; random vol/OI; generated strike grid | live chain |
| rates curve (USD + other ccy) | discounting, r for options, swaps, FRAs, caps, swaptions, MBS | `w.market.curve()`, `curve_for_ccy` | live Treasury/SOFR curve (live.py has YIELD_SYMBOLS) |
| floating fixings | IRS, caps, FRA, XCCY | zero-curve point / flat `fx.rate` | published SOFR / term SOFR / foreign RFRs |
| dividends | q in options, TRS, dividend swaps, early-exercise check | static `dividend_yield`, simulated ex-dates | real dividend calendar |
| futures settle / last | VM, IM, options on futures | `last_bar(fut).close` (live.py scales by live/ref spot ratio) | official daily settlement |
| futures IM rates | `sec.margin_pct x regime` | static pct x simulated regime | exchange margin table or a fixed pct |
| FX spot / forwards / foreign rates / FX vol | FX options/forwards/swaps, XCCY, non-USD margin | `w.market.fx`, static `FX_VOL` x regime | live FX + forward points; static or live vols |
| credit spreads | CDS, structured | curve IG/HY spreads scaled vs `curves[0]` | live CDX/iTraxx or a fixed table |
| commodity strips | commodity swaps/forwards/asians, options on futures | `curve_for(code)`, `spot(code)` | live futures strip |
| CPI / breakeven | inflation swaps | `macro.cpi_index`, macro inflation | BLS CPI, market breakevens |
| crypto funding | perps | fixed 0.0003/day | live funding rate or fixed |
| MBS pool factors | MBS paydowns, prices | model `step` | none in code (keep model or freeze) |


---------------------------------------------------------------------------------------------------

STATUS: COMPLETE - all 14 files in my area read in full (engines/options.py, options_pricing.py, futures.py, otc.py, otc_extra.py, otc_pricing.py, structured.py, mbs.py; quant/derivatives.py, swaps.py, fixed_income.py, volatility.py, credit.py, stochastic.py). Context read outside my area (partial, for tracing only): simulation.py:60-143, trading.py:140-169 + 318-379 + 476-535 + 563-603 + 630-787, pnl.py:25-124 + 136-185, world.py:463-516 + 596-617 + 712-737 + 1078-1113, live.py:195-234, vol.py:127-162, domain/otc_models.py:62-64, plus greps of models.py/fx.py/collateral.py for `base_value`. Not read (outside my area, referenced only): the rest of engines/vol.py (vol surface dynamics), engines/collateral.py (`move_cash`, calls), engines/treasury.py, engines/market.py (where `mbs.step` / `ensure_listings` / structured seeds are driven), engines/fx.py (`_adjust_cash`, `forward`), calendar.py, engines/commodity_desk.py (`deliver`, `SPREAD_MARGIN_RATE`), engines/prime.py (`affordable`). Map written 12:30-12:43 ET.
