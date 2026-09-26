# FinSim2 — Full Shaffer System Report

Checked against the code on branch `claude/finance-simulation-game-y0nkv0` (data to 2026-09-24). Status words:
**DONE** (implemented, tested and checked against real data), **PARTIAL**, **NOT DONE**, **BLOCKED** (cannot be done
with the data this environment can reach). A feature is not DONE because a screen shows it.

Paths are relative to `finsim/finsim2/`. Detailed evidence lives in `SHAFFER_AUDIT.md` (scores and ML, 133 assets),
`HEDGE_AUDIT.md` (hedging, 110 cases × 3 horizons), `SHAFFER_HEDGE.md` (hedge design), `PRODUCT_REGISTRY.md` (61
product types) and `AUDIT.md` (the 81-point product audit with its "Fixed since" table). Screenshots `01`–`32` were
taken from a live server against the real research store with a demo book (NAV $1.62M).

Commit key (details in §75): `be2af0f` ledger/corporate actions/FX · `2686ef2` backtests · `ae4b0f3` macro vintages ·
`d74cebf` Shaffer v2 · `03657f8` ML v2 · `d0c7e6b` WTI · `894b5fa` Shaffer audit · `08f7477` hedge engine · `ac3d57f`
ledger derivatives · `67bb0e6` hedge service/UI/net scores · `273b437` systematic objective · `1025ec7` docs ·
`5325481` over-hedging fix · `82904a7` input validation.

## Product direction — 2026-09-25: FinSim2 is Portfolio Manager Career Mode

FinSim2 = research the market → Shaffer Score → trade → optionally Shaffer Hedge → manage → review. The ML Lab is the
laboratory that evaluates and refines the two Shaffer systems; it does not trade or recommend (`ML_LAB.md`).

| Item | Status | Where / evidence |
|---|---|---|
| Shaffer Score everywhere, horizon-specific | DONE | Markets (calibrated and agreement were being dropped — fixed), Watchlist (calibrated, confidence, Trade button), Portfolio positions (horizon selector), ticket (supporting / opposing factors now follow the selected horizon), Asset Research, Analytics |
| Trade only / Trade + Shaffer Hedge | DONE | ticket; any hedge design can be booked instead of the engine package |
| Profit-aware hedging (risk removed − λ·profit given up − cost) | DONE | `hedge/designs.py`, ticket and Analytics → Shaffer Hedge; market-prior drifts, Shaffer evidence only where supported, thesis-disagreement flag; bond / FX / commodity carry not modelled (stated) |
| Marketplace concise vs Analytics deep | DONE | Analytics gains the Shaffer Hedge tab (risks, candidates, cost, basis, tail, before/after, designs) |
| Historical research dataset | DONE | `lab_records`: 819,392 point-in-time records, 157 assets, 6 horizons |
| Live day-over-day learning | DONE | ledger: Shaffer, ML return / volatility / drawdown, hedge recommendations (daily P&L paths kept), challengers in live shadow |
| Hierarchical weights (global → class → sector → industry → asset, shrinkage) | DONE | `engine/lab.py`; four variants; discovery / confirmation / walk-forward |
| Signal-level re-weighting inside the Shaffer equation (ω, W·A·H, γ strengths, interactions) | DONE (research) | `engine/weights.py`; four unseen eras + 2018 split, naive baselines, 9 score bands, specialisation by depth; results in `SHAFFER_WEIGHT_RESEARCH.md`. Promotion marks the registry; applying promoted weights to the live score is not wired yet |
| Shaffer Alpha vs Shaffer Directional (two targets, PIT base priors) | DONE (research) | `engine/directional.py`, ML Lab → Shaffer Alpha / Shaffer Directional, `SHAFFER_DIRECTIONAL_RESEARCH.md`: alpha ranks at 1D–1W; directional ≈ base rate, Shaffer adds to the prior only at 1D; production unchanged |
| New information (not more reweighting), judged against a frozen benchmark | DONE (research) | `engine/newinfo.py`, `data/finra.py`, ML Lab → New information, `NEW_INFORMATION_RESEARCH.md`, `NEW_DATA_SOURCES.md`: benchmark `benchmark-2.1-2026-09-25` frozen and verified; 7 families built, 9 blocked by data access; 4 of 102 tests survive FDR; breadth → Alpha 1W (via a beta interaction) and hedge volatility 1W (−7.3% error), earnings events → hedge volatility 1W (−0.5%); no Directional gain; FINRA short volume LIMITED HISTORY (not short interest); passing tests recorded daily in live shadow; production unchanged |
| Breadth volatility → Shaffer Hedge outcomes (complete hedge chain replayed point in time) | DONE (research) | `hedge/volhedge.py`, ML Lab → Hedge research, `BREADTH_HEDGE_RESEARCH.md`: breadth improves the market-volatility forecast (1W −15% squared log error vs the same model without breadth, 1M −8%; not at 3M) but not realised hedge utility — 0 of 50 objective × horizon tests survive FDR, no cell passes G1–G4; positive point estimates come ~91% from the new volatility architecture, not breadth; hedge-2 unchanged, no live shadow |
| Shaffer vNext: three separate programs (Alpha 1D–12M, Directional 1D/1W, Hedge: risk / sizing / product / Alpha link) | DONE (research) | `engine/alphanext.py`, `engine/dirnext.py`, `hedge/hedgenext.py`, `data/sec_extra.py`, ML Lab → Shaffer Alpha / Directional / Hedge areas, `SHAFFER_ALPHA_VNEXT.md`, `SHAFFER_DIRECTIONAL_VNEXT.md`, `SHAFFER_HEDGE_VNEXT.md`, `SHAFFER_VNEXT_SUMMARY.md`: no challenger passes every gate; Alpha's 1M–6M point estimates are positive but not significant over production; Directional does not beat the PIT prior; 31 hedge sizing cells survive FDR (larger hedges help at λ ≤ 2) but all fail the fixed cost/basis guard H4; production unchanged |
| Formula versioning | DONE | `formula:registry`: shaffer-2.1, hedge-2, four Shaffer challengers, hedge-2-sizing-exp |
| Promotion process (no automatic change) | DONE | gates G1–G3 + explicit user action; promoted hedge sizing is applied by the live engine; a promoted Shaffer weighting becomes a new score VERSION |
| Hedge learning (H_ML = m·H_raw) | DONE | sizing study: 72 of 174 multiples confirmed; challenger in live shadow |
| ML Lab page | DONE | production models, historical performance, signal research, weight research, challengers & promotion, hedge research, live learning, version comparison; per-asset ML forecasts kept as an independent benchmark |

Result: no Shaffer weighting challenger passes discovery at any horizon, and more specialisation is worse out of sample
(details in `ML_LAB.md`). The hedge sizing study finds real, confirmed biases (option hedges ≥15% oversized; linear
equity hedges correctly sized). The signal-level re-weighting (`SHAFFER_WEIGHT_RESEARCH.md`) finds no challenger that passes the historical gates at any horizon; production's direction is ~51% right against naive baselines of 53–72%, bullish scores are informative and bearish ones are not, and production's cross-sectional ranking is significant at 1D–1M.

## Research phase 2 — 2026-09-25 (what improved, with the out-of-sample numbers)

Rules kept: no weight retuned, no sign changed, no threshold loosened, the admission rule unchanged, nothing forced into
production, Shaffer and ML never combined. Audits rerun on data to 2026-09-24: `SHAFFER_AUDIT.md` (157 assets, ML on 21,
41 min) and `HEDGE_AUDIT.md` (110 cases × 1W/1M/3M). Every "t" below is date-clustered unless it says otherwise.

**Corrections made during this phase (found by the checks, fixed before anything was used):**
- The hedge-ML paired t counted every window as independent, but a group pools many books and hedges on the same dates.
  It is now clustered by start date (the same fix the Shaffer admission test got earlier). The original variance-only
  layer's one verified group (single-name hedges, 3M) does not survive: **0 groups verified**.
- The hedge-ML test had no "constant resizing" baseline, so a model that only rediscovered a fixed sizing bias looked
  like skill. The baseline is added and every model must now beat it.
- VaR/ES verification read a bootstrap p of exactly 0.00 as a failure (`p or 1`). Fixed.
- The ML beta-change model beat "no change" but not the textbook Blume adjustment, which is now a baseline.
- The Credit, Cross-Asset and Macro families' "positive records" (Credit incremental t +8.4 at 1W) come from ONE
  asset: BIL, a T-bill fund whose forward return is essentially the known bill yield. The audit now labels any family
  with fewer than 5 assets "not evidence".
- UI: the ML Lab crashed on a variable used before its definition (caught by the screenshot run); the risk forecasts
  in the forecast record were shown as returns ("+77.5%" for a drawdown probability). Both fixed.

### The ten questions

**1. Did any new information family pass admission? — No.** Nine candidate families (the seven plus Earnings Surprise
and Breadth). Incremental IC, date-clustered t before 2018 → from 2018:

| Family | 1W | 1M | 3M |
|---|---|---|---|
| Carry | +0.2 → **+3.8** | +1.5 → **+3.5** | +0.7 → +1.9 |
| Earnings Surprise (SUE, 43 stocks) | −1.3 → −1.1 | +0.2 → −1.9 | −0.5 → −1.8 |
| Breadth | — → +0.1 | +0.5 → +0.2 | +1.7 → −0.6 |
| Term Structure | −2.2 → −1.1 | −1.9 → −0.5 | — |
| Yield Curve | −0.8 → −0.8 | +0.1 → −1.0 | +1.1 → −1.4 |

Carry is again strong only after 2018 and fails the pre-2018 discovery test, so it stays in shadow as the rule requires.
Not buildable point in time from allowed sources: CFTC positioning (the CFTC is not an allowed domain), short interest
(FINRA's short-sale volume files start in 2019, too late for a pre-2018 test), consensus estimates, revisions and
forward valuation (the Alpha Vantage free tier allows 25 requests a day, already used up), fund flows, option surfaces
and futures curves.

**2. Did Shaffer OOS IC improve? — No.** Pooled production IC with date-clustered t: 1W +0.012 (t 1.2), 1M +0.007
(0.4), 3M −0.003 (0.1), 6M +0.013 (0.3), 12M −0.009 (0.1). This is identical to the previous audit; nothing entered
production. Methodology variants tested in shadow (§27b):
- dropping the economic prior H: neutral (1M incremental t −0.2 → +0.6);
- a strict validation multiplier V' = clip(t/2, 0, 1): clearly worse (incremental t from 2018 −4.8 to −6.2 at 1D–1M; score IC from
  2018 −0.008 → −0.025 at 1M).

Neither is adopted. Health check: the last three years' clustered IC is negative at 3M (−0.21, t −2.6) and 6M
(−0.28, t −2.3). Every Shaffer horizon reads **NO VERIFIED EDGE** (12M+: **INSUFFICIENT DATA**).

**3. Did score monotonicity improve? — Yes, from a bug fix, not from new information.** Mean calibration monotonicity
across assets, before → after fixing the calibration bin-merge bug: 1W 0.136 → 0.137, 1M 0.046 → 0.109, 3M −0.017 →
0.037, 6M 0.060 → 0.114, 12M −0.004 → 0.011. It is still weak.

**4. Did any improvement persist after 2018? — No.** The production score's IC from 2018: 1W −0.007, 1M −0.008,
3M −0.045, 6M −0.023, 12M −0.040. The only family that looks better after 2018 (Carry) did not qualify before it.
V_f audit (item 8): families with a negative record keep V ≈ 0.40–0.45 from 2018 (for example Rates at 1W: t −2.7,
V 0.45, influence 5.0% → 7.8%). They are "not shrinking enough", but the stricter-V variant that would shrink them
performed worse out of sample (Q2), so the per-asset validation record is too noisy to zero families on. Nothing was
changed by hand.

**5. Did ML risk prediction improve? — Yes, risk is where ML has something; returns: no.** Assets where the model
beat every naive baseline out of sample (and in both halves of the OOS period), out of 20–21:

| Horizon | Return | Direction | Volatility | Drawdown prob. | Tail loss | Beta change (incl. Blume) |
|---|---|---|---|---|---|---|
| 1W | 2 | 0 | 9 (3) | **19 (14)** | 8 (1) | — |
| 1M | 0 | 0 | 11 (2) | 10 (7) | 11 (2) | 5 (2) |
| 3M | 0 | 0 | 13 (3) | 6 (2) | 9 (4) | 5 (2) |
| 6M | 0 | 0 | 13 (7) | 4 (2) | 11 (8) | 7 (2) |
| 12M | 0 | 1 | **14 (9)** | 4 (1) | **13 (9)** | 6 (3) |

Median error improvement over the best baseline: volatility +18% at 12M, tail loss +7% at 12M, drawdown Brier +5% at
1W; return R² vs the historical mean is negative at every horizon. Health: ML volatility and tail-loss models are
HEALTHY at 6M–12M, drawdown at 1W/1M/6M; the ML return model is NO VERIFIED EDGE everywhere.

**6. Did hedge ML beat static hedge rules in more groups? — Barely, and mostly by learning a constant.** With
objective-specific targets and the richer features, verified against the static rule, a constant resizing and
min-variance, all date-clustered, the following group × metric tests passed out of 33 each: variance 5, factor exposure
2 (of 27), downside 5, drawdown 7, VaR 1, ES 5. That is 25 of 192, where chance alone gives about 4–5. The richer
features barely helped: 5 variance passes vs 3 with the original six features.

Where a model passes, its mean adjustment equals the constant's (for example equity options at 1W: −0.22 vs −0.22),
and its gain over the constant is a fraction of a percent. The real finding is a **sizing bias in the static rule**:
option hedges sized at full delta are about 11% too large for variance (the constant learned ×0.88–0.91, worth
+15–31% variance reduction vs the static rule), crypto ETF hedges ×0.85 (+41%), FX forwards ×0.85–0.90 (+4%), credit
×0.93–0.95 (+8%). The bias is reported, not applied to the static rule. Health: only the drawdown metric clears the
20%-of-groups bar.

**7. Did hedge costs become more realistic? — Partly.**
- Short proceeds now earn the configured rate, and short financing enters the net score.
- Option spreads and liquidity come from market quotes when a chain is imported.
- No chain source is reachable (Cboe is blocked), so option costs are still modelled.
- Historical bid/ask and liquidity are not features of the hedge ML: they are not available point in time.

**8. Did option results change under improved volatility assumptions? — Not measurable yet.** The quote → surface →
flat-vol pricing path and the confidence penalty (M = 1.0 / 0.9 / 0.8) are in place, and every option shown is labelled
MODEL-PRICED — FLAT VOLATILITY ASSUMPTION. With no chain data there is no surface to test, so the walk-forward option
results are unchanged.

**9. Did crisis replay reveal hidden risk? — Yes.** On the demo book (NAV $1.62M):

| Crisis | Factor replay | Own-history replay | Crisis 1-day VaR 95% |
|---|---|---|---|
| Dot-com bust | −$470k (−29%) | −$325k | $20.9k (normal $15.6k) |
| 2008 | −$178k | −$369k | — |
| March 2020 | −$208k | — | $75k (4.8× normal) |
| 2022 inflation / rate shock | −$297k | — | — |

The 2008 gap between the factor replay and the book's own history shows what the factor model misses.

**10. Did attribution fully reconcile daily P&L? — Yes.** On the real ledger's last 60 sessions: 0 days unreconciled,
maximum residual $0, and the "Trading & other" remainder under $1 on every day (none of those days had trades).

### Model health (new page, `/fs2/health`)
Statuses are pre-declared (HEALTHY / WEAKENING / DECAYING / NO VERIFIED EDGE / INSUFFICIENT DATA). As of today:
16 / 11 / 0 / 15 / 4.
- Shaffer: NO VERIFIED EDGE at 1W–6M.
- ML return: NO VERIFIED EDGE.
- ML volatility and tail loss: WEAKENING at short horizons, HEALTHY at 6M–12M.
- ML drawdown: HEALTHY at 1W/1M.
- Shaffer Hedge static rule: HEALTHY for equity beta (median variance reduction 44%, tail 72%), rates (70%), credit
  (95%) and crypto (48%). Single-name 100% is trivial: it shorts the stock itself.
- Currency and commodity hedges RAISE variance (−20%, −30%) but cut tail loss, so they are labelled "tail only".

The live ledger now also records the ML volatility and drawdown forecasts and grades them against realised volatility
and realised drawdowns. The first grades arrive when the 1W horizon passes.

**Bottom line.** Where the evidence is:
- Shaffer Hedge: the static hedge rule works.
- ML: helps predict risk (volatility, tail loss, drawdown probability), not returns.
- Shaffer Score: structured evidence with no verified edge, and it says so.

## Update — 2026-09-25 (after review)

Governing rule adopted: **do not optimise Shaffer v2 further; add independent economic information in shadow, keep
the out-of-sample validation untouched, and admit a family only if it improves incremental out-of-sample IC and
calibration.**

- **P0: continuous futures series** (GOLD, WTI, NATGAS … and user-added `=F` symbols) can no longer be bought or
  shorted — the price jumps to the next contract at each roll. The refusal names the fund that rolls the contracts
  (USO, UNG, GLD, SLV, CPER, DBA, DBC). Old positions replay, are flagged, and can be sold.
- **P0: option prices** carry "MODEL-PRICED — FLAT VOLATILITY ASSUMPTION" on every leg, candidate, ticket and holding;
  crash analyses warn that OTM puts are probably too cheap. Futures/forwards: "MODEL-PRICED — FAIR VALUE, NOT A QUOTE".
- **Hedge objectives by purpose**: Variance reduction · Beta reduction · Tail protection · Drawdown protection · Factor
  neutralization. Tail objectives (crash, ES, VaR) now judge candidates on walk-forward tail-loss reduction, drawdown on
  drawdown reduction. Every candidate shows its historical variance cut and tail cut and a type — variance hedge, tail
  hedge, both, or "tail hedge (adds variance)".
