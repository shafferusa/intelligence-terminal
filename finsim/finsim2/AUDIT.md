# FinSim2 — Full Product Audit (2026-09-24)

This audit was checked against the code on branch `claude/finance-simulation-game-y0nkv0` at commit `4394f92` and
the commits after it. No item was marked DONE only because a related feature exists: each one was checked in the
code. Items marked **[reproduced]** were also demonstrated by running the code against a synthetic research store
or the real one.

Paths are relative to `finsim/`. P = `finsim2/engine/portfolio.py`, S = `finsim2/server.py`,
ST = `finsim2/data/store.py`, JS = `finsim2/static/app.js`, H = `finsim2/engine/horizons.py`,
SC = `finsim2/engine/scores.py`, R = `finsim2/engine/research.py`, ML = `finsim2/engine/ml.py`,
BT = `finsim2/engine/backtest.py`, EQ = `finsim2/engine/equations.py`, U = `finsim2/data/universe.py`.

Status: **DONE / PARTIAL / MISSING / N/A**. Priority: **P0** (must fix before serious use) … **P3** (polish).
Each row reads: Item | Status | Current implementation (file) | Limitation → Recommended change | Priority.

---

## 1. Portfolio accounting

The ledger (`Ledger`, P:48-182) stores no state: every call replays every row in `transactions`.
`holdings()` and `nav_history()` each replay the rows separately, and they **disagree on SELL** (see edge cases).
The `snapshots` table (ST:45) is never written or read.

### Core bookkeeping
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Cash balance | PARTIAL | one USD scalar, `holdings()` P:112-141 | BUY never checks cash, so cash goes negative silently **[reproduced: $1k deposit, $230k buy accepted, cash −$229k]** → reject, or require an explicit margin setting with a financing charge | P0 |
| Cash by currency | MISSING | deposits/withdrawals hard-code USD (P:61,70); foreign buys convert at spot | → per-currency cash accounts with FX conversion transactions | P1 |
| Position quantity | DONE | P:131,140 | float; wrong after splits (see edge cases) | — |
| Average cost | DONE | weighted average in USD at trade-date FX, fees included (P:134-139) | only method → see §65 | — |
| Market value | DONE | last close (ffill ≤10) × qty × FX (P:350-352) | no warning when there is no price (MV = 0); raw close, not total return | P1 |
| Unrealized P&L | DONE | `mv − cost` (P:357) | FX and price effects are mixed → split them (§FX translation) | P1 |
| Realized P&L | DONE | proceeds − fee − avg×q (P:136-138) | wrong when a SELL is truncated (edge cases) | P0 |
| Total P&L | DONE | `pnl = nav − contributed` (P:422) | — | — |
| Daily P&L | MISSING | nav returns computed (P:416-419) but not returned | → expose 1D P&L and return; dashboard tile | P1 |
| Since-inception P&L | DONE | same as total | — | — |
| Portfolio NAV | DONE | cash + ΣMV | — | — |
| Beginning / ending NAV | PARTIAL | only `history.nav` | → period start/end NAV in a performance block | P1 |
| Deposits / withdrawals | DONE | P:57-70; withdrawal checks cash (min of that date and today) | — | — |
| External flows separated from performance | PARTIAL | `flows` in `nav_history` (P:160-181) used only internally | → performance block with flows shown separately | P1 |
| Return around flows | PARTIAL | `(b − flow)/a − 1`, flow assumed at end of day (P:419) | only feeds `risk_actual`, which is never shown | P1 |
| Time-weighted return | PARTIAL | daily flow-adjusted returns exist, never chained; `ann_return` = mean×252 | → chained TWR, periods 1D…inception | P1 |
| Money-weighted return / IRR | MISSING | — | → XIRR over the flows | P1 |
| Daily NAV history | DONE | P:144-182 | recomputed from scratch on every portfolio call (analytics, Monte Carlo, scenario, drivers each replay it) → cache in `snapshots` keyed by data_version and ledger version | P2 |
| Cost-basis history | MISSING | `holdings(as_of)` could provide it; no route | → endpoint and chart | P2 |
| Trade history | DONE | GET `portfolio/transactions` (S:348) | raw rows | — |
| Position history | MISSING | internal to nav_history | → expose | P2 |
| Cash history | MISSING | internal | → expose | P2 |

### Accounting edge cases
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Partial sales | DONE | average cost | — | — |
| Multiple purchases at different prices | DONE | — | — | — |
| Re-entering a closed position | DONE | `setdefault` (P:128) | the closed entry stays with float residue → zero it on close | P3 |
| Negative-cash prevention | PARTIAL | WITHDRAW checked; BUY not | see Cash balance | P0 |
| Oversell / back-dated transactions | **BUG** | SELL is checked only against holdings on its own date; later rows and deletions are never re-validated. `holdings` truncates with `q = min(...)` but credits cash for the full quantity; `nav_history` subtracts the full quantity, so it can go negative **[reproduced: a back-dated SELL created $2,288 of phantom cash; headline NAV ≠ NAV chart]** | → one replay function used by both; validate the whole ledger on every insert and delete; never truncate silently | P0 |
| Fractional shares | DONE | REAL quantities, amount sizing (S:366) | — | — |
| Stock splits | MISSING | Yahoo `close` is split-adjusted retroactively and history is re-downloaded on mismatch (refresh.py:116-129); transactions are never adjusted; `yahoo.py` asks for `events=div,split` but discards them | any split after entry (or a real broker fill price) produces a false loss or gain → parse split events, store them, scale quantity and price in the replay | P0 |
| Reverse splits | MISSING | same | same | P0 |
| Cash dividends | MISSING | no DIVIDEND kind; valuation on `close` (P:152,350) | NAV and P&L understate total return; risk uses `adj_close` (P:203), which is inconsistent → credit dividends from Yahoo events on the ex/pay date | P0 |
| Stock dividends | MISSING | — | → treat as splits | P1 |
| Special dividends | MISSING | — | → covered once dividend events are ingested | P1 |
| Spin-offs | MISSING | — | → manual corporate-action entry | P2 |
| Mergers | MISSING | — | → manual corporate-action entry (cash/stock ratio) | P2 |
| Acquisitions | MISSING | — | same | P2 |
| Delistings | MISSING | last price carried forward forever (P:152,350) | → stale-price flag after N sessions; manual close-out at a price | P1 |
| Symbol changes | MISSING | only the XOM/DIS extra CIKs for SEC | → alias table | P2 |
| ETF distributions | MISSING | close is ex-distribution | same fix as dividends | P0 |
| Bond coupon payments | PARTIAL | synthetic Treasury/BAA indices are total-return (carry is in the price) | bond ETFs lose their distributions (see dividends) | P0 (via dividends) |
| Bond maturity | N/A | constant-maturity indices only | document | P3 |
| Futures expiration | MISSING | continuous front-month price series; no roll or expiry | → treat as notional exposure; see §6 | P2 |
| Option expiration | N/A | no option positions | — | — |
| FX translation gains/losses | PARTIAL | local close × USD per unit (P:28-37, 351); an unsupported currency raises and takes down the whole portfolio page | FX P&L not separated; **USD-base pairs (USDJPY, USDCAD, USDCHF, USDCNY, USDMXN, USDINR) are valued as a constant**: price × 1/price = 1, so a long-dollar position always shows zero P&L → value FX pairs as financed positions (MV = N·(1 − r₀/rₜ) for USD-base pairs) | P0 |

---

## 2. Trading

FinSim2 is **a ledger of fills, not an order simulator**. The fill price is the user's price, or the asset's close
on the last calendar date on or before the trade date. This is intentional and should be stated in the UI and
README (currently it is only implied).

| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Buy | DONE | P:72-96, S:354-367 | no cash check (§1) | P0 |
| Sell | DONE | same | oversell/back-dating bug (§1) | P0 |
| Short sell | MISSING | `allow_short` is read but never set on any asset (P:93) | → see §3 | P1 |
| Buy to cover | MISSING | — | → §3 | P1 |
| Add to position | DONE | BUY | — | — |
| Reduce position | DONE | SELL | — | — |
| Close position | DONE | SELL the full quantity | no one-click close → add | P3 |
| Reverse long→short | MISSING | — | → §3 | P2 |
| Reverse short→long | MISSING | — | → §3 | P2 |
| Market / limit / stop / stop-limit / trailing / day / GTC | N/A (by design) | fills are entered, not orders | → document "FinSim2 records fills; it does not simulate order execution"; do not add order types | P3 |
| Bid/ask spread | MISSING | — | → optional per-asset-class spread applied to default fills | P2 |
| Slippage | MISSING | ledger none; the backtest has `cost_bps` | same | P2 |
| Commission | PARTIAL | `fee` field in the API and ledger | txModal has no fee input, so fees from the UI are always 0 → add the input | P1 |
| Trading fees | PARTIAL | same field | same | P1 |
| Market impact | MISSING | — | → only for large positions vs ADV (§63) | P3 |
| Liquidity assumptions | MISSING | — | → §63 | P2 |
| Volume restrictions | MISSING | — | → warn when quantity > x% of ADV | P2 |
| Costs by asset class | MISSING | — | → a default cost table used for fills and backtests | P2 |
| FX conversion cost | MISSING | — | → spread in bps on non-USD fills | P2 |
| Short borrow cost | MISSING | — | → with shorts | P2 |
| Funding cost | MISSING | negative cash costs nothing; only the portfolio Monte Carlo charges rf on it (S:382-384) | → if margin is allowed, accrue interest daily | P0 (with the cash check) |

---

## 3. Short selling
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Short positions supported | MISSING | blocked (P:93-94) | → allow_short per portfolio; SELL beyond holdings opens a short | P1 |
| Negative quantity handled | MISSING | only reachable through the nav_history oversell bug | → proper signed average cost | P1 |
| Short market value | PARTIAL | MV is signed (P:352) but unreachable | tests | P1 |
| Short P&L | MISSING | average-cost logic assumes longs | → signed cost basis | P1 |
| Borrow fee | MISSING | — | → accrue rate × |MV| | P2 |
| Dividends owed by short | MISSING | — | → with dividend events | P2 |
| Short exposure | PARTIAL | `short` key always 0 | — | P1 |
| Gross exposure | DONE | `gross` (P:423) | dollars only | — |
| Net exposure | DONE | `net` | dollars only | — |
| Beta incl. shorts | PARTIAL | signed MV (P:406), untested | tests | P1 |
| Risk incl. shorts | PARTIAL | signed weights (P:361-371), untested | tests | P1 |
| Short VaR contribution | PARTIAL | Euler, signed (P:374), untested | tests | P1 |
| Short factor exposure | PARTIAL | regression on the portfolio series, untested | tests | P1 |

## 4. Margin / leverage
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Gross leverage | PARTIAL | `gross` in dollars | → gross / NAV | P1 |
| Net leverage | PARTIAL | `net` in dollars | → net / NAV | P1 |
| Long exposure | DONE | `long` | — | — |
| Short exposure | PARTIAL | always 0 | §3 | P1 |
| Cash exposure | DONE | cash and cash % of NAV | — | — |
| Margin utilisation | MISSING | — | → only if margin is allowed | P2 |
| Buying power | MISSING | — | → cash (+ margin) | P2 |
| Leverage ratio | MISSING | weights sum above 1 when cash is negative, but no ratio is reported | → report it | P1 |
| Leveraged ETF recognition | MISSING | none in the universe; no leverage metadata | → `leverage` field for SSO/TQQQ-type additions | P2 |
| Futures embedded leverage | MISSING | futures valued as price × qty (no multiplier or margin) | → notional exposure field | P2 |
| Derivative delta-adjusted exposure | N/A | no option positions | — | — |
| Warning: leverage above threshold | MISSING | — | → risk-flag engine (§70) | P1 |
| Warning: extreme concentration | PARTIAL | "factor concentration" KPI only, no threshold | → largest weight, top-5, HHI with thresholds | P1 |
| Warning: liquidity | MISSING | — | → §63 | P2 |
| Warning: one factor dominates | PARTIAL | KPI only | → threshold flag | P1 |

---

