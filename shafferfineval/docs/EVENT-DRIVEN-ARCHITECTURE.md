# ShafferFinEval as a live decision system — event-driven architecture

**Status: DESIGN NOTE, recorded 2026-09-21. Not built. Nothing here changes the
historical work in flight.**

The target behaviour:

```
New market/fundamental information
  → recalculate affected factors
  → new Shaffer Score
  → new Shaffer Hedge
```

Open the terminal each morning and see current scores, what changed, why it
changed, and whether the hedge moved.

---

## 1. The point that matters for sequencing: most of this is already built

The historical point-in-time work and the live event-driven system are **the same
machinery pointed in opposite directions**. This was not planned; it fell out of
taking point-in-time correctness seriously.

| Live requirement | Already exists as | Status |
|---|---|---|
| "When did this information become actionable?" | `pit_fact.available_date`, computed by `information_latency_policy_v1` | **BUILT** |
| "Never overwrite yesterday" | Immutability triggers on `pit_fact`, `pit_feature`, `pit_score`; write-once close snapshots | **BUILT** |
| "Why did the score change?" | The derivation graph + stored intermediates + `effective_weights_json` | **Graph built, intermediate storage SPECIFIED not applied** |
| "Which factors does this event affect?" | `pit_derive` dependency graph — an event touches a primitive, and the graph names every derived node downstream | **BUILT** |
| "Is this score comparable to yesterday's?" | `score_signature` / `original_weight_coverage` | **SPECIFIED** |

**`available_date` is the event clock.** The historical question is *"what was
knowable at date t?"*. The live question is *"something just became knowable —
what must be recomputed?"*. Both are queries against the same column. A filing
accepted at 16:05 ET is available next session in the replay and triggers a
recompute next session in the live system, by the same rule and the same code.

**The dependency graph is the recompute planner.** When a primitive changes, the
graph's `descendants()` already returns exactly the set of derived nodes that
must be recalculated — no more and no less. That is the whole of "recalculate
affected factors", and it exists.

**"Why it changed" is impossible without the stored intermediates.** A score
moving +58 → +64 can only be decomposed into "EBITDA growth +4, real revenue
growth +2, valuation −1, sector +1" if the *previous* intermediates were kept.
Under the current schema they are computed and discarded. This is the single
thing that must be built before the "What Changed?" page can exist, and it is
already specified (`pit_cohort_stat` + per-company intermediates).

---

## 2. The three refresh speeds

```
MARKET REFRESH      prices, yields, spreads, curves, IV, futures — frequent
SCHEDULED REFRESH   daily/weekly/monthly economic releases
EVENT REFRESH       earnings, filings, Fed decisions, EIA, ratings changes
```

The existing `daily_job.py` is only the second of the three, and its `--date`
flag has already been fenced because it relabelled live data as historical.
Event refresh is genuinely new work.

---

## 3. Information events per asset class

| Class | Events |
|---|---|
| Equities | earnings, 10-Q/10-K/8-K, guidance, dividends, buybacks, splits, M&A, debt issuance; analyst estimates if licensed |
| Treasuries / rates | Fed decisions, CPI/PCE, jobs, GDP, auctions, curve moves, inflation expectations |
| Corporate credit | earnings, leverage changes, rating actions, defaults, spread moves, refinancing and new issuance |
| FX | central-bank decisions, inflation, employment, GDP, current-account and trade releases, rate differentials |
| Oil | EIA inventories, OPEC/OPEC+ announcements, production, refinery utilisation, curve changes, geopolitical events |
| Natural gas | EIA storage, weather forecasts, LNG flows, production, pipeline outages, curve changes |
| Gold / metals | real yields, USD, inflation, inventories, ETF flows, China and industrial data |
| Agriculture | USDA WASDE, crop progress, inventories, weather, export inspections and sales |
| Livestock | cattle-on-feed, slaughter and weights, feed costs, herd reports |
| Crypto | price and liquidity, ETF flows, funding and leverage, network activity, supply events |
| Volatility / options | IV, realised vol, skew, term structure, event proximity, positioning |
| Futures | underlying score plus curve, carry, roll yield, basis, contract expiry |
| MBS | rates, OAS, mortgage rates, prepayment data, volatility |
| REITs | earnings/AFFO, occupancy, NOI, cap rates, debt and refinancing |
| CDS | underlying credit score, spreads, rating and default events, equity deterioration |

**The equity case is the one with a free, reliable trigger.** EDGAR publishes
filings with an acceptance timestamp, and `information_latency_policy_v1`
already converts that into an actionable session. An earnings release is a
filing; the trigger is the filing's arrival, not a calendar guess.

**Forward earnings dates are a different problem.** EDGAR does not publish a
forward calendar. Companies announce dates in 8-Ks, and fiscal-period cadence
predicts them well, but "next important event" on the dashboard is an estimate
and must be labelled as one.

---

## 4. UI surfaces

**Market dashboard** — asset, price, daily move, Shaffer Score, previous score,
score change, 1M/3M/6M/12M forecast, confidence, recommended direction, Shaffer
Hedge %, next important event.

**Asset detail** — the score, the previous score, the decomposition of the
change by factor, upcoming events, the hedge ticket with its exact structure,
and alternatives.