- **Shaffer Score = structured quantitative evidence score** — what the evidence says, not a forecast — on every screen.
- **Seven candidate families in shadow** (Carry, Yield Curve, Term Structure, Inflation, FX, Commodity, Optionality;
  21 point-in-time signals, `engine/candidates.py`). Production scores are unchanged — checked identical on 7 real
  assets at every horizon and by test. Result of the 15–25-year walk-forward (`SHAFFER_AUDIT.md` §27): **none
  admitted.** Carry is the only family with a consistently positive incremental IC (all horizons; strong from 2018,
  t 3.5–3.8 at 1W/1M, positive in 75–79% of assets) but it was not significant before 2018 (t 0.2–1.5), so it stays
  in shadow; its recent strength coincides with one macro episode (the 2022+ rate shock). Inflation and FX signals
  hurt out of sample. Not buildable from reachable data: skew, futures curves beyond the front month, foreign
  inflation/growth differentials, commodity inventories.
- **Validation hardened**: a first admission run pooled correlated assets as independent (Stouffer) and admitted
  Carry at 4 horizons; that statistic overstates significance, so the test now pools evidence by date (and splits all
  assets at one date, 2018-01-01). The production score's IC table now reports the date-clustered t next to Stouffer.
- **Correction to §72 and the acceptance answer on score strength**: with date clustering the production Shaffer
  Score's out-of-sample IC is **not significant at any horizon** (157 assets): 1D +0.005 (t 0.2), 1W +0.012 (t 1.2 —
  Stouffer had said 4.7), 1M +0.007 (0.4), 3M −0.003 (0.1), 6M +0.013 (0.3), 12M −0.009 (0.1). The earlier "only 1W
  is significant" relied on treating correlated assets as independent. "Are stronger Shaffer Scores associated with
  stronger outcomes?" is therefore **NO** (not demonstrated), not PARTIAL.

---

## 1. P0 correctness status — DONE (two items PARTIAL)

| Item | Status | Evidence |
|---|---|---|
| Buying cannot create negative cash | DONE | `engine/portfolio.py` `Ledger._replay` checks buying power (cash − short collateral − futures margin) on every date; `test_buy_beyond_cash_is_rejected` |
| Back-dated trades replay correctly | DONE | whole ledger re-validated on insert; `test_backdated_sell_that_breaks_a_later_sell_is_rejected`, `test_backdated_buy_that_starves_a_later_buy_is_rejected` |
| Deleted trades replay correctly | DONE | deletes are voids, validated and audited; `test_void_is_validated_soft_and_audited` |
| NAV headline = reconstructed NAV history | DONE | one replay feeds both; `test_holdings_and_nav_history_agree_on_every_date` |
| No phantom cash through ordering | DONE | same replay; the back-dated tests reproduce the old $2,288 phantom-cash case and now refuse it |
| One canonical ledger/replay | DONE | `Ledger._replay` (shared by holdings, NAV history, packages) |
| Cash dividends credited | DONE | ex-date credit; `test_dividends_are_credited_to_holders_on_the_ex_date` |
| ETF/fund distributions credited | DONE | same path (Yahoo distributions are dividend events) |
| Stock splits applied | DONE | `_split_factor` after the trade's basis date; `test_split_after_a_trade_at_the_brokers_price_keeps_its_value` |
| Reverse splits | PARTIAL | same multiplicative code (ratio < 1) — no dedicated test |
| Adjusted accounting consistent | DONE | `test_default_price_is_already_in_todays_basis` |
| EPS / earnings yield / P/E / EPS growth split-consistent | DONE | SEC per-share figures put in today's basis; `test_eps_filed_before_a_split_is_put_in_todays_basis`, `test_earnings_yield_has_no_step_at_a_split` (P/E and growth use the same adjusted EPS) |
| USDJPY, USDCAD, USDCHF, USDCNY, USDMXN, USDINR | DONE / PARTIAL | USD-base pairs valued as financed positions (one code path); `test_long_usdjpy_gains_when_the_dollar_rises` — only USDJPY has its own test |
| Long and short FX P&L tested | PARTIAL | long tested; short FX goes through SHORT on the same valuation, not separately tested |
| Foreign-currency portfolio valuation | DONE | EWJ/EWG etc. valued in USD; hedge engine looks through to the currency |
| Backtest leaks (full-history direction, full-history standardisation, same-close execution) | DONE | `2686ef2`: 1-session execution lag, point-in-time direction, expanding standardisation |
| FRED revisions don't leak | DONE | first releases on their publication dates (`ae4b0f3`); `test_first_release_ignores_later_revisions`, `test_value_visible_exactly_from_its_publication_date` |
| Publication delays respected | DONE | same; monthly series lagged 45 days where no vintage exists |
| CPI / unemployment / NFCI treatment documented | DONE | `ARCHITECTURE.md`; NFCI used only from first-release vintages (`TestNfciNeedsVintages`) |
| Live and historical scoring use the same function | DONE | `Canonical.test_a_past_date_scored_directly_equals_its_record` |
| Regime adjustment present historically | DONE | the sweep computes the regime on each date |
| Expected return / expected error without future data | DONE | isotonic calibration only from scores that matured before the refit |
| VaR label corrected | DONE | `be2af0f` |
| Docs don't overstate look-ahead protection | DONE | `AUDIT.md` "Fixed since"; `ARCHITECTURE.md` lists what is and isn't point-in-time (fundamentals by filing date; no vintages for NFCI-like series) |
| Regression tests for every resolved P0 | DONE | `tests/test_fs2_ledger.py` (15), `test_fs2_vintages.py` (15), `test_fs2_shaffer.py` (11) |

Found and fixed in this round: WTI's −$37.63 close crashed analytics (`d0c7e6b`); a negative quantity was silently
booked as its absolute value, and unknown sides/objectives were accepted (`82904a7`).

## 2. One canonical Shaffer Score — DONE

`engine/shaffer.py` `ShafferRun` + `compute_shaffer_score(asset, horizon, as_of)` (config `shaffer_score.py`). Live:
`Research.shaffer_full` runs the same sweep to the last session. Historical: the same sweep's records. Backtests:
the backtest engine consumes the recorded point-in-time scores. Marketplace: `/markets` → `Research.light()` (latest
record). Portfolio/Watchlist: the same `light()` rows. There is no second formula. Tests: `Canonical` (3 tests).

## 3. Exact formula — DONE (implementation penalty lives outside the raw score)

```
SS_raw = 100·tanh( Σ_f A_f,a · H_f,h · FamilyScore_f / K_a,h )
FamilyScore_f = Σ_{i∈f} ω_i · s_i · c_i · r_i · d_i          (ω = correlation-penalised weights, Σω = 1)
K_a,h = 0.10 · Σ_f A_f,a · H_f,h over families with data
edge = shrunk (g(SS_raw) − ȳ),  g = isotonic map raw score → forward return (vol units), prior OOS only, ȳ = asset average
SS_cal = 100·tanh( edge / 0.25 )   (evidence relative to the asset's own average; E[R] = typical + evidence part)
SS_net(long/short) = 100·tanh( edge / σ_h / 0.25 ),  edge = calibrated expected return − costs of that side
```
s = δ·clip(z/2, −1, 1), z expanding (≥252 obs, capped ±3); w = |PS|·(¼ + ¾·stability), PS = median of three IC
estimates, shrunk toward asset-class/global evidence 20/(n_eff+20); c = confidence (§8); r = regime (§10); d = decay
(§9); A = product applicability, H = horizon applicability (tables in `SHAFFER_AUDIT.md` §10–11); U = uniqueness via
ω (§7); W: families enter with equal weight scaled by A·H (no separate learned family weight); K as above.
**Implementation penalty**: not inside SS_raw. Costs enter the net long/short scores (`hedge/scoring.py`):
spread, commission, borrow, lost interest on short proceeds, leveraged-fund decay, volatility premium for options.

## 4. Horizons — PARTIAL

1D, 1W, 1M, 3M, 6M, 12M are scored for 123–132 assets. 3Y and 5Y are defined but no asset has enough independent
observations; the UI shows "—" (insufficient evidence). 10Y is not offered at all. Median n_eff: 1D 1156, 1W 1147,
1M 272, 3M 87, 6M 38, 12M 14 (`SHAFFER_AUDIT.md` §14).

## 5. Long vs short — PARTIAL

`hedge/scoring.py` `net_scores`: long = E − r·h − frictions; short = −E − frictions_short, where short frictions
include borrow (general collateral 0.30%/yr, assumed), no interest on short proceeds, spread and commission;
dividends owed are shown (a short pays them) but, because E is a total return, they are already in −E. Futures are
symmetric apart from costs; options are long-only. SS_short ≠ −SS_long: e.g. SPY short −31/−40/−62 at 1W/1M/3M
while the long is not the mirror; TQQQ long negative and short positive (volatility drag). Missing: squeeze/tail
risk of shorts and hard-to-borrow rates (no data). The net scores use the calibrated scale, not SS_raw.

## 6. Signal families — PARTIAL (15 of 22)