## 5. Asset classes (133 assets: ETF 49, EQUITY 45, COMMODITY 11, FX 11, INDEX 9, TREASURY 4, CRYPTO 3, CORP_BOND 1)
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| US stocks | DONE | 45 current mega-caps (U:169-220) | survivorship bias; small universe → document it; allow a bulk add | P1 |
| Foreign stocks | MISSING | only via "Add a symbol" | a GBp/KRW listing makes the portfolio page fail (no FX mapping) → FX map for all Yahoo currencies; pence scaling | P1 |
| ADRs | MISSING | add-symbol only | — | P2 |
| Preferred shares | MISSING | — | — | P3 |
| Equity ETFs | DONE | 31 (U:55-88) | — | — |
| Bond ETFs | DONE | 14, hard-coded durations | distributions missing (§1) | P0 via §1 |
| Commodity ETFs | DONE | GLD SLV USO DBC | — | — |
| Currency ETFs | MISSING | — | — | P3 |
| Leveraged ETFs | MISSING | — | → with a leverage field | P2 |
| Inverse ETFs | MISSING | — | same | P2 |
| SPX | DONE | price return | — | — |
| Nasdaq | DONE | NDX | — | — |
| Dow | DONE | DJI | — | — |
| Russell | DONE | RUT | — | — |
| International indices | DONE | N225 FTSE DAX SX5E HSI | local currency | — |
| Treasury securities | PARTIAL | synthetic constant-maturity total-return indices from DGS yields (refresh.py:59-94) | **convexity is about 100× too small** (`_cx` = D²/100 used in ½·C·dy²; U:32-34) → store convexity in years² (≈D²+D, or modified-duration-based); no roll-down | P1 |
| Treasury ETFs | DONE | SHY IEI IEF TLT TIP | — | — |
| Corporate bonds | PARTIAL | synthetic BAA index + ETFs | no individual bonds | P3 |
| Yield curves | PARTIAL | 5 FRED points, equations tab only | → yield-curve chart and curve factors | P2 |
| Credit spreads | PARTIAL | BAA10Y only | → add HY and IG OAS (BAMLH0A0HYM2, BAMLC0A0CM) | P2 |
| Spot FX | DONE | 10 pairs + DXY | USD-base valuation bug (§1) | P0 |
| Currency conversion | PARTIAL | 10 currencies + HKD peg | anything else raises → full Yahoo FX map | P1 |
| Currency exposure | PARTIAL | by listing currency | no look-through (EFA shows as USD) → optional look-through weights | P2 |
| Commodity spot proxies | PARTIAL | front-month futures + ETFs | — | P3 |
| Commodity futures | PARTIAL | 11 `=F` continuous series | roll gaps stay in the returns; **CORN/WHEAT/SOYBEANS/COFFEE are quoted in US cents but tagged USD** (internally consistent in the ledger; wrong if you enter a real fill price in dollars or read the quantity as bushels) → a price_scale of 0.01 | P1 |
| Continuous futures data | PARTIAL | unadjusted front month | → back-adjusted series or flag roll days | P2 |
| BTC | DONE | — | weekend moves roll into Monday | — |
| ETH | DONE | — | — | — |
| Other liquid crypto | PARTIAL | SOL only | → add more by symbol | P3 |

## 6. Derivatives
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Calls / puts: priced | PARTIAL | BSM and Greeks (EQ:2652-2717) on a hypothetical 3-month ATM option | display only, no positions | P2 |
| Long / short options | MISSING | — | → an option position type in the ledger (valued with BSM on realised or implied vol) | P2 |
| Strike, expiration, multiplier, premium | MISSING (positions) | — | same | P2 |
| Intrinsic value / time value | PARTIAL | equations tab | same | P2 |
| Implied volatility | PARTIAL | IV solver exists in `finsim.quant` | no market option prices → document | P3 |
| Delta / gamma / vega / theta / rho | PARTIAL | equations tab | same | P2 |
| Black-Scholes | DONE | `finsim.quant.derivatives` | — | — |
| Option P&L | MISSING | — | — | P2 |
| Portfolio delta / gamma / vega | MISSING | — | — | P2 |
| Futures positions | PARTIAL | holdable as price series | no multiplier/notional → contract spec (multiplier, tick) | P2 |
| Contract multipliers | MISSING | `meta.multiplier` read by D5/D6 but never set | — | P2 |
| Notional exposure | MISSING | — | — | P2 |
| Expiration / roll dates | MISSING | — | — | P3 |
| Continuous futures series | PARTIAL | see §5 | — | P2 |
| Futures P&L | PARTIAL | price × qty | no margin or variation margin | P2 |
| Embedded leverage | MISSING | — | — | P2 |
| Term structure / contango / backwardation / roll yield | MISSING | only the front month is downloaded | → fetch the 2nd contract (e.g. CLZ26) for a front/next spread | P2 |
| Interest-rate swap valuation | PARTIAL | S1-S5, hypothetical $10mm on the Treasury curve | analytics only (fits the product) | P3 |
| TRS exposure | PARTIAL | S6-S9 hypothetical | analytics only | P3 |
| Swap scenario analysis | MISSING | — | — | P3 |

---

## 7. Market data
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Open / high / low / close / adj close / volume | DONE | `prices` table (ST:24-25) | synthetic bonds have close/adj only | — |
| Corporate-action-adjusted history | PARTIAL | `close` split-adjusted; `adj_close` also dividend-adjusted; re-download on overlap mismatch (refresh.py:114-130) | split and dividend *events* are discarded (yahoo.py:110-141) → store them (needed by §1) | P0 |
| Intraday | N/A | daily only | a partial live bar can be stored until the next overlap → skip today's bar before the close | P2 |
| Missing-date detection | MISSING | silent ffill of ≤5 sessions (align.py:15) | → per-asset gap report on Settings | P2 |
| Stale-price detection | PARTIAL | `stale()` checks SPY only | → per-asset last date; flag if more than 3 sessions behind | P1 |
| Bad-tick detection | MISSING | only non-finite values dropped | → flag \|r\| > 8σ jumps that revert the next day | P2 |
| Duplicate rows | DONE | PK(asset_id, date) + dedup on parse | — | — |
| Missing values | DONE | None rows skipped; FRED "." skipped | — | — |
| Extreme outliers | MISSING | z capped at ±3 downstream only | as bad-tick | P2 |
| Symbol changes | PARTIAL | extra CIKs for XOM/DIS | → alias table | P2 |
| Delisted stocks | MISSING | — | → keep delisted assets; flag | P2 |
| Survivorship bias | MISSING | hard-coded list of today's mega-caps | every cross-asset/universe statistic is biased → document it prominently; long term, point-in-time membership | P1 (docs) |
| Stale fundamentals | PARTIAL | STALE_DAYS = 550 (sec.py:27) | 18 months is too long → 200 days | P2 |
| Time-zone alignment | DONE | bar date = timestamp + exchange gmtoffset | Asia/US close asynchrony is not handled in correlations → note it | P3 |
| Market-calendar alignment | DONE | SPY calendar, ≤5-session ffill; macro ≤400 days | no NYSE holiday model | P3 |

## 8. Point-in-time data
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Prices use only data available on the date | DONE | align.series uses dates ≤ d | — | — |
| SEC filings available on the filing date | DONE | `filed` field; visible if filed ≤ date (sec.py:212) | an after-close filing counts from that close (≈1 session early) → +1 session | P2 |
| Macro uses publication date | PARTIAL | fixed lag days (U:225-243): daily 1, CPI 45, UNRATE 35, INDPRO 45, M2 45, NFCI 5, WALCL 4 | UNRATE 35 days can precede the jobs report; M2 45 is ≈8 days short → use real release calendars (FRED `realtime_start`) | P1 |
| Revised macro data does not leak backward | **MISSING** | fredgraph.csv = **latest vintage**; the refresh overwrites history | **revisions leak into CPI, UNRATE, INDPRO, M2, WALCL and especially NFCI** (re-estimated weekly; drives the liquidity regime and the `nfci` feature) → with FRED_API_KEY, fetch first releases (`output_type=4` / ALFRED); without it, drop NFCI from features and regimes and label macro features "revised data" | P0 |
| Earnings respect announcement dates | PARTIAL | uses 10-Q/10-K filed date (later than the 8-K, so conservative) | → document | P3 |
| Fundamental ratios point-in-time | PARTIAL | TTM from filings visible at t (sec.ttm_series) | **EPS is as filed, not split-adjusted, while prices are split-adjusted: `earnings_yield`, `pe_rel_5y` and `eps_growth_yoy` are wrong across every split (NVDA, AAPL, AMZN, GOOGL, TSLA, WMT, AVGO)** → scale EPS by the cumulative split factor (needs split events) | P0 |
| Index memberships | N/A | not used | — | — |
| Normalisation uses training data only | DONE | expanding z of values strictly before t, cap ±3 (signals.py:17-33); ML Preprocessor fitted on training rows (models.py:205) | — | — |
| PCA uses training data only | N/A | PCA is not used by the ML pipeline; equations-tab PCA is descriptive | — | — |
| Feature selection uses training data only | PARTIAL | feature/row inclusion by full-sample coverage (≥60% non-null, ML:51,55) | selection by availability, not the target: low risk → compute on the training window | P2 |
| Hyperparameter selection does not see test data | DONE | fixed hyperparameters, no tuning | — | — |
| Regime identification without future information | PARTIAL | all trailing windows and fixed thresholds (regimes.py:57-86) | inherits the FRED revision leak (NFCI, UNRATE, CPI) | P0 (via revisions) |
| Score histories are point-in-time | PARTIAL | weights refitted every 63 sessions using only outcomes known then; local ranks; persistence from pre-start data (R:263-321) | the history **omits the live score's regime blend**, so the OOS accuracy is measured on a different score from the one displayed → same weighting function for both | P0 |
| (extra) Backtest signal direction | **LEAK** | `direction_sign` from the full-sample matrix IC (S:323-324; R:258 cell drawer) | the backtest knows which way the signal worked over the whole history → point-in-time direction (expanding IC sign) | P0 |
| (extra) ML backtest normalisation | **LEAK** | OOS forecasts z-scored with the mean/sd of all OOS forecasts (R:334-336) | → expanding mean/sd | P0 |
| (extra) Same-close execution | **LEAK-ish** | decision on signal[t] earns the t→t+1 return (BT:61-90) | trades at the close whose data produced the signal → default 1-session execution lag | P0 |
| (extra) Ensemble weights | minor leak | block k weights use block k−1 ICs whose targets end after block k starts (ML:165) | → purge the last h/step rows of the previous block | P1 |
| (extra) Quant expected return | in-sample | today's full-sample weights applied to all history, then an OLS (SC:90-131) | shown as a forecast "± typical error" → map from the point-in-time composite; out-of-sample RMSE | P0 |

## 9. Fundamental analysis
Fundamentals exist for the 45 US equities with a CIK. Concepts fetched (sec.py:30-39): diluted EPS, revenue, net
income, stockholders' equity, shares outstanding. **Equity and shares are fetched but never used.**

| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Revenue | PARTIAL | only inside growth and margin | → expose | P2 |
| Revenue growth | DONE | `rev_growth_yoy` | — | — |
| Gross profit / gross margin | MISSING | — | → GrossProfit concept | P2 |
| Operating income / operating margin | MISSING | — | → OperatingIncomeLoss | P2 |
| Net income | PARTIAL | only via net margin | expose | P2 |
| EPS | PARTIAL | TTM, only via earnings yield | split bug (§8) | P0 |
| EPS growth | DONE | `eps_growth_yoy` | split bug | P0 |
| Free cash flow / FCF margin / capex | MISSING | — | → operating cash flow − capex concepts | P2 |
| Debt / cash / net debt | MISSING | — | → LongTermDebt, CashAndCashEquivalents | P2 |
| Assets / equity | MISSING (equity fetched) | — | → use it | P2 |
| ROE / ROA / ROIC | MISSING | — | — | P2 |
| Debt/equity, debt/revenue, debt/EBITDA, interest coverage | MISSING | — | — | P2 |
| Current ratio / quick ratio | MISSING | — | — | P3 |
| P/E | PARTIAL | earnings yield + P/E relative to 5 years; no raw P/E | → expose P/E | P2 |
| Forward P/E | MISSING | no reliable consensus source | → document as unavailable, do not fake | P3 |
| P/S, P/B, EV/EBITDA, EV/Sales, FCF yield | MISSING | — | → market cap from shares × price | P2 |
| Earnings yield | DONE | — | split bug | P0 |
| Dividend yield | PARTIAL | adj/close drift, equations tab only | → from dividend events | P2 |
| Valuation vs own history | DONE | `pe_rel_5y`, `value_5y` | — | — |
| Valuation vs sector / vs market | MISSING | — | → sector medians over the covered stocks | P2 |