**"What Changed?"** — biggest upgrades and downgrades, scores crossing the
bullish/bearish thresholds, earnings-driven changes, macro-driven changes,
sector changes, hedge changes, new high-confidence opportunities.

**Events** — today's earnings, Fed, CPI, EIA, USDA, auctions, OPEC.

---

## 5. What this implies for decisions already taken

**The intermediate-storage decision gets more urgent.** It was justified by ML
research value; it is also the only way the "What Changed?" page can exist. Two
independent reasons for the same build.

**Score comparability becomes user-facing.** A score that moved because a
pillar became *available* did not move for an economic reason. `score_signature`
stops the dashboard reporting a data event as a fundamental one — a
VGPD → GPD transition is not a downgrade.

**Confidence must be honest on the dashboard.** The evidence work shows a 12M
forecast is not supportable from current evidence (effective N 160.7 detects
nothing below ρ 0.155). A dashboard column labelled "12M forecast" implies a
precision the research does not have. It needs the `INSUFFICIENT_EVIDENCE`
state surfaced, not hidden behind a number.

**The hedge column depends on data that begins 2026-09-20.** Real option quotes
exist only from the archive start. A hedge recommendation is computable live;
its historical validation is not, and the UI must not imply otherwise.

---

# ADDENDUM — causal attribution (owner refinement, 2026-09-21)

## A1. "Factor effect" does not mean "the company changed"

A normalized factor moves when the BENCHMARK moves, even if the company did
nothing. Microsoft's EBITDA margin can be flat while its Shaffer EBITDA factor
improves, because the rest of its sector deteriorated. Reporting that as
"fundamentals improved" is wrong in a way a user would never catch.

So the factor effect must itself be decomposed. Four buckets for score movement:

| Bucket | Meaning | State |
|---|---|---|
| **Economic / market** | Real information changed: earnings, EBITDA growth, price, EPS, debt, rates | ordinary attribution |
| **Relative-context** | The company is similar; the comparison moved: sector benchmark, peer valuation, sector growth | ordinary attribution, separately labelled |
| **Data / comparability** | The available model changed: a pillar vanished or appeared, peer count fell below minimum | `COMPARABILITY_CHANGED` |
| **Methodology** | Shaffer itself changed: v2 → v3, new normalisation, new weight | `MODEL_VERSION_CHANGED` |

## A2. The reconciliation, corrected

The three summed terms operate **only over factors present on BOTH dates**.
Entering and exiting factors get their own terms. Mixing them double-counts an
entering factor's new weight.

```
Delta_CompanyScore = FactorEffect
                   + WeightEffect
                   + Interaction
                   + EntryEffect
                   - ExitEffect
```

For factors present on both sides:

```
w_t*F_t - w_t-1*F_t-1  =  w_t-1*Delta_F  +  F_t-1*Delta_w  +  Delta_w*Delta_F
```

## A3. Cause taxonomy, corrected

An earlier draft listed "peer set changed" as a WEIGHT cause. **That was wrong.**
A peer-set change normally moves the factor VALUE — the benchmark or percentile
it is measured against moved — at unchanged weight. It touches weight only when
it crosses a threshold.

**Weight causes** (and only these):
- factor appeared
- factor disappeared
- missingness / fallback rule applied
- eligibility threshold crossed (e.g. peer count fell below the minimum)

**Factor-value causes:**
- company primitive changed
- price changed
- peer benchmark changed
- macro input changed
- normalisation context changed (dispersion, anchor)

## A4. Decomposing the factor effect: the same counterfactual, one level down

For each factor, `Delta_F` is attributed by holding input CATEGORIES fixed and
moving one category at a time — the identical structure as the top-level split,
applied to the factor's own inputs.

```
company effect   : move company primitives to t, hold peers/macro/price at t-1
peer effect      : move the benchmark/percentile context to t, hold company at t-1
macro effect     : move macro inputs (CPI, rates) to t
market effect    : move price inputs to t
```

**Order-dependence is the trap.** A sequential walk gives a different answer
depending on which category is moved first, so "company effect" would depend on
an arbitrary implementation choice. With only 4-5 categories, the Shapley value
over category subsets is 2^k evaluations — 16 to 32 per factor per date — which
is affordable and **order-independent**. Use it, and record which method
produced the split so a later change of method is visible.

Each factor declares its input categories; `pit_derive` already knows which
primitives feed it, so the category map is a partition of the existing leaf set
rather than new metadata.

## A5. Model-version changes must stop ordinary attribution

Promoting `equity_shaffer_v2` to `v3` must never render as "Shaffer Score fell 9
points today" when the company did not change. It emits `MODEL_VERSION_CHANGED`
and day-over-day attribution **stops**:

```
Previous model (v2):  +63
New model (v3):       +54
  -9  methodology change
   0  economic change
```

Same philosophy as VGPD → GPD: a change in the measuring instrument is not a
change in the thing measured. Because `model_version` is already part of every
score row's uniqueness key, both scores coexist and the comparison is available
rather than reconstructed.

## A6. The target output

```
MSFT  +5.8 Shaffer points

  +2.4   EBITDA growth improved                  [economic]
  +1.1   stock became cheaper relative to earnings [market]
  +1.7   sector peer benchmark weakened           [relative-context]
  +0.6   sector score improved                    [relative-context]
   0.0   availability / comparability             [data]
   0.0   methodology                              [model]
  -----
  +5.8   net
```