Implemented (signals listed per family in `SHAFFER_AUDIT.md` §2–3): Momentum, Trend, Mean Reversion, Valuation,
Fundamental Quality, Fundamental Growth, Risk-Adjusted Performance, Volatility, Statistical/Time Series, Rates,
Credit, Macro, Liquidity, Cross-Asset, Relative Value. Not separate families: **Carry** (no dividend/roll yield
signal), **Yield Curve** (slope sits inside Rates), **Inflation** (CPI inside Macro, breakevens inside Rates),
**FX** (dollar momentum inside Cross-Asset), **Commodity** (oil/gold momentum inside Macro), **Term Structure** (no
futures curves), **Optionality / Volatility pricing** (no option chains; VIX indices exist but aren't a family).
Family score: ω-weighted sum of s·c·r·d; applicability by asset class and horizon in the audit tables.

## 7. De-duplication — DONE

ω_i = (w_i ÷ Σ_j ρ²_ij) normalised within the family, ρ from the signals' z-scores up to the refit; two identical
signals count as one. Families (not signals) are summed, so six trend/momentum signals are one or two votes.
Test: `test_correlated_signals_do_not_vote_twice` (duplicating a signal leaves the score unchanged). No explicit
clustering step beyond this; no separate family cap (tanh and K bound the total).

## 8. Confidence — DONE

c = min(1, √(n_eff/100)) × |IC|/(|IC| + 1.96·SE) × (1 − ½·q) × data quality; SE = 1/√(n_eff − 3); q =
Benjamini–Hochberg over all signals at that horizon; data quality = share of the last year with a value; stability
enters w. Weak evidence shrinks toward 0 (both through c and the hierarchical prior). Test: `test_bh_qvalues`.

## 9. Decay — DONE

trend = 0.6·(δ·IC₃ᵧ/|IC|) + 0.4·(δ·IC₁ᵧ/|IC|): HEALTHY ≥ 0.6, WEAKENING ≥ 0.2, else DECAYING, INSUFFICIENT DATA below
10 independent 3-year observations. d = 1 − λ(1 − clip(½ + ½·trend, 0.1, 1)), λ = n_eff₃ᵧ/(n_eff₃ᵧ + 30): a signal
that stops working loses weight gradually and never flips sign. Latest refit: `ret_3m` decaying in 75 assets at 3M.

## 10. Regime adjustment — DONE

Dimensions: market (bull/bear), volatility, rates, inflation, growth (recession/expansion), dollar, liquidity — each
from data up to t (trailing moves of SPY, VIX, 10Y, CPI first releases, unemployment, DXY, Fed balance sheet).
r = clip(1 + mean λ·(ratio − 1), 0.5, 1.5), λ = n_eff_state/(n_eff_state + 50) damps small regime samples. Up to 12M.

## 11. Historical Shaffer Score — DONE

One forward sweep: an observation dated t enters evidence at t + h + 1; monthly refits; macro as first releases on
publication dates; fundamentals by SEC filing date; splits applied to per-share data; standardisation expanding;
weights, regime, calibration and expected return from data before t. Tests: `test_later_prices_do_not_change_an_earlier_score`,
`test_a_past_date_scored_directly_equals_its_record`.

## 12. 15–25 year validation — DONE where data exists

Per asset (years of prices, first 3M score, OOS record) in `SHAFFER_AUDIT.md` §13: most US equities/indices from
1993 with scores from 2001-09 (+24.7 years of 3M out-of-sample record); newer assets have less (ETH +0.7y, SOL none).
Effective N by horizon in §14 (1D 135,752 pooled … 12M 1,726). Expanding training throughout.

## 13. Calibration — DONE (the relationship is weak)

Buckets (−100..−60, −60..−40, −40..−20, −20..−5, −5..5, 5..20, 20..40, 40..60, 60..100 — the extremes are pooled
because few scores reach ±80) with mean, median, up share, volatility, 95% CI and independent N per horizon
(`SHAFFER_AUDIT.md` §15–16). 1D is roughly monotone (−0.3% → +0.4% at the extremes, CIs excluding zero only there);
1W is monotone from −40 to +60 and breaks at the top bucket; 1M–12M are **not** monotone.

## 14. Raw vs calibrated — DONE

Isotonic (pool-adjacent-violators) map from raw score to forward return in volatility units, fitted only on
scores that matured before the refit; SS_cal = 100·tanh(g/0.25). Shown side by side on every screen. Test:
`test_isotonic_is_monotone_and_pools_violations`.

## 15. Expected return — DONE

Expected return = g(raw score) × forecast volatility × √h, where g is the point-in-time isotonic map, reported only
when the calibration is significant; otherwise it is suppressed ("no significant calibration") and the net scores
assume no edge. A range (CI) and confidence are shown with it.

## 16. Shaffer prediction ledger — DONE

`predictions` table (`data/store.py`), written by `engine/tracking.py` `record_shaffer` once per asset/horizon/date:
made_on, asset, horizon, model version, raw, calibrated, expected return, range (lo/hi), confidence, regime,
family points, n_eff, target date; graded once by `score_matured` (realised return, error, correct direction,
scored_on). Append-only (`test_record_is_append_only_and_graded_once`).

## 17. Universal product coverage — PARTIAL

61 product types (`hedge/products.py` `PRODUCT_TYPES`, generated table in `PRODUCT_REGISTRY.md`):

| Status | Count | Products |
|---|---|---|
| SUPPORTED | 8 | Cash (USD); common stock; short stock; ETFs; leveraged ETFs; inverse ETFs; FX spot; crypto spot |
| MODELLED | 9 | Government bonds; equity index futures; Treasury futures; FX futures; FX forwards; equity calls; equity puts; index/ETF options; commodity (ETF) options |
| PROXY | 12 | Money market (SGOV); T-bills (BIL/SGOV); preferred (PFF); REIT (VNQ); TIPS (TIP/SCHP); municipal (MUB); IG corporate (LQD); high yield (HYG/JNK); FRN (FLOT); leveraged loans (BKLN); agency MBS (MBB); convertibles (CWB) |
| PARTIAL | 2 | ADR/foreign equity; ETN (VXX) |
| ANALYSIS_ONLY | 3 | Commodity futures; interest-rate swaps; variance swaps |
| NOT_SUPPORTED | 28 | Closed-end funds; ABS; single-stock futures; crypto futures; NDF; commodity forwards; FX options; crypto options; warrants; OIS; FRA; caps; floors; swaptions; TRS; equity swaps; commodity swaps; FX swaps; cross-currency swaps; inflation swaps; CDS buy/sell; CDX/iTraxx; volatility swaps; VIX futures; VIX options; crypto perpetuals; structured notes |

Each NOT_SUPPORTED row states why (e.g. no NAV series for CEFs, no swap/OIS curve, no rate or FX vol surface, no
CDS spreads, OneChicago closed in 2020, no VIX futures curve). Test: `test_registry_is_complete_and_honest`.

## 18. Product-specific Shaffer logic — PARTIAL

Families apply by asset class (applicability table). Expected-return decomposition: the underlying's calibrated
expected total return; futures subtract financing (r·h) and add nothing for carry (in the price); options use
Δ·S·(E − r·h) + (value at forecast vol − premium); leveraged ETFs carry volatility drag L(L−1)σ²/2; short side adds
borrow and lost interest. Costs and risk units per product in the registry. Horizon restrictions: options limited
to their expiry; 3Y/5Y unscorable everywhere. Missing: product-specific signals (curve roll-down for bonds, basis
for futures, skew for options) — data not available.

## 19. Product risk registry — DONE for eligible products

`ProductRiskProfile` (`hedge/products.py`), built by `Priced.profile()`: product_type, underlying, currency,
market_value, notional, equity_beta, factor_betas, delta, gamma, vega, theta, rho, duration, convexity, DV01,
key_rate_DV01, spread_duration, CS01, FX/commodity/crypto/inflation/volatility exposure, liquidity, financing,
borrow_cost, carry. Fields the model can't produce are `None` (e.g. convexity for bond ETFs, rho for futures).

## 20. Universal Shaffer Hedge — DONE

`hedge/engine.py` `analyze(research, positions, objective, params)` accepts any list of positions: one position
(`#/hedge/TLT`, screenshot 30), a group, the whole portfolio (`#/hedge`, 04/28/29) or a proposed trade (ticket).
Long and short positions are signed exposures. Order: risk vector → targeted factors → candidates carrying that
risk → per-product sizing → optimiser. Tests: `Engine` (4).

## 21. Universal risk vector — DONE (liquidity and funding are not factors)

Units: $ P&L per unit factor move. MKT (beta-$); 11 sector spreads; semiconductors; size and value styles;
RATE 2Y/5Y/10Y/30Y (−key-rate DV01); REAL 10Y (TIPS); CREDIT IG/HY (−CS01, ICE OAS); FX per currency; CMD oil,
gas, gold, silver, copper, agriculture, broad; CRYPTO; VOL ($ per VIX point); IDIO per asset. Options add delta
(linear), gamma-$, vega-$, theta-$. Liquidity is a per-product input (ADV, spread), funding a cost, neither a
risk factor.

## 22. Equity hedge sizing — DONE

Beta-dollars = market value × β (ridge on MKT, own sector, styles, shrunk to prior β); shares = beta-$ ÷ (price × β
of the hedge). Test: `test_equity_hedge_uses_beta_dollars_not_market_value`.

## 23. Futures hedge sizing — DONE

Contract notional = F × multiplier (F at fair value S·e^{(r−q)T}); contracts = beta-$ ÷ (F × multiplier × β);
margin (6% equity index, 2.5% Treasury, 4% FX — estimates) is collateral only. Test:
`test_futures_use_notional_times_beta_and_margin_is_not_exposure`. UI: "margin (estimate) … — collateral, not exposure".

## 24. Option hedge sizing — DONE (flat volatility)

Delta-adjusted notional = contracts × 100 × S × |Δ|; premium is cost. Shown per leg: multiplier, Δ, Γ, vega, θ,
premium, delta-adjusted notional (screenshot 28: SPX 7320 put, Δ −0.188, Γ 0.00053, vega $819/vol pt, θ −$106/day,
premium $5,387, delta-adjusted notional −$144,529). Test: `test_option_hedge_is_delta_adjusted_notional_not_premium`.
Implied vol from Cboe indices on FRED (VIX/VIX3M/VXN/RVX/VXD/GVZ/OVX/VXEEM/VXEWZ and equity VIXes for AAPL, AMZN,
GOOGL, GS, IBM), flat across strikes.

## 25. Option nonlinearity — DONE

Each option leg is fully re-priced at 0/−5/−10/−20/+5/+10% with the VIX moving as it historically does on up/down
days; the hedge ratio uses re-computed deltas (screenshot 28: 22% → 53% → 71% → 88% on the way down). Test:
`test_option_hedge_ratio_rises_as_the_market_falls`. Crash objective sizes on the −20% scenario.

## 26. Rate hedge sizing — DONE

Key-rate DV01 (2/5/10/30Y) for bonds and bond funds (duration split across key rates); Treasury futures carry the
DV01 of the CTD-maturity forward; contracts = DV01 ÷ DV01 per contract. TLT example (screenshot 30): 1 UB contract
($145.86 DV01) takes 30Y DV01 from $291 to $180 and 10Y from $36 to $2. Test: `test_treasury_future_uses_dv01`.
Swaps: ANALYSIS_ONLY (no swap curve).

## 27. Credit hedge sizing — DONE (proxy products only)

CS01 = −price × spread duration × 1e-4 against ICE BofA OAS changes (Baa−10Y before 2023-09); floating-rate funds
carry rate duration 0.2y. Test: `test_bond_etf_dv01_and_credit_cs01`. No CDS/CDX (no spread data).

## 28. FX hedge sizing — DONE

Currency exposure in currency-dollars (foreign equity ETFs look through to their currency); forwards/futures
sized by currency notional. Test: `test_fx_forward_uses_currency_notional`. Walk-forward result is poor (§73.23).

## 29. Commodity hedge sizing — DONE (ETFs), futures ANALYSIS_ONLY

Commodity-$ per share of commodity ETFs (1:1 structural); futures contract notional = price × multiplier is
computed for analysis but futures aren't tradeable (only continuous front-month data → roll gaps). Test:
`test_commodity_structural_exposure`.

## 30. Volatility hedge sizing — PARTIAL

Portfolio vega = Σ option vega × (IV/VIX); VXX exposure is an empirical $ per VIX point. No variance exposure,
no VIX futures basis (no curve), no vol-beta beyond the VXX regression.

## 31. Long/short hedge logic — DONE

Long NVDA (ticket, 22): sell SMH, sell MES. Short SPY (27): **buy** ES futures. Crash objective: buy puts. Short
positions can be hedged by buying stock/futures or buying calls (options are long-only; calls appear as
candidates, e.g. SPY/SPX calls in 27). Test: `test_beta_target_is_met_within_a_lot_and_long_short_symmetric`.

## 32. Optimiser — DONE

```
min_q (γh/2)·[ w_S·(X_S − R*_S)ᵀ Σ_SS (X_S − R*_S) + X_Nᵀ Σ_NN X_N ] + Σ_j c_j|q_j| + Σ_j k_j|q_j|^1.5
X = R + Bq,  γ = A/NAV (A = 2),  h = horizon (sessions)
```
Risk mismatch: first term (w_S = 10,000 when sizing, 50 when choosing products/lots, 1 for variance objectives;
factor-by-factor matching zeroes cross-covariance between targeted factors). Basis: X_N includes each hedge's own
residual (IDIO) and every non-targeted exposure it adds. Cost: c_j = expected frictions over the horizon.
Liquidity: k_j|q|^1.5 = square-root impact 0.5·σ·√(notional/ADV). Turnover: not a separate term (every trade pays
c_j). Forward stepwise selection (≤2 legs, 3 for systematic/name/variance), whole lots.

## 33. Objectives — DONE (17 of 17 + systematic)

beta (reduce), neutral, sector, name, duration, curve, credit, fx, commodity, crypto, volatility, crash, var, es,
drawdown, min_variance, target_vol, and `systematic` (the ticket default). Automatic picks the dominant risk. An
objective that targets <2% of the book's variance now warns and names the largest risk (`5325481`). Unknown
objectives are refused (`82904a7`).

## 34. Basis risk — DONE

Linear: basis = √(Var after the hedge − Var of the ideal hedge) in $/day from the factor covariance plus residuals;
B = ρ² between the hedge's P&L and the targeted P&L. Walk-forward residual $/day is reported per case. Nonlinear:
options re-priced in scenarios; basis measured on realised P&L in the walk-forward, and T (convexity) scores tail fit.

## 35. Hedge cost — PARTIAL

Included: premium (via volatility premium: premium at implied vol − value at forecast vol), theta (shown), bid/ask
(Corwin–Schultz, ≥1bp), commission ($2.25/futures side, $0.65/option), slippage/impact (k|q|^1.5), borrow (GC
0.30%), financing (no interest on short proceeds), futures roll (ticks per roll), FX carry (forward points shown,
not a cost — it is in the price), fund structural drift, leveraged-fund drag. Not included: swap spread and CDS
premium (products unsupported). Option bid/ask assumed, not quoted.

## 36. Liquidity — PARTIAL

ADV ($) and spread estimate from daily high/low/volume; futures and options use the tracking ETF's ADV. No open
interest, no futures/option volume (no data). Penalty: L = 1/(1 + participation/10%) in the score and impact cost
in the optimiser.

## 37. Shaffer Hedge Score — DONE

```
SH = 100·tanh(E·Q·L·R·B·T / 1.0)
E = min(x, 2.5 − x) ∈ [−1, 1.25], x = walk-forward realised ÷ expected reduction (crash: tail offset ÷ requested share)
Q = value/(value + cost)   L = 1/(1 + participation/10%)   R = regime reduction ratio shrunk n/(n+20), [0.5, 1.5]
B = ρ²(hedge P&L, targeted P&L)   T = crash: option gain ÷ linear gain at −20%, [0.5, 1.5]; else 1
```
Nothing is ranked by hand. Fixed this round: E used to cap overshoot at 1.25 without penalty, so deep puts that
offset 3× the requested tail (and raised variance 15×) scored +92; they now score −85 (`5325481`,
`EffectivenessTerm`).

## 38. Eligibility — DONE

`Priced.eligibility()` requires market data (not stale: price ≤5 sessions, daily macro ≤10 days, monthly ≤100),
pricing (a model and its inputs), conventions (spec), risk model, sizing rule; otherwise `PRODUCT NOT ELIGIBLE FOR
SHAFFER HEDGE` with reasons (e.g. NVDA puts: "no implied-volatility data"; 6S: CHF rate series ended 2024;
indices not investable; continuous commodity series). UI lists ineligible products with reasons. Test:
`test_refusals_carry_reasons`.

## 39. ML score — DONE

`engine/ml.py`: OLS, ridge, lasso, elastic net, random forest, gradient boosting (pure Python), separate return,
direction (logistic), volatility and drawdown models; ensemble. Independent of the Shaffer Score (Shaffer is only
a baseline). Tests: `test_fs2_ml.py` (7), `test_fs2_models.py` (27).

## 40. ML baselines — DONE (buy-and-hold = historical mean)

Zero, historical mean (expanding, = buy-and-hold drift), previous return, momentum, mean reversion, Shaffer.
Unverified → ML Score 0 and "NO VERIFIED ML EDGE". Verified return edge only in EFA, EURUSD, USDJPY (1D) and GOLD,
XOM (1W) of 21 assets. Tests: `VerifiedEdge` (4).

## 41. ML walk-forward — DONE

Chronological expanding training; purge = horizon h (training rows whose target window closed before the test
block); expanding standardisation; features fixed (no selection on test); hyperparameters fixed; last 15% held out
untouched. Tests: `test_holdout_is_the_last_fifteen_percent`, `test_expanding_mean_uses_only_matured_outcomes`.

## 42. ML prediction ledger — DONE

Same `predictions` table (model = ensemble/direction/volatility/drawdown, version, predicted, error band,
confidence, score, target date); graded by the daily learning loop (`server.py daily_learning` → `score_matured`).

## 43. ML hedge layer — PARTIAL

`hedge/history.py` `ml_layer`: ridge per (risk, hedge kind, horizon) group. Inputs: VIX, 1-year and 3-month
correlation of book and hedge, change in beta, 3-month market return, bill rate; group identifies risk, product
kind, horizon; ratio via the target. Not inputs: Greeks, duration, liquidity, basis, cost, long/short (symmetric).

## 44. ML hedge targets — PARTIAL

Target: log(ex-post minimum-variance multiple × target share) — i.e. the variance-optimal ratio. Graded outcomes in
the walk-forward and hedge ledger: variance reduction, tail (ES-like) reduction, drawdown, hedge P&L, cost, upside
sacrificed, basis error. Not learned as separate targets: VaR, ES, DV01/CS01 reduction.

## 45. Raw vs ML — DONE

FinalHedge = RawHedge × (1 + 0.5·MLAdjustment), |MLAdjustment| ≤ 0.3 → at most ±15%. UI shows raw quantity,
ML adjustment with reason, final quantity (22, 28–30). Test: `MLCap`.

## 46. ML hedge baselines — PARTIAL

Compared with no hedge, fixed 25%, fixed 50%, the static rule (= static beta / DV01 / delta hedge — the Raw Shaffer
Hedge) and minimum variance. Verified only in `name:spot:63` (t 2.2 vs static, 2.7 vs min-var); every other group
has adjustment 0. Static delta is the static rule for option legs.

## 47. Shaffer vs ML — DONE

Per asset and horizon: Shaffer, calibrated, ML, agreement (STRONG AGREEMENT / MODERATE AGREEMENT / MIXED / STRONG
DISAGREEMENT / NO VERIFIED ML EDGE, `engine/scores.py agreement`) on Markets, Asset Research and the ticket. Pooled:
ML IC higher than Shaffer on the same rows at every horizon (`SHAFFER_AUDIT.md` §25), yet a verified ML edge is rare;
combining beat both in 4/21 (1D), 2/21 (1W), 0–1 elsewhere.

## 48. Marketplace main feed — DONE

`#/markets` (20): 160/160 products with Shaffer, calibrated, ML, agreement, confidence, net long/short (costs
included), horizon switch (1D…5Y), "Data as of" line, Trade button; click → product drawer (21) → ticket.

## 49. Trade entry + Shaffer — DONE

Ticket (22): pills per horizon, selected horizon's Shaffer/calibrated/ML/agreement/confidence/expected return,
"Driving it" and "Against it", net long/short at the horizon, one-line reading guide.

## 50. Automatic hedge panel — DONE

On every quantity/side/horizon change: the trade's own risk vector, the book before/after trade/after hedge
(22 right card), objective (systematic), candidates with scores, raw quantity, ML adjustment, final quantity,
cost, basis, expected variance cut, "why this leg".

## 51. Optional hedge execution — DONE

Trade only / Trade + Shaffer Hedge toggle (23, 24); Trade + Hedge submits one package (26).

## 52. Preview — DONE

Confirm box (25): each leg (instrument, side, quantity/contracts, risk unit, notional, est. cost) above the full
before/after table (before trade, after trade, after hedge; hedge % per factor; residual risks).

## 53. User control — DONE

25/50/75/100% or custom %; hedge objective selector; click a candidate to use it alone; Trade only declines (the
proposal is logged as declined). Never forced.

## 54. Multi-product hedge — DONE (two legs typical)

Stepwise selection gives multi-leg packages when a second leg improves J ≥2%: NVDA → SMH (sector/semis) + MES
(market), each with its "why" (22). A single-name NVDA put can't be offered (no NVDA implied vol).

## 55. Atomicity — DONE

`Ledger.trade_package` validates every leg together (cash, 150% short collateral, futures margin, holdings, no
option writing) and inserts in one SQLite transaction; any failure → nothing booked, message "the package was not
recorded (no leg was)" (27). Test: `test_package_is_atomic`. No partial state to reconcile.

## 56. Option hedge UI — DONE

Strike, expiry, premium (per contract and total, % NAV), contracts, Δ, Γ, vega, θ, delta-adjusted notional, % risk
hedged, hedge ratio and option value at 0/−5/−10/−20/+5/+10% (28). Market value = premium at purchase.

## 57. Futures hedge UI — DONE

Symbol and contract month, side, price, multiplier, contract notional, contracts, total notional, β or DV01 per
contract, hedge % per factor, margin (estimate, labelled collateral), expiry, roll-by date and rolls in horizon
(22, 29, 30).

## 58. Portfolio hedge — DONE

`#/hedge` (04): KPIs (NAV, risk $/day, largest factors, hedge coverage, carrying cost, regime), objective/horizon/
size controls, risk table, package, candidates, ineligible list, before/after, scenarios, Execute hedge, hedge
ledger. Per-position "Analyze hedge" from the Portfolio page.

## 59. Hedge history — DONE (grading unproven on live data)

`hedge_recommendations` (append-only): every executed or declined proposal with expected reduction and cost;
`grade()` (daily learning) records realised variance reduction, effectiveness, hedge P&L, drawdown saved, upside
given up, basis error once the horizon passes. The first live entry matures 2026-10-25, so no live grade exists yet.

## 60. Historical hedge validation — DONE

`HEDGE_AUDIT.md`: 110 cases × 1W/1M/3M, month-start windows since 2011, by hedge product, original product, risk,
horizon and regime (§73.20–22).

## 61. Portfolio performance — DONE

`engine/portfolio.py`: daily P&L, chained TWR (and annualised), XIRR money-weighted return, 1D…YTD…inception
periods, benchmark periods (SPY by default) with tracking error and information ratio. Tests: `Performance` (2).

## 62. Attribution — PARTIAL

Risk attribution by factor and by position (Risk page, hedge factor table). "Why did my portfolio move today?"
return attribution by asset/sector/asset class/country/currency/factor is **not built**.

## 63. Risk page — DONE

VaR 95%/99% and ES 95% (historical simulation), max drawdown, Sharpe/Sortino, beta and duration, factor drivers;
DV01/CS01/FX/commodity/crypto and book delta/gamma/vega/theta in the hedge page and ticket; risk contribution by position (concentration), correlation matrix and decay,
user-defined scenario, historical analogues, Monte Carlo cone (03).

## 64. Stress tests — PARTIAL

Built into every hedge view: market ±5/±10/−20/+10, VIX +10, rates +100bp parallel, curve steepener (2Y −25bp,
30Y +50bp), credit widening (HY +200bp, IG +75bp), USD +10%, oil −30%, crypto −50%. Missing: +200bp, curve
flattening, commodity shocks beyond oil, correlation convergence.

## 65. Historical crisis replays — NOT DONE

No 2000/2008/March 2020/2022 portfolio replay tool. The Risk page's historical analogues and the hedge audit's
best/worst windows (e.g. Feb–Mar 2020) cover part of it.

## 66. Data freshness — PARTIAL

Price date in the sidebar pill and every hedge view ("data Sep 24, 2026"); Markets "as of" line; stale inputs make
products ineligible with the reason (price >5 sessions, daily macro >10 days, monthly >100 days). Fundamentals are used
only from their filing dates, but the dates are not shown next to each figure; ML training time is stored in
`model_runs` but not surfaced everywhere; there is no global stale-data banner.

## 67. Failure handling — DONE

Checked on the live server: bad ticker → 400 "unknown asset"; zero/negative/non-numeric quantity → 400 (fixed this
round); unknown side/objective → 400 (fixed); missing option vol (NVDA) → ineligible with reason; missing FX rate
(6S) → ineligible; unsupported product → NOT_SUPPORTED with reason; ML failure/unverified → adjustment 0 with
reason; optimiser finds nothing → "No package" warning; illiquid → L and impact penalty; package over buying power →
atomic refusal (27). No uncaught server errors in the screenshot run (`ERRORS []`).

## 68. Test coverage — PARTIAL

New/extended this work: `test_fs2_hedge.py` (22: sizing units for equity, futures, options, Treasury futures, bond
DV01, credit CS01, FX forward, commodity; eligibility and registry; engine targets, long/short symmetry, units,
option nonlinearity, scenarios; effectiveness term; invalid input; ML cap; ledger short/future/option/package/
forward), `test_fs2_shaffer.py` (11: point-in-time history, de-duplication, points add up, priors, isotonic, BH,
families declared, ledger, non-positive prices), `test_fs2_ml.py` (7). Not unit-tested: crypto and ETF hedges
individually (covered by the audit run), score bounds per horizon, decay and regime functions in isolation, and
the UI (checked by the 25-screenshot CDP run on desktop and mobile, not by assertions).

## 69. Performance — DONE (measured on the demo server, warm caches unless noted)

| Operation | Time |
|---|---|
| Marketplace feed (160 products) | 0.41 s (cold scan of 160 assets: minutes; warmed in parallel) |
| Net long/short scores, all products (cached) | 0.002 s |
| Net scores for one product | 0.73 s |
| Portfolio | 0.19 s |
| Portfolio hedge analysis (automatic objective) | 7.0 s |
| Option (crash) hedge analysis, walk-forward cached | 1.3 s |
| Trade preview with hedge (NVDA) | 7.7 s |
| Hedge registry | 0.002 s |
| Shaffer audit (133 assets) / hedge audit (330 case-horizons) | 15 min / 4.0 min |

Uncached single-asset research (Shaffer sweep + ML) takes tens of seconds per asset.

## 70. Documentation — DONE

README, ARCHITECTURE, AUDIT ("Fixed since"), SHAFFER_AUDIT (score design and results), SHAFFER_HEDGE (hedge
design, trade/hedge workflow), PRODUCT_REGISTRY (generated), HEDGE_AUDIT, this report.

## 71. Known limitations

No option chains (Cboe chain host blocked here): flat implied vol, no skew, no single-stock options without a Cboe
index (NVDA puts ineligible), option bid/ask assumed. Futures, forwards and options are model-priced, not quoted;
no open interest or futures volume. Treasury futures without conversion factors/actual CTD. Commodity futures only
as continuous front-month series. 28 product types unsupported (§17). Borrow rates assumed (GC). Cash earns no
interest in the ledger. No FRED vintages for some series (NFCI used only as first releases; others lagged).
Fundamentals: SEC companyfacts, no consensus estimates, no point-in-time restatements beyond filing dates. 3Y/5Y
horizons unscorable; 12M has median n_eff 14. Credit data: index OAS only (no issuer spreads, no CDS).

## 72. Final Shaffer Score report

1. Formula: §3. 2–3. Families and signals: §6 and `SHAFFER_AUDIT.md` §2–3 (15 families, 74 signals). 4–5. Products
and applicability: 133 assets with ≥6 years scored (equities, ETFs, indices, Treasuries, corporate index,
commodities, FX, crypto); applicability table `SHAFFER_AUDIT.md` §10. 6. Horizons: §4. 7. Confidence: §8.
8. Regime: §10. 9. Decay: §9. 10. De-duplication: §7. 11. Calibration: §13–14. 12. 15–25Y: §12.
13. OOS by horizon (IC weighted by n_eff; Stouffer t): 1D +0.005 (1.9), 1W +0.011 (3.8), 1M +0.003 (0.5), 3M −0.006
(−0.6), 6M +0.005 (0.4), 12M −0.016 (−0.6) — t pooled as if assets were independent; date-clustered, none is significant (see Update). 14. Buckets: §13. 15. By class (1W): Treasuries
+0.041, corporate +0.088 (1 asset), ETFs +0.015, equities +0.011, FX −0.012, commodities −0.005. 16. By regime (1W):
high vol +0.019, weak dollar +0.012, bull +0.012, low vol −0.000. 17. Works: Mean Reversion at 1W adds independent
information (incremental IC +0.010, t 3.5). 18. Doesn't: Risk-Adjusted Performance and Volatility at 1W show no
measurable value; long-horizon momentum/trend are negative OOS. 19. Decaying: `ret_3m`, `macd`, `excess_3m`,
`rate_duration` decaying in 33–75 assets at the latest refit.

**Verdict: the Shaffer Score is honest and point-in-time, but it has no statistically reliable out-of-sample predictive
power at any horizon once assets' co-movement is accounted for.** It is useful as a transparent summary of evidence, not as a return forecast.

## 73. Final Shaffer Hedge report

1. Optimiser: §32. 2. Risk factors: §21. 3. Products: §17 (SUPPORTED + MODELLED + PROXY eligible). 4. Risk units:
`PRODUCT_REGISTRY.md`. 5. Options: §24–25. 6. Futures: §23. 7. Rates: §26. 8. Credit: §27. 9. FX: §28.
10. Commodities: §29. 11. Crypto: crypto-$ per unit (IBIT, BITO; BTC spot not shortable). 12. Volatility: §30.
13. Costs: §35. 14. Basis: §34. 15. Liquidity: §36. 16. Score: §37. 17. Raw hedge: objective → target R* →
product sizing in its own unit → optimiser → whole lots. 18–19. ML: §43–46, cap ±15%.
20. Historical (median realised variance reduction, full-hedge target): stock short 100%, credit ETF short 95.5%,
Treasury ETF short 81%, Treasury future 70%, sector ETF 64%, index future 55%, ETF short 54%, inverse ETF 54%,
crypto ETF 48.5%, single-stock put 35% (tail 148%), index/ETF put −3.8% (tail 108%), FX forward −20%, commodity ETF
put −30% (tail 197%); effectiveness ≈1.0 for linear hedges.
21. By regime: bear +47%, high inflation +43%, high vol +42%, recession +38% … low vol −0.2%.
22. By horizon: 1W 46.5%, 1M 48.9%, 3M 49.1% (median).
23. Failures: currency forwards on EWJ/EWG/EWU raised variance (−13% to −32%: the currency offsets the equity);
USO puts in 2020 (−184% at 1W: wild vol), GLD puts, HYG hedged with BKLN; crypto shorts gave up large upside.
Best: SPY puts on NVDA/AAPL/XLK in Feb–Mar 2020.
24. Limitations: §71; the SH score's E term was mis-specified until `5325481`.

## 74. Final Marketplace report (screenshots sent with this report)

| # | What | Screenshot |
|---|---|---|
| 1 | Marketplace with Shaffer, calibrated, ML, agreement, confidence, net long/short, 160/160 scored | `20-marketplace-feed` (and `06-markets`, `31-mobile-markets`) |
| 2 | Product clicked: drawer with scores by horizon, net scores, Buy/Short | `21-product-clicked-drawer` |
| 3 | Trade ticket: NVDA BUY 1,000, scores, drivers, net of costs | `22-trade-ticket-and-shaffer-hedge-preview` (mobile `32`) |
| 4 | Shaffer Hedge preview: risk to hedge, SMH + MES package, candidates, ML note | `22` |
| 5 | Trade-only selected | `23-trade-only-selected` |
| 6 | Trade + Hedge selected | `24-trade-plus-hedge-selected` |
| 7 | Multi-leg confirmation (NVDA + SMH + MES) | `25-trade-plus-hedge-confirmation-multi-leg` |
| 8 | Before/after risk (before, after trade, after hedge; beta 0.41 → 0.54 → 0.41) | `22`, `25` |
| 9 | Successful execution → Portfolio with the new legs | `26-successful-execution-portfolio` |
| 10 | Failure: SPY short 200,000 exceeds buying power → no leg booked | `27-failure-handling-rejected-package` |

Also: `28` crash (option details), `29` beta (futures details), `30` TLT DV01 hedge, `01`–`12` every page.

## 75. Commits

| Hash | Title | Purpose | Key files | Tests |
|---|---|---|---|---|
| `be2af0f` | a validated ledger with splits, dividends and correct FX pairs | P0 accounting | engine/portfolio.py, data/store.py | test_fs2_ledger |
| `2686ef2` | backtests without look-ahead | P0 leakage | engine/backtest.py | test_fs2_models |
| `ae4b0f3` | point-in-time macro from FRED first-release vintages | P0 macro | data/fred.py, data/store.py | test_fs2_vintages |
| `d74cebf` | Shaffer Score v2, one point-in-time function | canonical score, calibration, ledger | engine/shaffer.py, shaffer_score.py, engine/tracking.py | test_fs2_shaffer |
| `03657f8` | ML v2 with a verified-edge rule, daily learning loop, hierarchical priors, universe audit | ML, learning loop | engine/ml.py, engine/scores.py, server.py, audit | test_fs2_ml |
| `b8906f5` | document Shaffer v2, ML v2, the daily loop and the audit command | docs | README, ARCHITECTURE | — |
| `d0c7e6b` | a non-positive price is missing for analytics (WTI) | bug fix | engine/align.py | NonPositivePrices |
| `894b5fa` | SHAFFER_AUDIT.md from the full-universe replay | evidence | SHAFFER_AUDIT.md | — |
| `08f7477` | Shaffer Hedge engine | risk vector, registry, pricing, sizing, optimiser, walk-forward | hedge/*.py, data/universe.py | test_fs2_hedge |
| `ac3d57f` | ledger with short sales, futures, options, forwards and atomic packages | accounting for hedges | engine/portfolio.py, data/store.py | LedgerDerivatives |
| `67bb0e6` | Shaffer Hedge service, UI, net long/short scores, hedge audit | Marketplace/ticket/hedge page | hedge/service.py, hedge/scoring.py, hedge/audit.py, server.py, static/app.js | test_fs2_hedge |
| `273b437` | systematic hedge objective, factor-by-factor matching, clearer ticket | trade default objective | hedge/engine.py, static/app.js | Engine |
| `1025ec7` | Shaffer Hedge design, product registry, README/ARCHITECTURE/AUDIT | docs | *.md | — |
| `5325481` | penalise over-hedging in the Shaffer Hedge Score; ticket layout; position view objective | score bug, UI | hedge/engine.py, static/* | EffectivenessTerm |
| `82904a7` | refuse invalid hedge inputs instead of guessing | validation bug | server.py, hedge/service.py, hedge/engine.py | InvalidInput |
| `8920779` | full Shaffer system report | report | SYSTEM_REPORT.md | full suite |
| `cb9c057` | continuous futures series cannot be opened; option prices labelled flat-volatility | P0 | engine/portfolio.py, hedge/*, static/app.js | ContinuousSeries, option label |
| `eda90ab` | hedge objectives grouped by purpose; variance vs tail hedges; Shaffer Score as evidence | UI, scoring | hedge/engine.py, static/app.js | HedgeType |
| `cd0a522` | seven candidate Shaffer families in shadow, with an out-of-sample admission test | new information, gated | engine/candidates.py, engine/shaffer.py, shaffer_score.py, engine/audit.py | ShadowFamilies |
| (this) | date-clustered admission test and audit; docs | validation | engine/audit.py, SHAFFER_AUDIT.md, docs | test_admission_rule |

## 76. Test results

Final run on commit `82904a7` + this report, 2026-09-25, one process per test file (4 in parallel; the single-process
discover run was stopped after 54 minutes because it would have hit its 90-minute timeout — nothing had failed):

| Suite | Files | Passed | Failed | Skipped |
|---|---|---|---|---|
| FinSim2 (`test_finsim2`, `test_fs2_*`) | 9 | 178 | 0 | 0 |
| Original FinSim (all other `tests/test_*.py`) | 34 | 239 | 0 | 0 |

| File | Result |
|---|---|
| `test_otc` | 18/18 OK (3,324 s — the slowest file) |
| `test_risk` | 7/7 OK (265 s) |
| `test_operations` | 7/7 OK (485 s) |
| `test_options_global` | 6/6 OK (35 s). The intermittent failure seen earlier did not occur in this run; nothing in this work touched it, so it is **not** known to be fixed — its cause is still unexplained |

Screenshot run (CDP driver, desktop 1440 px and mobile 390 px): 25 screenshots, `ERRORS []`.

## 77. Open issues

**P0** (money, risk, history or leakage)
- ~~Continuous commodity series can be bought as spot~~ — **fixed** (`cb9c057`): they can no longer be opened; old
  positions replay, are flagged and can be sold.
- Option marks are model prices at flat vol; OTM put values are likely understated (no skew), which flatters put
  hedges' cost in the score and ledger. **Now labelled** "MODEL-PRICED — FLAT VOLATILITY ASSUMPTION" everywhere
  (`cb9c057`); the pricing itself is unchanged until chain/skew data exist.

**P1** (major missing capabilities)
- Option chains/skew; single-stock options (NVDA puts); crisis replays; return attribution; 3Y/5Y evidence.
- Shaffer Score has no demonstrated OOS edge beyond 1W; seven requested families missing (Carry, Yield Curve,
  Inflation, FX, Commodity, Term Structure, Optionality).
- Cash earns no interest (idle-cash return understated; short proceeds treatment is retail-conservative).
- Tradeable futures only for index/Treasury/FX; swaps, CDS, VIX futures unsupported.
- Live hedge grading not yet exercised on matured entries.

**P2** — ML hedge inputs (Greeks, liquidity, cost); +200bp, flattener, correlation-convergence stresses; open
interest; borrow availability; hedge optimiser turnover term; option writing (covered calls, collars).

**P3** — Candidate table still scrolls on narrow screens; ticket preview takes ~7s; TLT page "why this leg" column
clipped at 1440px; reverse-split and short-FX dedicated tests.

## 78. Failures not hidden

| What | Why | Current behaviour | Risk | To finish |
|---|---|---|---|---|
| Option skew / chains | Cboe chain host (cdn-api.cboe.com) is outside the allowed domains | flat IV from Cboe indices; NVDA-type options ineligible | put hedge cost understated | allow a chain source, add a skew surface |
| 3Y/5Y/10Y scores | too few independent observations | shown as insufficient evidence | none (refused) | decades more data or pooled evidence |
| Shaffer predictive power | the signals don't forecast returns OOS | scores shown with low confidence | users may over-read scores | new families (carry, curve), better data |
| ML hedge adjustment | beats static and min-var in 1 of 21 groups | adjustment 0 elsewhere, with reason | none | richer inputs; more history |
| FX forward hedges | currency and local equity offset each other | offered, walk-forward shows −20% | a "hedge" that adds risk | objective-level warning when the audit shows negative reduction |
| SH score overshoot | E capped instead of penalised | fixed in `5325481` | was recommending 3× over-hedged puts | done |
| Negative quantity | abs() applied silently | fixed in `82904a7` | wrong-direction trade | done |
| Crisis replays, attribution | not built | absent | — | build on nav_history + factor model |

---

## Final acceptance questions

| Question | Answer | Evidence |
|---|---|---|
| Every dollar of NAV reconstructable? | YES | one replay; `test_holdings_and_nav_history_agree_on_every_date`; packages atomic |
| Historical Shaffer Scores reproducible point-in-time? | YES | `Canonical` tests; first-release macro; filing-dated fundamentals |
| Today's score uses the same function? | YES | `test_history_is_produced_and_the_live_score_is_its_last_record` |
| Stronger scores → stronger outcomes? | NO (not demonstrated) | best horizon 1W: IC +0.012, date-clustered t 1.2 (see Update); 1M–12M not monotone |
| Confidence falls when evidence is weak? | YES | c formula (§8); BH q-values; shrinkage |
| Every supported product gets an economically appropriate score? | PARTIAL | net long/short with costs for spot, futures, forwards, long options; 7 families missing; no product-specific signals |
| Every supported product exposes correct risk units? | YES | `SizingUnits` (7 tests), registry |
| Hedge long and short positions? | YES | long/short symmetry test; short SPY → buy ES (27) |
| Search across product classes? | YES | candidates span stocks, ETFs, inverse ETFs, futures, options, forwards, VXX (22, 28) |
| Options sized by delta-adjusted notional? | YES | `test_option_hedge_is_delta_adjusted_notional_not_premium` |
| Futures sized by notional, not margin? | YES | `test_futures_use_notional_times_beta_and_margin_is_not_exposure` |
| Bonds/rates by DV01/KRD? | YES | `test_treasury_future_uses_dv01`; TLT example (30) |
| Credit by CS01? | YES (proxies) | `test_bond_etf_dv01_and_credit_cs01`; no CDS |
| FX by currency exposure? | YES | `test_fx_forward_uses_currency_notional` |
| ML adjusts only after beating baselines? | YES | verified 1/21 groups; `MLCap` |
| ML can't override risk math? | YES | ±15% cap |
| Shaffer Score visible in Marketplace? | YES | 20 |
| Ticket analyses incremental risk automatically? | YES | 22 |
| Trade Only or Trade + Hedge? | YES | 23, 24 |
| Trade + Hedge preserves accounting? | YES | `test_package_is_atomic`; 26, 27 |
| Explains why each leg exists? | YES | "Why this leg" column (22, 25) |
| Unsupported products refused? | YES | eligibility + registry; `test_refusals_carry_reasons` |
| Stale/missing data surfaced? | PARTIAL | eligibility reasons and as-of dates; no global stale banner |
| Known look-ahead leaks removed? | YES | §1, §11, §41 |
| Safe enough for serious simulated research? | PARTIAL | accounting and point-in-time machinery yes; option pricing without skew and weak score evidence mean conclusions about puts and forecasts need care |

## Final summary

**What changed.** A risk-first Shaffer Hedge (risk vector, 61-type registry, per-product sizing, optimiser,
walk-forward, capped ML, hedge ledger); a ledger that books shorts, futures, forwards, long options and atomic
packages; net long/short Shaffer Scores; Marketplace → ticket → hedge → execute flow; ML v2 with a verified-edge
rule; full audits; two scoring/validation bugs fixed after the screenshot review.

**What now works.** Accounting integrity (P0s fixed), point-in-time scores and ML, hedging of equity, sector,
single-name, rates, credit, FX, commodity and crypto risk with the right units, crash hedging with re-priced
options, Trade + Hedge execution all-or-nothing, desktop and mobile UI.

**Mathematically validated.** Sizing units (tests); ledger invariants (tests); point-in-time guarantees (tests);
linear hedge effectiveness ≈1.0 over 15 years of walk-forward windows; ML cap.

**Only partially validated.** Option hedges (flat vol, variance reduction negative for index puts at full size,
tail reduction strong); ML hedge adjustment (one group); Treasury futures (no conversion factor); live grading of
hedges (none matured yet).

**Cannot be trusted yet.** Shaffer Score as a return forecast beyond 1W; option premiums for OTM strikes; FX
forward hedges of foreign equity; anything involving unsupported products.

**Biggest remaining P0/P1.** Continuous commodity spot buys in the ledger; no skew; no crisis replays or return
attribution; missing families; cash without interest.

**Recommended next 10 commits.**
1. Refuse (or roll-adjust) BUY of continuous commodity series in the ledger.
2. Credit idle cash at the bill rate and short proceeds per a broker setting.
3. Warn on objectives whose walk-forward reduction is negative (FX forwards on foreign equity).
4. Crisis replay tool (2000, 2008, 2020, 2022) on the current book.
5. Daily return attribution by asset, sector, class, currency and factor.
6. Carry and yield-curve families (dividend yield, roll-down, slope).
7. Stress additions: +200bp, flattener, correlation convergence, commodity basket.
8. Skew from a permitted chain source (or a documented skew proxy) with eligibility labels.
9. ML hedge inputs: Greeks, liquidity, cost; optional turnover penalty.
10. Speed up the ticket preview (cache the book's risk vector and candidates per data version).