---

## 10. Returns analytics (A = asset, P = portfolio)
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Daily / weekly / monthly return | A DONE, P MISSING | features `ret_*` (log); `change` 1D/1W/1M/1Y (R:172-175) | YTD hard-coded None; no portfolio periods → performance block | P1 |
| 3M / 6M / 12M return | A DONE, P MISSING | features | same | P1 |
| 3Y / 5Y / 10Y return | MISSING (both) | only eq 5 annualised over ≤5 years | → return table | P1 |
| Simple return | A DONE | eq 1/3 | — | — |
| Log return | A DONE | features | — | — |
| CAGR | A PARTIAL, P MISSING | eq 5; backtest CAGR | `ann_return` = mean×252 → true CAGR | P1 |
| Rolling return | A DONE | equations X-ret history | P missing | P2 |
| Excess return | A PARTIAL | vs the *current* 3M rate | → historical rf series | P2 |
| Relative / benchmark-relative return | A DONE, P MISSING | X-rel vs SPY | → §15 | P1 |

## 11. Risk metrics
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Volatility | DONE (A, P) | features; covariance; `risk_stats` | P = current weights simulated over 756 days | — |
| Downside volatility | A DONE, P PARTIAL | inside Sortino only | expose | P2 |
| Beta | DONE | beta_252; P = weighted betas + factor regression | SPY only | — |
| Alpha | A DONE, P PARTIAL | weekly intercept, not annualised, not shown | → annualise and show | P2 |
| Sharpe / Sortino | DONE | rf = current 3M level | → historical rf | P2 |
| Treynor | MISSING | — | add | P3 |
| Information ratio / tracking error | A DONE, P MISSING | EQ:3061-3081 | → §15 | P1 |
| Maximum drawdown | DONE | A, P | P is simulated, not the real NAV | P1 |
| Drawdown duration / recovery time | MISSING | — | add | P2 |
| VaR (historical) | DONE | A: 1260 days; P: var95/var99 1-day | — | — |
| Parametric VaR | A DONE, P MISSING | eq 109 | add P | P2 |
| Monte Carlo VaR | PARTIAL | p5 of ending value | → label it VaR/ES at the horizon | P2 |
| Expected shortfall | DONE | es95 shown; es99 computed, not shown | show | P3 |
| Skewness / kurtosis | A DONE, P MISSING | — | add P | P3 |
| Tail risk | PARTIAL | VaR99/ES99, kurtosis | — | P3 |
| Upside / downside capture | MISSING | — | add | P2 |
| Calmar | MISSING | — (not in backtests either) | add | P2 |

## 12. Portfolio risk
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Portfolio variance / volatility | DONE | P:369-370 | — | — |
| Covariance matrix | PARTIAL | pairwise sample ×252 over 756 days (P:212-225) | not guaranteed positive semi-definite; missing pairs become 0 → Ledoit-Wolf shrinkage on an aligned sample | P1 |
| Correlation matrix | DONE | UI heatmap | label says "1-year" but the window is 3 years → fix the label | P3 |
| Marginal contribution to risk | DONE | `marginal_risk` | — | — |
| Component contribution to risk | PARTIAL | w·MRC not exposed | expose | P3 |
| Percentage contribution to risk | DONE | `risk_contribution` | — | — |
| Concentration risk | MISSING | — | → HHI, top-1/top-5 weight | P1 |
| Sector / geographic / currency concentration | PARTIAL | exposure donuts only | → risk by group | P2 |
| Factor concentration | PARTIAL | UI-side ratio | move server-side with a flag | P2 |
| Correlation clustering | MISSING | — | → hierarchical clustering of the correlation matrix | P2 |
| Effective number of positions | MISSING | — | → 1/HHI and exp(entropy of risk shares) | P2 |
| Diversification ratio | PARTIAL | optimiser page only | add to analytics | P3 |

## 13. Factor model
Factor ETFs (P:20-25): Market SPY; Size IWM−SPY; Value IWD−IWF; Momentum MTUM−SPY; Quality QUAL−SPY;
Low vol USMV−SPY; Tech QQQ−SPY; Rates IEF; Credit HYG−IEF; Dollar DXY; Oil WTI; Gold GOLD. The model is
weekly OLS on about 150 weeks with classical standard errors and all 12 factors at once (collinear).
| Item | Status | Limitation → Recommendation | P |
|---|---|---|---|
| Market, size, value, momentum, quality, low vol | DONE | ETF-spread proxies | — |
| Growth | PARTIAL | inverse of value / QQQ−SPY | P3 |
| Profitability, investment | MISSING | → optional Fama-French data (Ken French library CSV) | P3 |
| Rates, credit, USD, oil, gold | DONE | — | — |
| Inflation | MISSING | → TIP−IEF breakeven factor | P2 |
| Liquidity | MISSING | → NFCI changes (after the vintage fix) | P3 |
| Portfolio factor exposure | DONE | collinear; no HAC → ridge or a reduced factor set; Newey-West t | P2 |
| Factor contribution to return | MISSING | → β·f per period + residual (§14) | P1 |
| Factor contribution to risk | PARTIAL | split of explained variance only; residual excluded → include idiosyncratic risk | P2 |
| Rolling factor exposure | MISSING | → 52-week rolling betas chart | P2 |

## 14. Performance attribution
| Item | Status | Limitation → Recommendation | P |
|---|---|---|---|
| Best / worst contributors | MISSING | → Σ w_i,t−1 · r_i,t per period | P1 |
| Contribution by asset | MISSING | only unrealised P&L since cost | P1 |
| Contribution by sector / asset class / country / currency | MISSING | → group the asset contributions | P1 |
| Market beta, sector, momentum, rates, FX, commodity contribution | MISSING | → factor attribution with the §13 betas | P1 |
| Residual / idiosyncratic return | MISSING | → return − Σβf | P1 |
| "Why Did My Portfolio Move Today?" page | MISSING | → new section on the dashboard and Portfolio (1D/1W/1M/YTD) | P1 |

## 15. Benchmarking
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| User-selected benchmark | MISSING | SPY hard-coded (align.py:16, P:380) | → a settings entry, including a blended benchmark | P1 |
| SPY default | DONE | — | — | — |
| Benchmark return / excess return | A DONE, P MISSING | — | → portfolio vs benchmark | P1 |
| Alpha / beta | A DONE, P PARTIAL | — | — | P1 |
| Tracking error / information ratio | A DONE, P MISSING | — | — | P1 |
| Rolling relative performance | A DONE, P MISSING | — | — | P2 |
| Drawdown vs benchmark | MISSING | — | — | P2 |
| Benchmarks per asset class (equities, bonds, commodities, balanced) | MISSING | bonds, FX and crypto all measured vs SPY | → a class → benchmark map (AGG, DBC, 60/40) | P2 |

---

## 16. Quant signal library
The catalogue has 156 equations; all 156 have handlers (EQ `_H`), and failures return empty rather than raising
(EQ:2898-2908). By design some equations return None (e.g. OU items when AR φ ∉ (0,1)), use proxies (bond, swap and
credit items on non-bonds flagged `applies=False`; portfolio items on a 50/50 asset/SPY proxy), or use placeholders
(rf 4% fallback, D_t = 0 in eq 1). **The equations are computed, not only displayed.** The ~64 *signals* are the
FEATURES; the equation tab's items carry their own interpretation.

| Field | Status | Where | Limitation → Recommendation | P |
|---|---|---|---|---|
| Formula | PARTIAL | Equations tab (LaTeX) | features have a one-line description only → add formula to FEATURES | P2 |
| Variables | PARTIAL | Equations tab | same | P2 |
| Current value | DONE | bundle `current` | not shown on the asset page | P3 |
| Standardised value | DONE | z | — | — |
| Historical percentile | DONE | bundle/equation cards | — | — |
| Direction | DONE | trend | — | — |
| Interpretation | PARTIAL | strength label; equation cards | — | P2 |
| Expected horizon | DONE | best_horizon | uncorrected max over 9 horizons (§59) | P2 |
| Predictive IC | DONE | rank IC | — | — |
| Hit rate | DONE | \|z\|>0.25 | direction from in-sample IC, so biased upward | P2 |
| p-value | DONE | Student t on n_eff | — | — |
| Corrected significance | PARTIAL | BH q per asset × horizon; not in bundle `current` rows | → add q to rows; wider correction (§59) | P1 |
| Independent sample estimate | DONE | n_eff | — | — |
| Regime performance | DONE | by_regime (h ≤ 252) | regime p uncorrected | P2 |
| History chart | PARTIAL | rolling IC in the drawer; `/signal/{name}` endpoint unused | → signal history chart in the drawer | P2 |
| Source data | PARTIAL | description exists but is not shown | show source + publication lag | P2 |

## 17. Signal-horizon analysis
All 9 horizons (1D, 1W, 1M, 3M, 6M, 12M, 3Y, 5Y, 10Y) are evaluated: **DONE**. 10Y is reported as insufficient
evidence (about 2 independent windows).
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| IC (Pearson) | MISSING | "ic" is Spearman | → add Pearson for reference | P3 |
| Rank IC | DONE | H:183 | — | — |
| Directional hit rate | DONE | — | biased (above) | P2 |
| Average future return | PARTIAL | quintile means in the drawer | → mean return when the signal is long vs short | P2 |
| Median future return | MISSING | — | add | P3 |
| Standard error / confidence interval | MISSING | t only | → SE = 1/√(n_eff−3) Fisher CI | P1 |
| p-value | DONE | — | — |
| Effective independent sample size | DONE | n / max(h, persistence) | persistence estimated on the full sample for the live matrix | P3 |
| Multiple-testing correction | PARTIAL | BH within asset × horizon only | §59 | P1 |
| Stability | DONE | thirds sign agreement | — | — |
| Regime dependency | DONE | by_regime | — | — |

## 18. Quant score
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Bounded −100 … +100 | DONE | 100·tanh(C)·strength (SC:83-87) | — | — |
| Separate score per horizon | DONE | 9 horizons | — | — |
| Weak evidence damped | DONE | strength × sample_factor (n_eff/30) | — | — |
| Uses point-in-time data | PARTIAL | live score uses today's full-history evidence (correct for today); history is point-in-time but differs from the live formula | P0 (see §8) | P0 |
| Weights learned from past data | DONE | usefulness = IC·min(1,\|t\|/2) | — | — |
| Regime weighting | DONE | blend toward regime usefulness (SC:31-42) | not in the history | P0 |
| Expected return | PARTIAL | in-sample OLS on the full-history composite | P0 (see §8) | P0 |
| Expected error | PARTIAL | in-sample RMSE | → out-of-sample RMSE and a quantile interval | P0 |
| Confidence | DONE | sample × (evidence, accuracy, stability, regime, data, agreement) | the agreement term is never passed (constant 0.5) → pass it | P2 |
| Signal contribution explanation | DONE | explain() top 12, bullish/bearish/neutral | — | — |
| Historical score | DONE | score_history, refit every 63 sessions | regime blend missing | P0 |
| Score calibration | **MISSING** | none | **[reproduced]** point-in-time composite quintiles vs realised 3M return: SPY Q1 +3.9%, Q5 +7.0% (middle not ordered); NVDA roughly increasing; **TLT inverted** (Q1 −1.3%, Q5 −2.9%). So a +80 does not reliably mean more than +30 → calibration table (score bucket → realised mean, hit rate, n) per asset × horizon; report the monotonicity; damp or flag the score when the out-of-sample IC ≤ 0 | P1 |
| Realized subsequent performance | PARTIAL | out-of-sample IC of the composite | quant forecasts are never written to the prediction ledger → record and grade them like ML | P1 |

## 19. Shaffer Score
*Updated after the built-in formula landed (v1.0).* The score is SS = 100·tanh(Σ_f W·[Σ w·s·c·r·d]·A·H / K), per
asset and horizon (see `finsim2/shaffer_score.py`).
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Loaded safely | DONE | built-in; an override file is imported per request; a failed load falls back to the built-in and the source says so | override re-imported every call → cache by mtime | P3 |
| Failure doesn't crash the app | DONE | `evaluate` catches everything, clamps to ±100 | — | — |
| Available inputs documented | DONE | `score_asset` docstring | — | — |
| Output schema documented | DONE | horizons → score, numerator, K, families → signals | — | — |
| Horizon-specific score | DONE | 9 horizons | — | — |
| Explanation | DONE | family and signal terms (s, w, c, r, d, W, A, H) on the Analytics tab | — | — |
| Confidence | PARTIAL | c per signal, W per family; no single confidence number | → summarise Σ W·A·H / Σ A·H as coverage | P2 |
| Appears in Asset Research / Analytics / Markets / Watchlist / Portfolio | DONE | tile, header, strip, tab, columns | — | — |
| Historical Shaffer Score / backtest | MISSING | → compute with the point-in-time evidence at each refit, like the Quant Score history | P1 |
| Comparison with Quant/ML | PARTIAL | side by side on the tab and strip | → agreement + out-of-sample IC | P2 |
| Calibration (K) | PARTIAL | KAPPA = 0.04 from 10 years of weekly readings of 5 assets, applying today's evidence to past readings (scale only, in-sample) | → recalibrate on the whole researched universe with point-in-time evidence | P1 |

## 20. ML features (64 features, 12 families)
Families: **Returns** (ret_1d…ret_12m, excess_3m), **Momentum** (mom_12_1, rel_strength_6m, ma_cross),
**Statistics** (z_20, z_50, dist_ma200, skew_60, kurt_60, pctile_252), **Risk** (vol_20, vol_60, downside_vol_60,
sharpe_252, sortino_252, drawdown_252, beta_252, corr_252, alpha_252, idio_vol_252), **Volatility** (ewma_vol,
garch_vol, vol_ratio, vol_of_vol, vol_pctile), **Technical** (rsi_14, macd, bb_pctb, volume_z), **TimeSeries**
(ar1_63, acf1_252, half_life, adf_t), **Valuation** (value_5y, earnings_yield, pe_rel_5y), **Fundamentals**
(eps_growth_yoy, rev_growth_yoy, net_margin), **Rates** (y10, d_y10_3m, slope_10y3m, d_slope_3m, real_y10,
breakeven_10y, rate_beta_252), **Credit** (credit_spread, d_credit_3m), **Macro** (vix, d_vix_1m, dollar_mom_3m,
oil_mom_3m, gold_mom_3m, cpi_yoy, unemp_gap, nfci, fed_bs_growth, dollar_beta_252). There is no separate
"cross-asset" or "regime" family: the cross-asset features are beta/corr/alpha/idio (vs SPY), rate_beta and
dollar_beta, and the regimes are separate labels, not features.

| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Exact definition | PARTIAL | code + one-line description (features.py:20-85) | → formula per feature in FEATURES and the UI | P2 |
| Data source | PARTIAL | implicit | → `source` field | P2 |
| Publication timing | DONE (macro/SEC) | lag days; filed date | revision leak (§8); `breakeven_10y` uses T5YIE (5-year) → relabel | P0 / P3 |
| Normalisation | DONE | expanding z, cap ±3 | — | — |
| Missing-value treatment | DONE | None propagates; ≤5 ffill; macro ≤400 days; ML median imputation on train | — | — |
| Winsorisation | PARTIAL | z capped ±3; raw features not winsorised | acceptable | P3 |
| Point-in-time safety | PARTIAL | trailing windows everywhere | EPS split bug; FRED vintages | P0 |
| Expected asset classes | MISSING | fundamentals features None for non-equities (by data) | → `applies_to` field; hide inapplicable rows | P3 |

## 21. ML targets
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Continuous future return | DONE | ln(P[t+h]/P[t]) | — | — |
| Direction target | PARTIAL | logistic on sign of the same return | → separate reported model with calibration | P1 |
| Risk-adjusted target | PARTIAL | pooled models only (vol-scaled) | → per-asset option | P2 |
| Volatility target | MISSING | — | → future realised vol model (highly predictable; valuable for risk) | P1 |
| Drawdown target | MISSING | — | → P(max drawdown over h > X%) classifier | P2 |

## 22. ML models
| Item | Status | Implementation | Recommendation | P |
|---|---|---|---|---|
| Linear, ridge, LASSO, elastic net, logistic, random forest, gradient boosting | DONE | models.py; fixed hyperparameters | — | — |
| Extra trees | MISSING | — | cheap to add (random thresholds on the existing tree) | P3 |
| HistGradientBoosting | PARTIAL | the GBM is histogram-binned internally | — | — |
| XGBoost / LightGBM / CatBoost | MISSING | stdlib-only project | optional import if installed; not needed | P3 |
| Bayesian regression | MISSING | — | → gives predictive intervals | P2 |
| Quantile regression | MISSING | — | → prediction intervals (§58) | P1 |
| PCA regression | MISSING | PCA exists, unused | P3 |
| Hidden Markov models (regimes) | MISSING | — | → probabilistic regimes (§30) | P2 |
| Deep learning | N/A | optional by design | — | — |

## 23. ML training process
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Chronological split | DONE | rows in time order | — | — |
| Walk-forward validation | DONE | `_folds`: first 40% train-only, up to 6 test blocks, expanding window | — | — |
| Embargo / buffer | DONE | train row i only if idx[i]+h < block start (ML:75) | no extra embargo beyond h; ensemble weights unpurged (§8) | P1 |
| No random split | DONE | — | — | — |
| No target leakage | PARTIAL | purge correct | no tests of `walk_forward`/`_folds` at all → add | P1 |
| No future normalisation | DONE | Preprocessor fitted on train | — | — |
| Retraining schedule | MISSING | on demand only | → retrain monthly in the scheduler when stale | P2 |
| Minimum sample requirement | DONE | 80 rows; 60% coverage | — | — |
| Hyperparameter tuning isolated | DONE (no tuning) | fixed | — | — |
| Final holdout set | MISSING | every out-of-sample block is used for ranking, weights and importance | → reserve the last 15% as an untouched holdout reported separately | P1 |
| Reproducible seeds | DONE | seed 0/1 | not recorded | P2 |
| Model versioning | PARTIAL | `VERSION` string; `model_runs` table | **recorded params are the defaults, not the ones used** (80/30 trees recorded as 100/40); `train_end` wrong; fitted models not persisted; `ml:{asset}` overwritten and not tied to data_version | P1 |

## 24. ML evaluation
| Item | Status | Implementation | Recommendation | P |
|---|---|---|---|---|
| MAE | PARTIAL | ledger report only | add to leaderboard | P2 |
| RMSE | DONE | — | — | — |
| R² (out of sample) | MISSING | — | add (vs zero and vs mean) | P1 |
| Correlation with realised return | PARTIAL | Spearman only | add Pearson | P3 |
| IC / rank IC | DONE | — | — | — |
| Accuracy | DONE | direction hit | — | — |
| Balanced accuracy / precision / recall / F1 | MISSING | — | add for the logistic | P2 |
| ROC AUC | MISSING | — | add | P2 |
| Brier score | MISSING | — | add | P1 |
| Calibration | MISSING | prob_up from an in-sample logistic | → reliability table out of sample | P1 |
| Strategy Sharpe | PARTIAL | sign(pred)·y, no costs | → after costs | P2 |
| CAGR / max drawdown / turnover / after-cost | PARTIAL | backtest page, ensemble only | → per model in the leaderboard | P2 |
| Hit rate | DONE | — | — | — |

## 25. Model baselines
| Baseline | Status | Recommendation | P |
|---|---|---|---|
| Predicting zero | PARTIAL | "beats noise" = ensemble IC > 0 and robust > 0; RMSE is never compared with a zero forecast | P1 |
| Historical mean return | MISSING | add | P1 |
| Previous-period return | MISSING | add | P1 |
| Buy and hold | PARTIAL | backtest benchmark only | add to leaderboard | P1 |
| Random signal | MISSING | → permutation p-value of the IC | P1 |
| Simple momentum | MISSING | → sign(12-1 momentum) | P1 |
| Simple mean reversion | MISSING | → −z_20 | P1 |
The ML score should be non-zero only when the ensemble beats the **best** baseline out of sample (P1).

## 26. ML score
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| −100 … +100 | DONE | (2·pct−1)·100·quality | — | — |
| Separate per horizon | DONE | — | — | — |
| Zero when no model beats baseline | PARTIAL | zero when ensemble IC ≤ 0 | baseline is zero IC, not simple baselines (§25) | P1 |
| Confidence | DONE | sample × (quality, stability, agreement) | — | — |
| Model agreement | DONE | share of models agreeing; quant agreement label | — | — |
| Feature importance | DONE | permutation on the last block | §28 | — |
| Expected return | DONE | exp(ensemble)−1 | — | — |
| Expected error | PARTIAL | whole-sample OOS RMSE | → quantile interval | P1 |
| Prediction history | DONE | predictions table | — | — |
| Realised grading | DONE | `score_matured` after each refresh | untested; rows with missing prices retried forever | P1 |
| Model decay detection | PARTIAL | binary flag from windowed ICs of OOS forecasts | → HEALTHY/WEAKENING/DECAYING/INSUFFICIENT from the live ledger (§56) | P1 |
| Calibration | MISSING | — | §24 | P1 |
| (extra) Stale after refresh | BUG | `ml:{asset}` not versioned by data_version; bundle shows an old ML score | → store data_version, show "trained on data to …", mark stale | P1 |

## 27. Ensembles
| Item | Status | Implementation | Recommendation | P |
|---|---|---|---|---|
| Equal weighting | PARTIAL | first block only | report as a baseline ensemble | P2 |
| Performance weighting | DONE | max(0, mean previous-fold IC) | purge (§8) | P1 |
| Regime weighting | MISSING | — | P3 |
| Model diversity | MISSING | → report pairwise prediction correlation | P3 |
| Ensemble out-of-sample improvement | MISSING | shown side by side only → "ensemble IC − best single IC" field | P2 |

## 28. Feature importance
| Item | Status | Implementation | Recommendation | P |
|---|---|---|---|---|
| Regression coefficients | PARTIAL | computed in models.py, unused by ml.py | expose | P2 |
| Standardised coefficients | PARTIAL | same | expose | P2 |
| Tree importance | PARTIAL | gain computed, unused | expose | P3 |
| Permutation importance | DONE | last held-out block, 2 repeats | same block used for weights → use the holdout (§23) | P1 |
| SHAP | PARTIAL | Saabas path attribution for trees; exact for linear | adequate; label "Saabas (approximate SHAP)" | P3 |
| Feature stability | MISSING | → importance per fold, rank correlation | P2 |
| Sign stability | MISSING | → sign of linear coefficients per fold | P2 |
| Importance over time | MISSING | per fold | P2 |
| Importance by regime | MISSING | — | P3 |
| Importance by horizon | DONE | per horizon + short/long families | — | — |

## 29. Model explainability
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Bullish / bearish / neutral contributors with sizes | DONE | quant: `explain` (asset page); ML: per-forecast contributions (ML Lab) | never side by side; contributions are in z-units, not score points → show both in score points (e.g. "Momentum +11") on the asset page | P2 |

## 30. Regime engine
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Exact regime definitions | DONE | market: SPY > MA200 and drawdown > −20%; volatility: VIX > trailing 5-year median; rates: Δ10y over 63 sessions > 0; inflation: CPI y/y > 3%; growth: Sahm gap ≥ 0.5; dollar: DXY > MA200; liquidity: NFCI < 0 | document in the UI | P2 |
| No hindsight | PARTIAL | trailing windows | FRED revisions (NFCI!) | P0 |
| Regime transition dates | MISSING | → list of switches | P2 |
| Probabilities vs hard labels | MISSING (labels only) | → logistic or HMM probabilities | P2 |
| Historical durations | MISSING | — | P2 |
| Regime performance statistics | PARTIAL | history_share; IC by regime | → asset return/vol by regime | P2 |
| Regime-specific signal performance | DONE | by_regime (h ≤ 252) | — | — |
| Regime-specific ML performance | MISSING | — | → split out-of-sample IC by regime | P2 |

## 31. Cross-asset analysis
| Item | Status | Implementation | Recommendation | P |
|---|---|---|---|---|
| Rolling correlations | PARTIAL | corr_252 vs SPY; correlation_decay 5Y/1Y/3M | `/correlations` unused in the UI → pair-explorer page | P2 |
| Rolling beta | DONE | beta_252, rate_beta_252, dollar_beta_252 | — | — |
| Lead/lag, cross-correlation | MISSING | → CCF at ±1…±21 sessions | P2 |
| Granger tests | MISSING | → with n_eff caveats | P3 |
| Cointegration | MISSING | → Engle-Granger for pairs | P3 |
| Spread z-scores | PARTIAL | credit spread z, relative strength | → pair spread z | P3 |
| Regime-dependent correlation | MISSING | — | P2 |
| Named relationships (stocks vs rates, gold vs real rates, BTC vs liquidity, …) | PARTIAL | only via asset features (rate_beta, real_y10 IC) | → a "macro relationships" block per asset: rolling corr/beta to y10, real y10, DXY, oil, credit, VIX | P2 |

## 32. Macro analytics
FRED series (19): DGS3MO, DGS2, DGS5, DGS10, DGS30, T10Y3M, T10Y2Y, BAA10Y, DBAA, CPIAUCSL, UNRATE, INDPRO, T5YIE,
DFII10, NFCI, WALCL, M2SL, VIXCLS, DTWEXBGS.
| Item | Status | Recommendation | P |
|---|---|---|---|
| Fed funds | MISSING | add DFF | P1 |
| 2Y / 10Y / 30Y | DONE | — | — |
| 2s10s | PARTIAL | fetched, unused → feature | P2 |
| 3m10y | DONE | — | — |
| CPI | DONE | — | — |
| Core CPI / PCE / core PCE | MISSING | add CPILFESL, PCEPI, PCEPILFE | P2 |
| Unemployment | DONE | — | — |
| Payrolls | MISSING | add PAYEMS | P2 |
| GDP | MISSING | add GDPC1 (quarterly, long lag) | P3 |
| Industrial production | PARTIAL | fetched, unused | P3 |
| Retail sales | MISSING | add RSAFS | P3 |
| Money supply | PARTIAL | M2 fetched, unused | P3 |
| Credit spreads | PARTIAL | BAA10Y only → HY/IG OAS | P2 |
| VIX | DONE | — | — |
| Dollar index | DONE | — | — |
| Liquidity proxies | DONE | WALCL, NFCI | vintage leak | P0 |
| Macro dashboard | MISSING | → a Macro page: levels, changes, percentiles, regime history, curve | P1 |

## 33. Economic surprises
MISSING. There is no consensus data source in the allow-listed feeds. → **Document as unavailable; do not
fabricate expectations.** A possible proxy is change versus the trailing trend, clearly labelled "vs trend, not vs
consensus". P3.

## 34. Backtest engine
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Signal backtests | DONE | any feature z | **direction from the full-sample IC (leak)** | P0 |
| Quant Score backtests | DONE | point-in-time composite | uses the composite C, not the score; entry "1.0" applies to C → document | P2 |
| ML Score backtests | DONE | OOS ensemble | **full-sample z (leak)** | P0 |
| Shaffer Score backtests | MISSING | — | §19 | P1 |
| Long only / long-short | DONE | — | — | — |
| Threshold entry / exit | DONE | symmetric | — | — |
| Rebalance frequency | MISSING | daily + min_hold | add weekly/monthly | P2 |
| Transaction costs | DONE | bps per unit change | per-trade stats gross of costs → net | P1 |
| Slippage | MISSING | → extra bps | P2 |
| Borrow costs | MISSING | → for shorts | P2 |
| Benchmark | PARTIAL | "benchmark" = the asset's own buy & hold; alpha/beta vs SPY | label it clearly | P2 |
| Position sizing | MISSING | always ±100% | → vol-target sizing | P2 |
| Maximum positions | N/A | single-asset engine | portfolio backtest later | P2 |
| Stop loss / take profit | MISSING | — | optional | P3 |
| Execution timing | **LEAK-ish** | same-close execution | **default 1-session lag** | P0 |
| Metrics: return, CAGR, vol, Sharpe, Sortino, max DD, win rate, profit factor, avg win/loss, exposure, turnover, alpha, beta | DONE | BT:129-135 | Sharpe/Sortino with rf = 0; turnover = changes per year | P2 |
| Metric: Calmar | MISSING | add | P2 |

## 35. Walk-forward backtesting
| Item | Status | Implementation | Recommendation | P |
|---|---|---|---|---|
| Train / predict future / move window / retrain / predict next | DONE | expanding walk-forward, 6 blocks | — | — |
| Validate (separate validation set) | MISSING | no validation split (no tuning) | fine while there is no tuning | P3 |
| Concatenate genuinely out-of-sample results | DONE | concatenated OOS series | only the last 600 rows are kept → keep all | P2 |
| IN SAMPLE / OUT OF SAMPLE / LIVE-LIKE shown separately | PARTIAL | OOS chart (x-axis is the row index, not dates); forecast ledger ≈ live | → three labelled bands with dates | P1 |

## 36. Portfolio optimisation
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Minimum variance | DONE | projected gradient | — | — |
| Max Sharpe | PARTIAL | best of a 40-point λ grid | approximate → refine | P3 |
| Mean-variance frontier | DONE | 25 points | — | — |
| Risk parity | DONE | ignores the cap | apply the cap | P3 |
| Equal weight | DONE | — | — | — |
| Inverse volatility | MISSING | add | P2 |
| Maximum diversification | MISSING | ratio only | add | P3 |
| Target volatility | MISSING | → scale with cash | P2 |
| Max position weight | DONE | single cap | — | — |
| Min position weight | MISSING | — | P3 |
| Max sector / asset-class weight | MISSING | — | P2 |
| Turnover constraint | MISSING | — | P2 |
| Long-only | DONE (forced) | — | — | — |
| Allow shorts | MISSING | — | P3 |
| Leverage cap | MISSING | — | P3 |
| Minimum cash | MISSING | — | P2 |
| Instability caveat shown | DONE | JS:719 | — | — |
| Expected returns | PARTIAL | 0.5·hist + 0.5·6% for every asset (bonds and gold too); quant/ML sources mix log and arithmetic units | → asset-class priors; consistent units | P1 |

## 37. Robust optimisation
| Item | Status | Recommendation | P |
|---|---|---|---|
| Covariance shrinkage | MISSING | Ledoit-Wolf | P1 |
| Expected-return shrinkage | PARTIAL | naive 50/50 → class priors | P1 |
| Black-Litterman | MISSING | market-implied prior + score views | P2 |
| Resampled frontier | MISSING | bootstrap the inputs | P2 |
| Robust covariance | MISSING | — | P3 |
| Sensitivity analysis (weights vs input changes) | MISSING | → perturb μ by ±1 SE and show the weight ranges | P2 |

## 38. Monte Carlo
| Item | Status | Implementation | Limitation → Recommendation | P |
|---|---|---|---|---|
| Asset simulation | DONE | — | — | — |
| Portfolio simulation | DONE | bootstraps the historical series at today's weights | constant weights | — |
| Correlated paths | PARTIAL | implicit through the single portfolio series | assets with missing returns dropped without reweighting → reweight | P2 |
| Covariance matrix | N/A | not used (historical series) | fine for bootstrap | — |
| Drift / volatility assumptions | DONE | historical, no shrinkage | → option to shrink drift | P2 |
| Horizon | DONE | 1M–10Y | — | — |
| Number of simulations | DONE | 200–5000 | — | — |
| Percentile distribution | DONE | 5/25/50/75/95 + cone | — | — |
| Probability of loss | DONE | — | — | — |
| Probability of target return | PARTIAL | fixed +10% | → user target | P2 |
| Drawdown distribution | PARTIAL | only P(DD beyond X) | → return the distribution | P3 |
| Historical bootstrap / block bootstrap | DONE | block 10 | block length not exposed | P3 |
| GBM | DONE | — | — | — |
| Fat-tailed distribution | MISSING | → Student-t | P3 |
| GARCH volatility | MISSING | → GARCH-filtered bootstrap | P3 |
| Regime-dependent simulation | MISSING | → bootstrap from current-regime days | P3 |

## 39. Scenario analysis
Inputs: 10Y bp, Nasdaq %, oil %, USD %, VIX (points or level), credit bp. Blank factors are filled with their
conditional expectation; bonds use duration and convexity; historical analogues are 21-session windows over up to
25 years.
| Item | Status | Recommendation | P |
|---|---|---|---|
| Stocks down | DONE | via Nasdaq % | — |
| Rates up / down | DONE | — | — |
| Curve steepening / flattening | MISSING | add a 2s10s factor | P2 |
| Credit spreads wider | DONE | — | — |
| USD up / down | DONE | — | — |
| Oil up / down | DONE | — | — |
| Gold up / down | MISSING | add | P2 |
| Crypto crash | MISSING | add BTC factor | P2 |
| Volatility spike | DONE | VIX | — |
| Inflation shock | MISSING | breakeven factor | P2 |
| Recession shock | MISSING | composite preset | P2 |
| 2000 dot-com | MISSING | only a hand-set preset list ("2022-style" etc.) → replay actual factor moves and actual asset returns over the window | P1 |
| 2008 GFC | MISSING | same | P1 |
| March 2020 | MISSING | same | P1 |
| 2022 inflation/rate shock | PARTIAL | hand-set preset, not a replay | P1 |
| Convexity | BUG | 100× too small (§5) | P1 |
| (extra) Weekly scaling | minor | macro changes use 4 sessions vs 5 for prices → fix | P2 |

## 40. Stress testing
| Item | Status | Recommendation | P |
|---|---|---|---|
| Correlations → 1 | MISSING | rescale the covariance | P1 |
| Volatility doubles | MISSING | VaR/ES at 2σ | P1 |
| Liquidity disappears | MISSING | days-to-liquidate haircut | P2 |
| Largest position −50% | MISSING | direct revaluation | P1 |
| Top 3 positions −30% | MISSING | direct revaluation | P1 |
| Rates +200bp | PARTIAL | manual scenario input | preset | P1 |
| Credit +300bp | PARTIAL | manual scenario input | preset | P1 |

## 41. Watchlist
| Item | Status | Recommendation | P |
|---|---|---|---|
| Add / remove | DONE | — | — |
| Sorting | DONE | — | — |
| Quant Score | DONE (1W/1M/3M/12M) | — | — |
| ML Score | MISSING | add | P2 |
| Shaffer Score | MISSING | add | P1 |
| Confidence | MISSING | add | P2 |
| Price / daily move | DONE | — | — |
| Valuation | MISSING | add | P3 |
| Volatility | MISSING | add | P3 |
| Best horizon | MISSING | add | P3 |
| Regime | MISSING | add | P3 |
| Alerts | MISSING | §70 | P2 |
| Notes | PARTIAL | API supports notes; UI does not show them | show | P2 |

## 42. Markets page
| Item | Status | Recommendation | P |
|---|---|---|---|
| Asset class filter | DONE | — | — |
| Sector / geography | MISSING | data present → filter chips | P2 |
| Quant / ML / Shaffer / confidence / horizon screens | PARTIAL | columns (ML only in data); no thresholds | P2 |
| Momentum / valuation / volatility / beta / Sharpe / drawdown / market cap | MISSING | columns from the light bundle | P2 |
| Sortable columns | DONE | — | — |
| Filters | PARTIAL | class only | P2 |
| Search | PARTIAL | global picker only | P3 |
| Saved screens | MISSING | — | P3 |

## 43. Asset Research page
Header: class, currency, sector, history length, ticker, name, price and 1D/1M/1Y, strongest evidence (score, label,
confidence at the primary horizon), regime, your exposure, Shaffer (when live), ML trained?
| Item | Status | Recommendation | P |
|---|---|---|---|
| Market cap | MISSING | shares × price | P2 |
| Quant / ML / Shaffer / confidence / regime / best horizon | DONE | — | — |
| Chart | DONE | line only | P3 |
| Returns | PARTIAL | tile only → a return table | P2 |
| Valuation / fundamentals / risk / momentum / volatility | MISSING here (on Analytics tabs) | → compact summary cards with links | P2 |
| Factor exposures | MISSING | — | P2 |
| Macro relationships | MISSING | §31 | P2 |
| Signal contributions | DONE | — | — |
| ML explanation | MISSING here | — | P2 |
| Equation results | MISSING here | — | P3 |
| Historical score | MISSING here | — | P2 |
| Backtest shortcut | MISSING | button | P3 |

## 44. Analytics page
| Item | Status | Recommendation | P |
|---|---|---|---|
| Overview | DONE | — | — |
| Family tabs | DONE | — | — |
| Search | PARTIAL | equations tab only | P3 |
| Favorites | MISSING | localStorage | P3 |
| Explanations | PARTIAL | §51 | P2 |
| Historical charts | PARTIAL | sparkline + histogram | P3 |
| Percentile | DONE | — | — |
| Significance | PARTIAL | cards lack p/q | P2 |
| Horizon usefulness | DONE | — | — |
| Regime usefulness | DONE | — | — |

## 45. Quant Lab
| Item | Status | Recommendation | P |
|---|---|---|---|
| Equation leaderboard | DONE | — | — |
| Signal/horizon heatmap | DONE | — | — |
| Asset selector / horizon selector | DONE | — | — |
| Regime selector | MISSING | add | P2 |
| Lookback selector | MISSING | add | P3 |
| Significance filter | MISSING | "q < 0.10 only" toggle | P2 |
| IC / hit rate / effective N | DONE | — | — |
| Score contribution | MISSING | column | P3 |
| Chart | PARTIAL | drawer rolling IC | P3 |
| Backtest button | PARTIAL | drawer only | P3 |
| Score history | DONE | — | — |

## 46. ML Lab
| Item | Status | Recommendation | P |
|---|---|---|---|
| Models | DONE | — | — |
| Train dates / OOS dates | PARTIAL | runs table only; `fold_bounds` unused | P2 |
| Validation dates | N/A | no validation split | — |
| Features | MISSING | stored, not shown | P3 |
| Hyperparameters | MISSING | stored (wrongly, §23) | P2 |
| Model performance | DONE | — | — |
| Baselines | MISSING | §25 | P1 |
| Feature importance | DONE | — | — |
| Predictions | DONE | — | — |
| Errors | DONE | — | — |
| Calibration | MISSING | §24 | P1 |
| Decay | PARTIAL | §56 | P1 |
| Retraining history | DONE | — | — |
| Model × Asset × Horizon comparison | PARTIAL | one asset only | P2 |

## 47. Risk page
| Item | Status | Recommendation | P |
|---|---|---|---|
| Portfolio VaR / ES | DONE | — | — |
| Concentration | PARTIAL | §12 | P1 |
| Beta | DONE | — | — |
| Factor exposures | DONE | — | — |
| Risk contributions | DONE | — | — |
| Scenarios | DONE | — | — |
| Stress tests | MISSING | §40 | P1 |
| Monte Carlo | DONE | — | — |
| Drawdowns | PARTIAL | number only → chart from the real NAV | P2 |
| Correlation | DONE | — | — |
| Liquidity risk | MISSING | §63 | P2 |
| Currency risk | PARTIAL | exposure only | P2 |
| Duration risk | PARTIAL | duration number + scenario | P3 |

## 48. Dashboard
| Component | Status | Recommendation | P |
|---|---|---|---|
| NAV | DONE | — | — |
| Daily P&L | MISSING | §1 | P1 |
| Total P&L | DONE | — | — |
| Benchmark comparison | MISSING | §15 | P1 |
| Top contributors / detractors | MISSING | §14 | P1 |
| Largest risks | MISSING | top risk contributions + flags | P1 |
| Portfolio Quant Score | MISSING | weight-averaged per horizon | P2 |
| Portfolio ML signal | MISSING | same | P2 |
| Regime | DONE | — | — |
| Watchlist movers | MISSING | — | P3 |
| Model changes | MISSING | score changes since yesterday | P2 |
| Alerts | MISSING | §70 | P2 |
| (bug) VaR tile | BUG | labelled "Volatility (1-day VaR 95%)" but shows annual volatility → fix the label/value | P0 |

## 49. UI / UX
| Item | Status | Notes | P |
|---|---|---|---|
| Mobile / desktop / tablet | DONE | breakpoints 1100/860px; drawer sidebar | — |
| Dark mode | DONE | — | — |
| Responsive tables | DONE | horizontal scroll | — |
| Sticky headers | DONE | — | — |
| Tooltips | PARTIAL | charts yes; table headers support titles but none set → add metric definitions | P2 |
| Readable charts | DONE | — | — |
| Loading indicators | DONE | — | — |
| Empty states | DONE | — | — |
| Error states | PARTIAL | page-level card and toasts; dashboard swallows some failures silently | P2 |
| Data freshness indicator | PARTIAL | §52 | P1 |
| Consistent decimals / % / currency formatting | DONE | `fmt` helpers | — |
| Not overloading screens | PARTIAL | the asset page and Analytics are dense but tabbed | P3 |

## 50. Charts
| Chart | Status | P |
|---|---|---|
| Candlestick | MISSING | P3 |
| Line | DONE | — |
| Volume | MISSING | P3 |
| Returns | PARTIAL (histograms) | P3 |
| Drawdown | MISSING | P2 |
| Volatility | PARTIAL (equation sparklines) | P3 |
| Rolling beta | MISSING (data exists) | P3 |
| Correlation | DONE | — |
| Valuation | PARTIAL | P3 |
| Score | DONE | — |
| ML forecast | DONE (x-axis = row index → use dates) | P2 |
| Signal contribution | PARTIAL (text) | P3 |
| Feature importance | DONE | — |
| Efficient frontier | DONE | — |
| Monte Carlo fan | DONE | — |
| Factor exposure | DONE | — |
| Yield curve | MISSING | P2 |
| Synchronised date ranges | MISSING | P3 |

## 51. Explanations / education
| Item | Status | Recommendation | P |
|---|---|---|---|
| Formula | DONE (equations) / PARTIAL (features) | §16 | P2 |
| Variable definitions | DONE (equations) | — | — |
| Plain-English meaning | PARTIAL | interpretation text | P2 |
| Why it matters | MISSING | add to catalogue entries | P2 |
| Common interpretation mistakes | MISSING | add | P3 |
| Current value | DONE | — | — |
| Example | MISSING | add | P3 |

## 52. Data freshness
| Item | Status | Recommendation | P |
|---|---|---|---|
| Last price update | PARTIAL | SPY date pill (always green) | P1 |
| Last fundamentals update | MISSING | P1 |
| Last FRED update | MISSING (`last_macro_date` exists) | P1 |
| Last model training | DONE | — |
| Last score calculation | PARTIAL | as_of + seconds | P2 |
| Stale warnings | MISSING | amber/red pill by age; per-asset stale list | P1 |

## 53. Caching / performance
| Item | Status | Notes | P |
|---|---|---|---|
| Analytics caching | DONE | kv keyed by data_version + BUNDLE_VERSION | — |
| Database indexing | DONE | PKs + 4 indexes | — |
| Past score caching | DONE | scorehist kv | — |
| ML result caching | PARTIAL | not versioned by data (§26) | P1 |
| Background data refresh | PARTIAL | scheduler every 15 minutes; refresh when SPY is stale; no 17:30 time check despite the docstring; exceptions swallowed | P2 |
| Lazy calculation | DONE | bundles on request / scan | — |
| Expensive page load avoidance | PARTIAL | `is_cached` loads each full bundle JSON (133 in Markets); kv never pruned; portfolio ledger replayed 2–5× per page | → cache light rows; prune kv; snapshot NAV | P2 |
| Targets | measured: cached asset page ≈1 s; uncached bundle ≈18 s; portfolio page ≈1 s on a small ledger | the portfolio page will slow as the ledger grows (full replay) | P2 |

## 54. Database design
Tables: assets, prices, macro, fundamentals, kv, fetch_log, predictions, model_runs, backtests, portfolios,
transactions, snapshots (unused), watchlist, store_meta.
| Entity | Status | Notes | P |
|---|---|---|---|
| Assets | DONE | — | — |
| Prices | DONE | — | — |
| Fundamentals | DONE | — | — |
| Macro | DONE | no vintage column → add `realtime_start` | P0 (with the vintage fix) |
| Features | kv blob | fine (derived) | — |
| Signals | kv blob | fine | — |
| Scores | kv blob | no score history table → a `score_log` for the quant forecasts ledger | P1 |
| Predictions | DONE | — | — |
| Realized outcomes | DONE (columns in predictions) | — | — |
| Models / model versions | PARTIAL | model_runs; fitted models not persisted | P2 |
| Portfolio | DONE | only "main" is used | P2 |
| Trades / cash flows | DONE | transactions | — |
| Positions | derived | fine | — |
| Corporate actions | MISSING | needed | P0 |
| Backtests | PARTIAL | spec + metrics; no data_version | P1 |
| Scenarios | MISSING | not stored | P3 |
| Indexes match queries | DONE | — | — |

## 55. Prediction ledger
Columns: id, asset_id, horizon, model, model_version, made_on, target_date, predicted, error_band, confidence, score,
realized, error, scored_on, detail (only `{weights}`).
| Field | Status | Notes | P |
|---|---|---|---|
| Prediction timestamp | PARTIAL | data date only, no wall-clock | P2 |
| Asset / horizon / model / version | DONE | model is always "ml_ensemble" | — |
| Features snapshot | MISSING | store the top contributions | P2 |
| Prediction / confidence / expected error | DONE | — | — |
| Regime | MISSING | add | P2 |
| Due date / actual / error | DONE | due date ignores holidays | P3 |
| Correct direction? | PARTIAL | computed on the fly | P3 |
| Grading status | DONE (implicit) | — | — |
| Never overwritten | DONE | only the grading UPDATE; a retrain on the same data date is silently not logged | P2 |
| Quant forecasts recorded | MISSING | §18 | P1 |

## 56. Model decay
| Item | Status | Recommendation | P |
|---|---|---|---|
| Rolling IC | PARTIAL | windowed ICs on the OOS series | — |
| Rolling hit rate / MAE / calibration / strategy Sharpe | MISSING | add | P1 |
| Alerts on deterioration | PARTIAL | a message string + tile | P2 |
| HEALTHY / WEAKENING / DECAYING / INSUFFICIENT DATA | MISSING | → classify from the live graded ledger and the OOS series | P1 |

## 57. Signal decay
PARTIAL: `ic_thirds` and `ic_recent` per signal, plus a rolling 3-year IC in the drawer. → A per-signal table of IC over
10Y/5Y/2Y/1Y with WEAKENING/REVERSED flags, shown in the leaderboard. P1.

## 58. Uncertainty
| Item | Status | Recommendation | P |
|---|---|---|---|
| Prediction | DONE | — | — |
| Confidence | DONE | — | — |
| Prediction interval | MISSING | quantile regression or empirical out-of-sample residual quantiles | P1 |
| Evidence strength | DONE | strength, n_eff, q | — |

## 59. Multiple testing
| Method | Status | Notes | P |
|---|---|---|---|
| Benjamini–Hochberg FDR | PARTIAL | within one asset × one horizon across ~64 signals | — |
| Holm | MISSING | — | P3 |
| Bonferroni | MISSING | — | P3 |
| Selection across horizons, assets, regimes and ML models | MISSING | best_horizon / primary_horizon / leaderboard picks are uncorrected → BH across the whole signal × horizon family per asset; state it in the UI | P1 |
| Explanation of the method used | PARTIAL | README/ARCHITECTURE | also in the UI tooltip | P2 |

## 60. Statistical robustness
| Item | Status | Notes | P |
|---|---|---|---|
| Autocorrelation | PARTIAL | persistence → n_eff | — |
| Heteroskedasticity | MISSING | classical OLS standard errors in factor regressions → White/HAC | P2 |
| Overlapping returns | PARTIAL | n_eff; the expected-return RMSE ignores overlap | P2 |
| Effective sample size | DONE | — | — |
| Bootstrap confidence intervals | MISSING | block bootstrap for IC | P2 |
| Non-normal distributions | PARTIAL | rank IC; JB shown | — |
| Outlier sensitivity | PARTIAL | z capped | — |
| Regime sensitivity | DONE | — | — |

## 61. Correlation vs causation
PARTIAL. Predictive wording is used in many places ("have actually predicted", "predictive score"), but
causal-sounding labels sit on statistical outputs: "What drives the portfolio", "Drivers" tab, "What currently
drives my portfolio", "What matters right now", "Why: score", "equation-driven", "Estimated portfolio impact".
→ Rename to "Associated with", "Historically predictive of", "Largest statistical contributors", and keep "impact"
only for the scenario engine with "linear model" alongside. P2.

## 62. Model discovery vs confirmation
MISSING. There is one expanding walk-forward, with no research / validation / untouched split. → Freeze a final
holdout (the last ~15% of history, or everything after a fixed date) that is only evaluated on an explicit
"confirm" run, and record every confirm run. P1.

## 63. Liquidity analysis
MISSING: ADV, dollar volume, volume percentile, days to liquidate, position/ADV, bid-ask proxy and liquidity score.
Volume is stored and used only for `volume_z`. → A liquidity block (ADV 20/60, $ADV, position/ADV, days to
liquidate at 20% participation, Corwin-Schultz spread from high/low) plus a portfolio liquidity score. P2.

## 64. Position sizing
| Tool | Status | P |
|---|---|---|
| Equal weight | DONE | — |
| Volatility targeting | MISSING | P2 |
| Risk parity | DONE | — |
| Kelly / fractional Kelly | MISSING (show with an instability warning only) | P3 |
| Max position cap | DONE | — |
| Score-weighted sizing | PARTIAL (quant/ML μ in mean-variance) | P2 |

## 65. Tax lots
MISSING. Average cost only; no lots are stored. FIFO/LIFO/specific identification are optional. P3.

## 66. Income analytics
MISSING: dividend income, yield, yield on cost, coupon income, interest on cash, projected income, income by month.
Depends on dividend ingestion (P0). P2.

## 67. Rebalancing
PARTIAL: the optimiser shows Current vs model weights. Missing: stored target weights, drift, rebalance trade list,
turnover and estimated costs. → Save a target (from the optimiser or manually), show drift, generate a trade list
(not executed automatically). P2.

## 68. Portfolio comparison
PARTIAL: the store supports many portfolios, but `App.ledger()` and the routes hard-code "main"; the UI has one
portfolio. → Portfolio selector, create/rename, and an A-vs-B comparison page. P2.

## 69. Security comparison
MISSING. → Side-by-side page for 2–4 assets: returns, risk, valuation, scores, ML, factor betas, correlation. P2.

## 70. Alerts
MISSING (only the ML decay message and the correlation-decay flag). → A rule table evaluated after each refresh:
score thresholds, confidence changes, decay, volatility spikes, drawdown, concentration, valuation extremes, regime
changes, stale data; shown in the UI (no external push). P2.

## 71. Saved research
PARTIAL: backtests are saved (spec + metrics). Missing: notes, charts, active signals, thesis and snapshot scores
with later results. → §72. P2.

## 72. Investment thesis tracker
MISSING. → `theses` table: ticker, long/short, thesis, horizon, target, risk, invalidation, snapshot of price and
Quant/ML/Shaffer scores at creation, and automatic grading at the horizon. P2.

## 73. Decision journal
PARTIAL: only the transaction note and the (API-only) watchlist note. → Built on theses: record what I thought,
what Quant and ML said, and what happened; aggregate hit rates of human vs Quant vs ML vs combinations. P2.

## 74. Exporting
MISSING (raw JSON GET routes only). → CSV/JSON download for NAV history, transactions, positions, analytics tables,
model results, backtests. P1.

## 75. Importing
MISSING. → CSV import of transactions (date, kind, asset, quantity, price, fee, currency, note) with a dry-run
validation report; holdings snapshot import as a dated opening balance. Broker integration stays separate. P1.

## 76. Error handling
| Case | Status | Notes | P |
|---|---|---|---|
| Bad ticker | PARTIAL | regex → 400, but a Yahoo 404 raises YahooError, which is not a ValueError → **HTTP 500** | P1 |
| Missing price history | DONE | 400 | — |
| Missing fundamentals | DONE | silent None | note in UI | P3 |
| Missing FRED data | PARTIAL | features None; scenario reports `unavailable` | P2 |
| Network / Yahoo failure | DONE | retries ×3 × 2 hosts, logged to fetch_log | — |
| SEC failure | PARTIAL | no retry on 429/503 | P2 |
| FRED failure | PARTIAL | no retry | P2 |
| Model training failure | DONE | job status failed + message | — |
| Corrupted database | MISSING | no integrity check → `PRAGMA integrity_check` on start; clear message | P2 |
| Missing Shaffer plugin | DONE | falls back | — |
| Invalid trade | PARTIAL | 400s; but an unknown currency makes the whole portfolio page 400 | P1 |
| Insufficient sample | DONE | None / "insufficient" with a reason | — |
| No uncaught errors reach the UI | PARTIAL | page-level catch; generic 500s possible | P1 |

## 77. Testing (current: 130 FinSim2 tests)
| Area | Status | P |
|---|---|---|
| Portfolio accounting | PARTIAL (NAV identity, weights, amount sizing, quantity after sell) | P0 |
| Realized P&L | MISSING | P0 |
| Corporate actions | MISSING (only data re-fetch) | P0 |
| Foreign currency | MISSING (ledger FX untested) | P0 |
| Short positions | MISSING | P1 |
| Benchmark calculations | MISSING | P1 |
| Attribution | MISSING (no engine) | P1 |
| Model calibration | MISSING | P1 |
| Feature leakage | PARTIAL (cutoff, standardize, preprocessing, SEC TTM); features.py and ML purge untested | P0 |
| FRED release timing | DONE (test_available_on) | — |
| SEC filing timing | DONE (test_ttm_point_in_time) | — |
| Multiple testing | DONE | — |
| Effective sample size | PARTIAL | P2 |
| Model versioning | PARTIAL | P1 |
| Prediction grading | PARTIAL (store only; `score_matured`/`report` untested) | P1 |
| Scenario engine | PARTIAL | P2 |
| Stress tests | MISSING (no engine) | P1 |
| Backtest lookahead | MISSING | P0 |

## 78. Reproducibility
| Field | Status | Notes | P |
|---|---|---|---|
| Software version | PARTIAL | "fs2-ml-1" only | → git hash / package version | P2 |
| Dataset cutoff | PARTIAL | data_version only inside the overwritten `ml:` blob; backtests have none | P1 |
| Asset | DONE | — | — |
| Features | DONE | model_runs.features | — |
| Model parameters | **BUG** | defaults recorded, not the params actually used | P1 |
| Random seed | PARTIAL | implicit | P2 |
| Training range | PARTIAL | `train_end` recorded wrongly | P1 |
| Test range | DONE | — | — |

## 79. Audit trail
MISSING. There is no actions log; transactions have no created_at; DELETE is a hard delete with no log and no
re-validation. → An `audit_log` table (action, entity, before/after JSON, at); soft-delete transactions
(`voided_at`); log refreshes, trainings, settings changes. P1.

## 80. Documentation
| Topic | Status | P |
|---|---|---|
| Architecture | DONE (ARCHITECTURE.md) | — |
| Data sources | DONE | — |
| Equation library | PARTIAL (ID ranges) | P3 |
| Scoring algorithm | DONE | — |
| ML pipeline | DONE | — |
| Leakage protections | DONE, but overstated: it doesn't mention the FRED vintage, backtest direction, ML z or split-EPS issues → correct it | P0 |
| Portfolio accounting | PARTIAL (docstring only; "fills, not orders" never stated) | P1 |
| Database | DONE | — |
| API / routes | MISSING | P2 |
| Testing | PARTIAL | P3 |
| Shaffer Score interface | DONE | — |
| Data refresh process | DONE | — |

---

## 81. Product quality: explicit answers

- **Portfolio: can FinSim2 explain every dollar of value and P&L?** No. Dividends and distributions are missing, splits
  after entry corrupt positions, BUY can overdraw cash with no financing charge, back-dated or deleted transactions
  can create phantom cash, and USD-base FX pairs show no P&L. There is no attribution, daily P&L or TWR/IRR.
- **Quant: can it identify which signals historically mattered without leakage?** Largely yes for the evidence
  matrix: expanding z-scores, effective-sample p-values, BH q-values and a point-in-time score history. Not fully:
  FRED revisions leak into macro features and regimes, EPS is not split-adjusted, and the expected-return mapping
  is in-sample.
- **ML: can it distinguish real improvement from noise?** Partly. Purged walk-forward, a robust (mean − 0.5·sd)
  ranking and a zero score when the out-of-sample IC ≤ 0 are good. There are no simple baselines, no untouched
  holdout, no calibration, the ensemble weights leak slightly, and importance is measured on the data that chose
  the weights.
- **Risk: can it tell me where the portfolio can lose money and why?** Partly. VaR/ES, Euler risk contributions,
  factor betas, scenarios with conditional completion, historical analogues and Monte Carlo exist. Stress tests,
  concentration and liquidity metrics, historical crisis replays and risk from the real NAV are missing, and bond
  convexity is understated.
- **Research: can I understand an asset from a single page?** Mostly for the evidence (scores by horizon, why,
  signals today, regime). Valuation, fundamentals, risk, factor and macro summaries require the Analytics tabs.
- **Explainability: can every score be traced to its inputs?** The Quant Score: yes (weights × z per signal, with IC,
  p, q, n_eff). The ML Score: yes per forecast (Saabas/linear contributions). The Shaffer Score: no (single float).
- **History: can every historical score be reproduced using only information available at the time?** Not yet.
  The history uses point-in-time weights, but omits the live regime blend, relies on revised FRED data and
  un-split-adjusted EPS, and model runs record the wrong parameters.
- **Performance: is normal daily use fast?** Yes for cached pages (about 1 s). An uncached asset takes about 18 s.
  ML training takes 30–60 s per asset. The portfolio replays the full ledger several times per request, which will
  slow with many transactions.
- **Reliability: does bad or missing data fail safely?** Mostly. Retries, fetch log, None propagation and
  page-level error cards exist. Exceptions: a bad ticker gives HTTP 500, an unknown currency breaks the whole
  portfolio page, there is no corrupted-DB handling and no per-asset stale detection.
- **UX: can a non-developer use every major function without the terminal?** Nearly. Everything except the first
  install and launch (`python3 -m finsim2 open`) is in the UI, but fees, portfolio selection, CSV import/export and
  pooled class-level ML have no UI.

---

## Completion by area (DONE = 1, PARTIAL = ½, over the audited items; N/A excluded)

| Area | Score | Sections counted |
|---|---|---|
| Portfolio Accounting | **35%** | 1, 3, 4, 63–68 |
| Trading | **25%** | 2 (order types N/A by design) |
| Data | **50%** | 5–9, 32–33, 52–54, 76 |
| Quant | **60%** | 10, 16–19, 30–31, 57, 59–61 |
| ML | **50%** | 20–29, 35, 46, 55–56, 58, 62, 78 |
| Risk | **45%** | 11–15, 36–40, 47 |
| Backtesting | **55%** | 34–35 |
| UI | **50%** | 41–51, 69–75 |
| Testing | **35%** | 77 |
| Documentation | **70%** | 80 |

These scores measure coverage, not correctness. Several DONE items sit on top of P0 bugs.

---

## B. P0: must fix before using seriously

1. **BUY is not checked against cash.** Cash goes negative silently (unfunded leverage with no interest).
   [reproduced] (P:72-96)
2. **Back-dated and deleted transactions are not re-validated.** A truncated SELL credits its full proceeds
   (phantom cash), and `holdings` and `nav_history` disagree, so the headline NAV ≠ the NAV chart. [reproduced]
   (P:109-182)
3. **Dividends and ETF/bond-fund distributions are never credited.** NAV and P&L understate total return; risk uses
   adj_close while valuation uses close. (P:152,350; yahoo.py discards the events)
4. **Stock splits are not applied to transactions.** Positions are revalued at post-split prices with pre-split
   quantities.
5. **SEC EPS is not split-adjusted.** Earnings yield, P/E-relative and EPS growth are wrong across splits (NVDA, AAPL,
   AMZN, GOOGL, TSLA, WMT, AVGO).
6. **USD-base FX pairs (USDJPY, USDCAD, USDCHF, USDCNY, USDMXN, USDINR) are valued as a constant.** They show zero
   P&L. (P:28-37)
7. **Backtest lookahead:**
   (a) signal direction comes from the full-sample IC;
   (b) ML out-of-sample forecasts are z-scored with the full-sample mean/sd;
   (c) trades execute at the same close as the signal.
   All three are verified. (S:323-324, R:334-336, BT:61-90)
8. **FRED latest-vintage data leaks revisions backward** into macro features and regimes (NFCI worst; also UNRATE,
   CPI, WALCL). The UNRATE lag can precede its release.
9. **The Quant Score's expected return and "± error" are in-sample**, and the point-in-time score history (used for
   out-of-sample accuracy, confidence and backtests) omits the live score's regime blend. The number that is
   validated is not the number that is shown.
10. **Dashboard risk tile mislabel:** "Volatility (1-day VaR 95%)" shows annual volatility.
11. **Documentation overstates leakage protection:** ARCHITECTURE/README claim point-in-time everywhere. Correct it
    until items 5, 7, 8 and 9 are fixed.
12. **Tests are missing for everything above:** realized P&L, FX in the ledger, corporate actions, backtest
    lookahead, ML purge.

## C. P1: major features still missing (highest value first)

1. **Score calibration.** Score bucket → realised return, hit rate and n per asset × horizon. Flag or damp scores
   whose out-of-sample IC ≤ 0 (TLT is inverted today). Record Quant forecasts in the prediction ledger.
2. **Portfolio performance.** Daily P&L, chained TWR, IRR, period returns (1D…10Y, YTD, inception), a
   user-selectable benchmark (SPY default) with excess return, TE, IR and beta.
3. **Attribution: "Why Did My Portfolio Move Today?"** By asset, sector, class, country and currency, plus factor
   attribution and the residual.
4. **ML baselines.** Zero, historical mean, previous return, momentum, mean reversion, random/permutation. The ML
   score is non-zero only when the ensemble beats the best baseline out of sample. Add out-of-sample R² and Brier.
5. **Untouched final holdout** and research/validation/confirmation separation (§62). Purge the ensemble weights.
6. **Stress tests** (correlations → 1, vol ×2, largest −50%, top-3 −30%, rates +200bp, credit +300bp) and
   **historical crisis replays** (2000, 2008, Mar 2020, 2022) using actual moves.
7. **Multi-currency cash, FX P&L separated from price P&L,** and a full currency map for added symbols.
8. **Covariance shrinkage** (Ledoit-Wolf) and asset-class priors for expected returns in the optimiser.
9. **Data freshness and staleness warnings** (prices per asset, FRED, SEC, model training).
10. **Reproducibility.** Record the actual params, data_version, train range and code version for model runs and
    backtests. Version `ml:` results by data and mark them stale.
11. **Model decay classes** (HEALTHY/WEAKENING/DECAYING/INSUFFICIENT) from the graded ledger. **Signal decay table**
    (IC over 10Y/5Y/2Y/1Y).
12. **Prediction intervals** (quantile regression or empirical out-of-sample residual quantiles) for both scores.
13. **Wider multiple-testing correction** (BH across signal × horizon per asset). Standard errors and CIs on IC.
14. **Shaffer Score schema v2.** Per-horizon values, confidence and explanation; shown on the Watchlist and
    Portfolio; backtestable history.
15. **Short positions** (signed cost basis, short MV and P&L, exposure) with leverage ratios and concentration
    warnings.
16. **CSV import/export** of transactions, positions, NAV history, backtests and model results. **Audit trail** with
    soft-delete.
17. **Macro dashboard** (curve, spreads, inflation, labour, liquidity, regime history) plus Fed funds.
18. **Units and small data bugs:** cents-quoted grains, convexity units, a bad ticker returning 500, an unknown
    currency breaking the portfolio page, the fee input in the UI.
19. **Volatility target model** (future realised vol) and a direction model with calibration.

## D. P2: advanced improvements

- Expanded fundamentals: margins, FCF, debt, ROE/ROA/ROIC, EV multiples, valuation vs sector.
- Liquidity analytics: ADV, days to liquidate, spread proxy.
- Rolling factor exposures, factor contribution to risk including residual, HAC standard errors.
- Probabilistic regimes (logistic/HMM), transition dates, durations, regime-specific ML performance.
- Cross-asset: lead/lag, cross-correlation, regime-dependent correlation, a macro-relationship block per asset.
- Black-Litterman, a resampled frontier, sensitivity of weights, inverse-vol / target-vol / sector constraints.
- Monte Carlo with Student-t, GARCH-filtered and regime-conditioned options; a user target probability.
- Futures notional and multipliers; the front/next spread for term structure and roll yield; option positions.
- Portfolio selector and A-vs-B comparison; security comparison; rebalancing trade lists; income analytics.
- Thesis tracker and decision journal; alerts engine; saved research.
- Automatic monthly retraining; feature and sign stability across folds; per-model backtests after costs.
- Causal-language cleanup (§61); tooltips with definitions; formulas for features.
- NAV snapshot caching; kv pruning; light-row cache for Markets.
- SEC/FRED retries; a DB integrity check; the scheduler time window.

## E. P3: nice-to-have

- Candlestick, volume, drawdown and yield-curve charts; synchronised ranges.
- Favorites, saved screens, table search.
- Extra trees, PCA regression, optional XGBoost/LightGBM when installed.
- Tax lots (FIFO/LIFO/specific); Kelly sizing with an instability warning.
- Treynor, es99 display, skew/kurtosis for the portfolio.
- Equation "why it matters / common mistakes / example" text.
- One-click close position; a "fills, not orders" note in the UI; Pearson IC next to rank IC.
- Holiday-aware due dates; more crypto; preferreds; currency ETFs.

## F. Recommended next 10 commits (ordered; each testable on its own)

1. **ledger: one validated replay; no overdraft; no phantom cash**
   - Scope:
     - a single `_replay()` used by `holdings` and `nav_history`;
     - validate the whole ledger on insert and delete: reject an overdraft (a BUY beyond cash) and any SELL that
       makes the quantity negative at any later date;
     - SELL credits only the quantity actually sold;
     - `allow_margin` stays off by default.
   - Files: finsim2/engine/portfolio.py, finsim2/server.py, finsim2/static/app.js (txModal error text).
   - Tests:
     - BUY beyond cash → 400;
     - a back-dated SELL that breaks a later SELL → 400;
     - deleting a BUY that later SELLs depend on → 400;
     - holdings and nav_history agree on every date;
     - realized P&L on partial sells and re-entry.
   - Done when the two reproductions in this audit fail with clear messages, and NAV(t) from nav_history equals
     cash + MV from holdings(t) for random ledgers.

2. **data: store split and dividend events; ledger applies them**
   - Scope:
     - parse Yahoo `events.splits` and `events.dividends` into a new `corporate_actions` table;
     - the replay scales quantity and price across splits and credits dividends × quantity on the ex-date (in the
       asset currency → USD);
     - valuation stays on close, with income now in cash.
   - Files: finsim2/data/yahoo.py, store.py, refresh.py, finsim2/engine/portfolio.py.
   - Tests:
     - parse fixtures with a 4:1 split and quarterly dividends;
     - a position bought before a split keeps its value;
     - total-return NAV ≈ quantity × adj_close path.
   - Done when an NVDA 2024 split fixture shows no false loss and SPY dividends appear as cash income.

3. **fundamentals: split-consistent EPS**
   - Scope: divide as-filed EPS by the cumulative split factor after its period end (from commit 2) before TTM and
     ratios.
   - Files: finsim2/data/sec.py, finsim2/engine/features.py.
   - Tests: a synthetic 10:1 split leaves earnings_yield continuous; pe_rel_5y has no jump.
   - Done when NVDA's earnings yield has no step at 2021-07 or 2024-06.

4. **fx: correct USD-base pair valuation and FX P&L split**
   - Scope:
     - value FX-pair holdings as financed positions;
     - report the price and FX components of unrealized P&L separately;
     - map every Yahoo currency (with GBp/pence scaling).
   - Files: finsim2/engine/portfolio.py, universe.py.
   - Tests:
     - long USDJPY from 150 to 160 shows +6.25% in USD;
     - EURUSD unchanged behaviour;
     - an unknown currency gives a clear per-position warning, not a page 400.
   - Done when all 10 pairs' P&L match hand calculations.

5. **backtest: remove lookahead; net-of-cost trade stats; Calmar**
   - Scope:
     - signal direction from an expanding (point-in-time) IC sign;
     - ML OOS z with expanding mean/sd;
     - a default 1-session execution lag (configurable);
     - per-trade returns net of costs;
     - add Calmar;
     - label the benchmark as the asset's own buy & hold.
   - Files: finsim2/engine/backtest.py, research.py, server.py, app.js.
   - Tests:
     - a signal that equals tomorrow's return must NOT earn it with lag 1;
     - flipping future data does not change past direction;
     - win rate is net of costs.
   - Done when the lookahead tests pass and existing backtests still run.

6. **macro: first-release vintages and correct release lags**
   - Scope:
     - with FRED_API_KEY, fetch `output_type=4` (initial release) into a vintage-aware column;
     - without the key, drop NFCI from features and regimes and badge macro features "revised data";
     - UNRATE and M2 lags from release timing.
   - Files: finsim2/data/fred.py, store.py (column), universe.py, features.py, regimes.py.
   - Tests: a mocked ALFRED response keeps the first print; no revision appears before its real-time date.
   - Done when regimes and features built with the key are unchanged by later revisions in fixtures.

7. **quant: one scoring function for live and history; out-of-sample expected return**
   - Scope:
     - `score_history` uses the same `_weight` (regime blend) and strength as the live score;
     - expected return and error come from regressing realised returns on the *point-in-time* composite
       (out-of-sample residuals);
     - pass the agreement term to confidence;
     - record quant forecasts in the prediction ledger.
   - Files: finsim2/engine/scores.py, research.py, tracking.py.
   - Tests:
     - live score on the last date == history's last value (same inputs);
     - expected-return RMSE uses only rows before each refit;
     - quant predictions are recorded and graded.
   - Done when the displayed score, validated score and backtested score are the same series.

8. **quant: calibration table and evidence gate**
   - Scope:
     - per asset × horizon, buckets of the point-in-time score (quintiles and fixed bands ±20/±50) → realised
       mean, hit rate and n with CIs;
     - a monotonicity statistic;
     - scores whose out-of-sample IC ≤ 0 (or non-monotone calibration) are shown dimmed with "not validated"
       and excluded from the primary horizon.
   - Files: research.py, scores.py, server.py (route), app.js (Quant Lab "Calibration" tab + asset strip).
   - Tests: a planted monotone signal gives increasing buckets; an inverted signal is gated.
   - Done when TLT's current scores show "not validated" and SPY's calibration table renders.

9. **portfolio: performance block and benchmark (daily P&L, TWR, IRR, periods, excess/TE/IR)**
   - Scope:
     - chained flow-adjusted TWR; XIRR; 1D/1W/1M/3M/YTD/1Y/3Y/5Y/inception returns;
     - benchmark selectable in Settings (SPY default; blends allowed);
     - dashboard tiles;
     - fix the VaR tile label.
   - Files: finsim2/engine/portfolio.py, server.py, app.js.
   - Tests:
     - TWR is unaffected by the size and timing of deposits (a known example);
     - XIRR on a known cash-flow set;
     - excess = portfolio − benchmark;
     - TE/IR against hand values.
   - Done when the dashboard shows daily P&L, TWR vs benchmark, and numbers match the test fixtures.

10. **portfolio: "Why Did My Portfolio Move Today?" attribution**
    - Scope:
      - contributions w_{i,t−1}·r_{i,t} for 1D/1W/1M/YTD, grouped by asset, sector, class, country and currency;
      - factor attribution β·f plus the residual, using the weekly betas;
      - top contributors and detractors on the dashboard.
    - Files: finsim2/engine/portfolio.py (or a new attribution.py), server.py, app.js.
    - Tests: contributions sum to the TWR period return (within fees/flows); factor + residual = portfolio return.
    - Done when the dashboard shows today's top contributors and detractors, and the sums reconcile to the
      performance block.

After these ten: ML baselines + holdout (C4–C5), stress tests + crisis replays (C6), covariance shrinkage (C8),
freshness warnings (C9), reproducibility fields (C10).
