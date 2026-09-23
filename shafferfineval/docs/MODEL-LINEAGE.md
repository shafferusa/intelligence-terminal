# Model Lineage — the Shaffer Framework and its successors

**Status:** authoritative. This file is the permanent, human-readable record of what
each Shaffer model version *is*, who authored it, what it superseded, and what it may
be called. The machine-readable twin of this file is the `pit_model_lineage` table in
the research store (`pit_store.py`), seeded by `pit_lineage_seed.py`.

---

## 0. The originating invention

The **Shaffer Score** is a human-designed scoring framework. It was specified by its
author before any machine learning existed in this project: the factors, their weights,
the transforms, the peer construction and the missing-data rule were all chosen by a
person and written down as equations.

Every `*_v1` formula in this repository is therefore an **initial production
hypothesis** — a human claim about what makes an asset attractive, stated precisely
enough to be tested and precisely enough to be wrong. The v1 numbers are not fitted
parameters. Nothing estimated them. That is exactly why they are testable: a fitted
parameter can only be re-fitted, but an authored hypothesis can be confirmed,
contradicted or improved on the record.

Machine learning entered this project afterwards and for one purpose: to **test,
validate, calibrate and propose improvements to** the human framework. It was never
added to erase it, replace it silently, or rebrand its output.

### The permanent lineage

    Shaffer Human Framework  ->  Evidence  ->  ML Challenger  ->  Human Review  ->  New Shaffer Version
                ^                                                                          |
                +--------------------------  successor, never overwrite  -----------------+

Every arrow is one-way and every box is recorded. A challenger cannot skip Human
Review. A new version never edits the version it descends from.

---

## 1. Registered model versions and their authored specifications

Each subsection below is the exact specification as authored, transcribed from the
code that implements it. Where a number appears here it also appears as a named
constant in the file cited — these are documentation of the code, not a parallel
description that can drift from it.

### 1.1 `equity_model_v1` — the equity Shaffer Score

**Source of record:** `company_scoring.py` (arithmetic), `scoring.py`
(classification), `storage.py` (the version string stamped on every score row).

**Naming note.** The same human equation carries two version strings in the codebase
for historical reasons: `storage.EQUITY_MODEL_VERSION = "equity_model_v1"` is what is
stamped on live score rows, and `asset_models.EQUITY.version = "equity_shaffer_v1"` is
the label on the same equation inside the multi-asset model catalogue. They are one
model. `equity_model_v1` is the canonical lineage id; `equity_shaffer_v1` is recorded
as its alias. The PIT research twin (§2) descends from this model under either name.

**Master equation** (`company_scoring.MAJOR_WEIGHTS`):

    CompanyRawScore = 0.40 V + 0.25 G + 0.20 P + 0.15 D     clamped [-100, +100]

**Sub-equations:**

| Factor | Weight | Sub-weights (constant) |
|---|---|---|
| V — Valuation | 0.40 | single term, see below |
| G — Growth | 0.25 | `GROWTH_WEIGHTS`: revenue_growth 0.45, revenue_acceleration 0.35, ebitda_growth 0.20 |
| P — Profitability | 0.20 | `PROFITABILITY_WEIGHTS`: ebitda_margin 0.65, roa 0.35 |
| D — Debt strength | 0.15 | `DEBT_WEIGHTS`: net_debt_ebitda 0.60, debt_market_cap 0.40 |

Both debt components are scored **lower-is-better**
(`COMPONENT_HIGHER_IS_BETTER`); all five other components are higher-is-better.

**Company scaling** (`COMPANY_SCALE = 0.75`):

    CompanyScore = 0.75 x CompanyRawScore                   clamped [-75, +75]
    FinalEquityScore = CompanyScore + SectorOverlay          clamped [-100, +100]

The 0.75 is the authored headroom reservation: the company equation is deliberately
capped at ±75 so the sector overlay (±25, §1.2) has room to move the final score
without either term being able to saturate the other out of existence. A missing
overlay contributes 0 rather than blocking the score, and the caller is required to
say the sector environment was unavailable.

**Valuation transform** (`VALUATION_TANH_K = 2.0`):

    V = 100 x tanh(2 x ValuationGap)                        clamped [-100, +100]

where the gap chain is

    EV                 = MarketCap + TotalDebt - Cash
    BenchmarkEVEBITDA  = winsorized mean EV/EBITDA of the EBITDA cohort
    ImpliedEV          = CompanyEBITDA x BenchmarkEVEBITDA
    ImpliedEquityValue = ImpliedEV - CompanyDebt + CompanyCash
    ImpliedPrice       = ImpliedEquityValue / SharesOutstanding
    ValuationGap       = (ImpliedPrice - CurrentPrice) / CurrentPrice

tanh is the authored choice specifically so an extreme gap saturates rather than
dominating: +25% gap scores about +46, +50% about +76, and the curve flattens from
there. A linear map would let one mispriced input swamp the other three factors.

**Peer-percentile normalisation.** Every component except valuation is scored by
percentile rank against peers in the **same industry**, never by raw magnitude:

    p     = percentile position of the company inside peers + itself, in [0, 1]
            (average ranks, ties shared — statlib.percentile_rank_within)
    score = 200p - 100        higher-is-better
    score = 100 - 200p        lower-is-better

A lone company has no percentile and is scored `None`, never 0.5 — a fabricated
middle is the failure this rule exists to prevent.

**Peer construction** (the parts that decide *who* you are measured against):

- `MIN_INDUSTRY_PEERS = 5` — below five same-industry peers the benchmark widens to
  the whole sector and is labelled `Sector Fallback`. The fallback is always labelled,
  never silent.
- `MIN_EBITDA_COHORT = 3` — the minimum size for a usable cohort, and also the
  minimum peer count before a single component may be ranked at all.
- The **ThirdQuartilePeerSet**: the EBITDA cohort is peers whose EBITDA sits in the
  **50th–75th percentile** — deliberately that band, not the top quartile.
- `MIN_VALID_EV_EBITDA = 0.1`, `MAX_VALID_EV_EBITDA = 300.0` — a multiple outside this
  band is not an economically usable observation and is excluded.
- Winsorisation at the 5th/95th percentile, count-based with `MIN_WINSOR_TRIM = 1`
  (`statlib.winsorize`), so a single abnormal peer cannot drag the benchmark mean.

**Missing-data rule — drop and renormalise.** An unavailable factor is *dropped*, and
the remaining weights are renormalised over what survived:

    used[k] = MAJOR_WEIGHTS[k] / sum(MAJOR_WEIGHTS[j] for j available)

The same rule applies inside each sub-equation. Two consequences are load-bearing:

1. A score computed with valuation unavailable is a **different model state**
   (0.4167 / 0.3333 / 0.25 over the remaining three) and must be recorded as such —
   which is why `pit_score.effective_weights_json` is mandatory, not decorative.
2. Nothing is imputed. No factor is ever filled with a peer median, a zero or a
   carried-forward value to keep the weights tidy.

If no factor is available, the company score is `None`. It is never 0.

**Classification** (`scoring.BAND_THRESHOLDS`):

    +40 to +100  BULLISH
      0 to  +40  SEMI-BULLISH
    -40 to    0  SEMI-BEARISH
    -100 to -40  BEARISH

### 1.2 `sector_model_v1` — the sector overlay

**Source of record:** `sector_scoring.py`.

Four factors, each normalised to [-100, +100] by **percentile rank across sectors**
(`SECTOR_FACTOR_WEIGHTS`):

| Factor | Weight | Direction |
|---|---|---|
| G — growth acceleration vs VTI | 0.40 | higher is better |
| ROE — winsorized mean ROE | 0.21 | higher is better |
| ROA — winsorized mean ROA | 0.14 | higher is better |
| D — winsorized mean Debt/MarketCap | 0.25 | **lower** is better |

    SectorRawScore = 0.40 G + 0.21 ROE + 0.14 ROA + 0.25 D    clamped [-100, +100]
    SectorOverlay  = SectorRawScore / 4                       clamped [-25, +25]

**The /4 overlay divisor** (`OVERLAY_DIVISOR = 4.0`) is the authored statement of how
much a sector may move a company: at most ±25 points against a company term capped at
±75. Sector context tilts the verdict; it never delivers it. `SectorOverlay` is the
only number the company model consumes — the company formula itself is untouched by
this engine.

Growth acceleration is the sector's own acceleration minus VTI's, on market-cap
weighted returns over a `RECENT_WINDOW = 63` session window against a
`LOOKBACK_WINDOW = 126` prior window, requiring `MIN_PRICE_HISTORY = 127` sessions.

Guards: `MIN_COMPANIES_PER_FACTOR = 5` before a factor is trustworthy,
`MIN_ELIGIBLE_COMPANIES = 3` before a sector is scoreable at all, and
`MIN_FACTORS_FOR_SCORE = 2` — below two available factors the score is withheld rather
than computed from one term. Same drop-and-renormalise rule as §1.1.

### 1.3 `return_calibration_v1_0.20` — the score-to-return mapping

**Source of record:** `prediction.py` (`V1_SLOPE = 0.20`, `V1_INTERCEPT = 0.0`).

    PredictedReturnPct = 0.00 + 0.20 x ShafferScore      (score in -100..+100)
    PredictedPrice     = CurrentPrice x (1 + PredictedReturnPct / 100)

So +100 -> +20%, +50 -> +10%, 0 -> 0%, -100 -> -20%. The horizon is 12 months
(`PRIMARY_HORIZON = "12M"`, 252 sessions), and the quantity is an **absolute price
return from today's market price** — not excess return versus VTI, not total return
including dividends, not an analyst target, and not the peer-implied value from the
valuation factor. Those are different numbers answering different questions and are
never relabelled as each other.

This calibration is deliberately stored **separately from the score**, carrying its own
version id, in both stores (`storage.RETURN_CALIBRATION_VERSION`,
`pit_store.RETURN_CALIBRATION_VERSION`, `pit_score_prediction.calibration_version`).
The reason is lineage: research must be able to ask whether 0.20 was ever the right
slope without the answer being baked into the historical score record.

The 0.20 is a transparent placeholder, and is labelled as one in the product
(`UNCALIBRATED_NOTE`). It is the most obviously testable claim in the framework and
the single most likely first target of a validated challenger. Until one is promoted
by a human, 0.20 is what the terminal says.

### 1.4 `hedge_model_v1` — the Shaffer Hedge

**Source of record:** `hedging.py`, with the approved strategy universe in
`strategy_catalog.py`.

**Hedge arithmetic:**

    AdverseScore = max(0, -d x FinalEquityScore)        d = +1 long, -1 short
    HedgeRatio   = clamp((AdverseScore - 15) / 85, 0, 1)
    HedgedShares = abs(Shares) x HedgeRatio

`HEDGE_FLOOR = 15.0` and `HEDGE_SPAN = 85.0` are the authored claim that mild
disagreement is not worth paying for: below an adverse score of 15 the model asks for
no hedge at all, and the ramp reaches a full hedge only at 100. Only a position facing
*against* its score is adverse — a long with a positive score has nothing to hedge.

**Seven-factor strategy score** (`STRATEGY_WEIGHTS`):

| Factor | Weight |
|---|---|
| effectiveness | 0.30 |
| cost | 0.20 |
| upside | 0.15 |
| liquidity | 0.10 |
| capital | 0.10 |
| tenor | 0.10 |
| basis | 0.05 |

Same drop-and-renormalise rule as §1.1. A strategy whose effectiveness cannot be
measured is excluded rather than scored on the other six.

**Sizing and eligibility constants:** `CONTRACT_MULTIPLIER = 100`;
expiry targeting `TARGET_DTE = 90` inside `MIN_DTE = 60` .. `MAX_DTE = 120`;
`STRESS_MOVE = 0.25` with `MIN_HEDGE_PAYOFF = 0.01` (a structure must actually pay at
the stress point to count as a hedge — eligibility is by payoff, not by name);
`MAX_OTM_PCT = 0.15` for dynamic strike selection; default protective offsets
`PUT -7.0%`, `CALL +10.0%`, applied **only** to a leg sitting at its group default, so
a deliberately different strike stays the product's identity rather than collapsing
every put strategy into one.

### 1.5 The asset-class v1 models

**Source of record:** `asset_models.py`. Nineteen authored equations, one per asset
class, sharing the `blend()` machinery, the [-100, +100] range, the
drop-and-renormalise rule and the deterministic confidence classifier
(`classify_confidence`: HIGH at coverage ≥ 0.85 with ≥ 3 terms, MEDIUM at ≥ 0.55 with
≥ 2, otherwise LOW).

| Version | Asset class | Top-level factors and weights | Scale |
|---|---|---|---|
| `equity_shaffer_v1` | Common equity | valuation 0.40, growth 0.25, profitability 0.20, debt 0.15 | 0.75 |
| `reit_shaffer_v1` | REIT | valuation 0.35, growth 0.25, quality 0.20, debt 0.20 (all nested) | 1.0 |
| `preferred_shaffer_v1` | Preferred stock | credit 0.35, yield_spread 0.30, rates 0.20, call_risk 0.15 | 1.0 |
| `etf_shaffer_v1` | ETF / index basket | underlying 0.80, breadth 0.20 (nested) | 1.0 |
| `rates_shaffer_v1` | Government bond | central_bank 0.27, inflation 0.22, growth 0.18, yield_level 0.13, policy 0.12, curve 0.08 | 1.0 |
| `corp_credit_shaffer_v1` | Corporate bond | credit 0.32, spread 0.23, rates 0.18, gpi 0.10, cash_flow 0.09, technical 0.08 | 1.0 |
| `mbs_shaffer_v1` | Agency MBS | oas 0.30, rates 0.25, prepayment 0.20, volatility 0.15, carry 0.10 | 1.0 |
| `structured_shaffer_v1` | Structured credit | spread 0.30, collateral 0.25, coverage 0.20, structure 0.15, liquidity 0.10 | 1.0 |
| `fx_shaffer_v1` | FX pair | real_rate 0.25, central_bank 0.20, gpi 0.17, growth 0.13, current_account 0.09, valuation 0.08, carry 0.08 | 1.0 |
| `oil_shaffer_v1` | Crude oil | inventories 0.22, supply 0.17, demand 0.17, gpi 0.17, curve 0.12, refining 0.08, usd 0.07 | 1.0 |
| `natgas_shaffer_v1` | Natural gas | storage 0.25, weather 0.17, supply 0.17, gpi 0.15, lng 0.13, curve 0.08, demand 0.05 | 1.0 |
| `gold_shaffer_v1` | Gold / precious metals | real_yield 0.27, usd 0.18, flows 0.13, inflation 0.13, gpi 0.12, risk 0.09, momentum 0.08 | 1.0 |
| `industrial_metal_shaffer_v1` | Industrial metal | pmi 0.23, inventory 0.18, supply 0.17, china 0.13, gpi 0.12, usd 0.09, curve 0.08 | 1.0 |
| `ag_shaffer_v1` | Agriculture | stocks_use 0.27, weather 0.22, production 0.18, exports 0.13, gpi 0.12, curve 0.08 | 1.0 |
| `livestock_shaffer_v1` | Livestock | herd 0.25, feed 0.20, slaughter 0.20, demand 0.15, curve 0.10, seasonality 0.10 | 1.0 |
| `crypto_shaffer_v1` | Crypto | liquidity 0.25, momentum 0.20, flows 0.15, network 0.15, leverage 0.15, supply 0.10 | 1.0 |
| `vol_shaffer_v1` | Volatility product | vol_value 0.30, curve 0.25, stress 0.20, vol_of_vol 0.15, positioning 0.10 | 1.0 |
| `futures_shaffer_v1` | Futures contract | underlying 0.85, carry_curve 0.10, liquidity 0.05 | 1.0 |
| `option_shaffer_v1` | Listed option | direction 0.55, vol_value 0.25, theta 0.10, liquidity 0.10 | 1.0 |

Equity is the only model carrying a scale other than 1.0, because it is the only one
that takes a sector overlay on top.

**Six derivative overlays.** These *inherit* an underlying score rather than running an
independent economic model, so a derivative can never contradict its own underlying:

| Overlay | Version | Equation |
|---|---|---|
| CDS | `cds_shaffer_v1` | BuyProtection = −CreditStrength + RelativeValueOverlay; SellProtection = −BuyProtection + CarryOverlay |
| Total return swap | `trs_shaffer_v1` | ReceiveTRS = Underlying − CarryCost; PayTRS = −Underlying − CarryCost (cost paid on both sides) |
| Interest rate swap | `irs_shaffer_v1` | ReceiveFixed = RatesScore + Overlay; PayFixed = −RatesScore + Overlay |
| FX forward / NDF | `fx_forward_shaffer_v1` | 0.85 × FXScore + 0.15 × ForwardValue (renormalised to FX alone when forward value is missing) |
| Swaption | `option_shaffer_v1` | receiver = +RatesScore, payer = −RatesScore, then through the listed-option equation |
| Option direction | (the D term of `option_shaffer_v1`) | call = +UnderlyingScore, put = −UnderlyingScore |

### 1.6 `gpi_v1` — the geopolitical overlay

**Source of record:** `political.py` (`GPI_VERSION = "gpi_v1"`). A separately versioned
overlay with its own severity weights, exposure weights and event half-lives, bounded
by `OVERLAY_DEFAULT_CAP = 10.0` and a hard `OVERLAY_MAX_CAP = 15.0`. It is recorded as
its own lineage entry rather than folded into the equity model, because it is a
separate human hypothesis with a separate testable claim.

---

## 2. `equity_shaffer_v1` is not `equity_shaffer_v1_pit`

**Source of record:** `pit_store.py` (`EQUITY_PIT_MODEL_VERSION`), `pit_policy.py`.

The **Historical PIT Twin** (`equity_shaffer_v1_pit`) applies the *same human-authored
equation* — the same 0.40/0.25/0.20/0.15, the same 0.75, the same tanh, the same
drop-and-renormalise — to a historically honest reconstruction of what was knowable on
a past date. It will not reproduce the production number, and it is not supposed to.
The four structural reasons:

1. **EBITDA is assembled, not supplied.** Production reads a vendor's EBITDA field.
   The twin assembles EBITDA from XBRL tags under a versioned concept ladder, often
   across two filings with different accession numbers.
2. **Facts are point-in-time, not current.** The twin selects the latest vintage a
   reader could have seen on the as-of date, so a later restatement is invisible to it.
   Production sees today's restated values.
3. **Peers are point-in-time.** The twin builds the cohort from the industry
   classification each issuer carried *at filing time*, from a universe that includes
   companies which have since died. Production ranks against today's survivors.
4. **Identity is historical.** The twin is keyed on CIK-backed entities and listings
   disambiguated by first trade date. Production is keyed on today's ticker.

**The gap between the two is research evidence, not a defect to be engineered away.**
It is a measurement of how much of the production score comes from the equation and
how much comes from surviving, restated, vendor-normalised data. Closing that gap by
loosening the twin's point-in-time discipline would destroy the only instrument that
can measure it. The twin is therefore never "fixed" to agree with production, and
production is never adjusted to agree with the twin. They are two model versions, kept
side by side, and diffed.

The twin's authorship is **`human`** — it implements a human equation — and its status
is **`RESEARCH`**. It is not a challenger. It is the honest historical baseline that
challengers must beat.

---

## 3. Naming and status rules

These rules exist so that a reader of any output, at any time, can tell whether they
are looking at the invention or at an experiment about the invention.

**Only an explicitly promoted production model may be called "Shaffer Score" or
"Shaffer Hedge."** Those names refer to the human framework in production. They are not
generic words for "the number this system produced."

Research output must be labelled with one of:

| Label | Means |
|---|---|
| **ML Challenger** | A fitted model competing against the human framework on the same evidence. |
| **Research Model** | Any non-promoted model, fitted or authored, not in production. |
| **Experimental Calibration** | A proposed mapping (e.g. a slope other than 0.20) under test. |
| **Historical PIT Twin** | The human equation replayed on point-in-time data (§2). |
| **Candidate Shaffer v2** | A proposal that has passed review and is awaiting or undergoing human approval. |

**Status values** (`pit_store` / `storage`, identical vocabulary in both):

    RESEARCH -> CHALLENGER -> VALIDATED_CHALLENGER -> PRODUCTION -> RETIRED

`RESEARCH` is the default for everything registered. Nothing arrives at `PRODUCTION`
by training, by scheduling, by a metric threshold, or by any code path that runs
unattended. The ML lab's own registry defaults every model to `RESEARCH`, and the
production score-to-return mapping remains the human V1 calibration until a model is
explicitly promoted.

Prohibited, permanently:

- Presenting a challenger's output under the name "Shaffer Score."
- Substituting a challenger for the production model at runtime because it scored
  better on a backtest.
- Pooling production rows and PIT rows into one series — they are different model
  versions computed from different sources and are never averaged, joined or charted
  as one line.
- Rewriting a historical score, prediction or snapshot under a newer formula.

---

## 4. The promotion process

    challenger  ->  proposal  ->  human approval  ->  NEW version  ->  predecessor retained

1. **A challenger produces evidence.** It runs against a recorded fold set
   (`ml_fold`, with explicit purge and embargo), on a recorded replay run, and its
   per-fold metrics are stored (`ml_fold_metric`) beside the baseline's.

2. **A challenger becomes a proposal.** Evidence alone changes nothing. A proposal is
   a row in `pit_proposal` carrying: the target model, the claim, the rationale, the
   **current** spec and the **proposed** spec side by side, the replay run and fold set
   it rests on, its metrics and the baseline's, the effect size, the effective sample
   size, how many folds improved out of how many, **how many hypotheses were tested**,
   and its **known weaknesses**. A proposal that cannot state its weaknesses or its
   multiple-testing count is not a proposal.

3. **A proposal needs explicit human approval.** The reviewer is a person. The decision
   (`decided_by`, `decided_at`, `decision_note`) is recorded whether it is yes or no.
   There is no automatic approval, no quorum of metrics, no threshold that promotes on
   its own. `record_promotion()` is the only way a model's status changes, and nothing
   in any training or refresh pipeline calls it.

4. **Approval creates a NEW version.** It never edits an existing one. A promoted
   change to the equity weights produces `equity_model_v2` with
   `predecessor_version = 'equity_model_v1'`, its own row, its own authored spec, and
   its own `created_date`. Authorship is recorded honestly: `human` where a person
   chose the change, `hybrid` where a person adopted a challenger's suggestion,
   `ml_challenger` where the specification is the fitted model itself.

5. **The predecessor is never rewritten or migrated.** `equity_model_v1` keeps its row,
   its constants and its status history forever. Rows it produced keep its version
   string and are never recomputed under the successor's formula. A promotion moves
   the predecessor to `RETIRED`; it does not delete, edit, backfill or reinterpret it.
   Retired does not mean wrong — it means superseded, and it stays readable.

Every status transition is an audit row in `pit_promotion`: from-status, to-status, the
proposal it rests on, the evidence, who promoted it, when, and why.

---

## 5. Every version stays reproducible forever

A model version that cannot be re-run is not evidence, so reproducibility is a property
of the record rather than a practice:

- **The specification is stored, not just referenced.** `pit_model_lineage` holds each
  version's factor definitions, weights, transformations, missing-data rule,
  normalisation, overlays, calibration version and hedge rules as JSON. Reading the row
  is enough to reconstruct the equation without the code.
- **Policies are versioned and never edited in place.** `LATENCY_POLICY_VERSION`,
  `LADDER_VERSION`, `PEER_SET_VERSION` and `RETURN_CALIBRATION_VERSION` are stamped on
  every row they produced. Changing a cutoff, a tag order or a peer rule means adding
  a v2 beside the v1, not amending it.
- **Raw observations are append-only.** A restatement is a new row with its own
  accession and availability date — enforced by database triggers, not convention.
- **Replays are runs, not overwrites.** Features and scores are immutable per run;
  re-scoring under a corrected replay means a new `run_id`, so both stay side by side
  and can be diffed.
- **Effective weights travel with every score.** A score computed with a factor missing
  records the weights that actually applied, so the model *state* is recoverable and
  not merely inferable.
- **Live production history is immutable too.** A close snapshot already written for
  an (asset, date, model version) is never rewritten, and a historical prediction keeps
  the model that produced it.

---

## 6. Where the record lives

| Record | Location |
|---|---|
| This document | `docs/MODEL-LINEAGE.md` |
| Machine-readable lineage | `pit_model_lineage` (research store) |
| Seeding of the production versions | `pit_lineage_seed.py` |
| Round-trip verification | `test_pit_lineage.py` |
| Status changes (audit) | `pit_promotion` |
| Proposals and decisions | `pit_proposal` |
| Live production scores | `storage.py` — `score_history`, immutable per close |
| Historical replay scores | `pit_store.py` — `pit_score`, immutable per run |

The production store and the research store are separate database files and neither
writes to the other's tables. That separation is what makes it structurally impossible
for a research run to alter what the terminal said on a past day.

---

# ADDENDUM — the first baseline is named for its limitation (2026-09-21)

## The decision

The first historical baseline is frozen as:

```
equity_shaffer_v1_pit_SURVIVOR_ONLY_DIAGNOSTIC
```

The limitation goes in the **model name**, not in a footnote.

## Why the name and not a note

`model_version` is part of the uniqueness key on every `pit_score`, `pit_feature`
and `pit_pillar_score` row. Putting the limitation in the name means it travels
with **every single row, forever**, and cannot be separated from the data by
copying a number into a slide. A footnote is detachable; a primary key is not.

Three further consequences fall out of it:

- The attribution engine already refuses to compare across model versions
  (`MODEL_VERSION_CHANGED`), so this baseline can never be silently diffed
  against a later proper one — which is correct, because they *are* different
  models measured on different populations.
- When survivorship-free prices arrive, the proper baseline is frozen as its own
  version and **both coexist in the same table**. The survivorship question then
  becomes a measurable delta rather than an argument.
- Any quote of a performance number carries the name, so "Shaffer scored X
  historically" cannot be said without saying which model said it.

## What this baseline MAY establish

- whether scores rank surviving stocks sensibly
- whether factors behave as designed
- whether signatures matter
- whether short horizons show signal
- whether the replay machinery works at all

## What it may NOT be used for

- the claim "Shaffer Score historically predicts stocks"
- promoting a production challenger
- any performance figure quoted without the model name attached

The governing distinction: **"the model is ready to calculate" is not "the model
has been validly proven."** Freezing this baseline settles the first and says
nothing about the second.

---

# ADDENDUM — earnings sign-cross taxonomy

61.35% of entities cross zero at some point, so this is a majority behaviour, not
an edge case. `-$0.10 -> +$0.10` is one of the most meaningful improvements a
business can make, and ordinary percentage growth renders it as **-200%** —
pointing the wrong way.

| Transition | Treatment |
|---|---|
| positive → positive | growth may be calculated |
| negative → positive | `EARNINGS_SIGN_CROSS_POSITIVE` |
| positive → negative | `EARNINGS_SIGN_CROSS_NEGATIVE` |
| negative → negative | no conventional P/E-growth interpretation |

**No percentage is ever emitted for a sign-cross event.** Absolute, and tested.

---

# ADDENDUM — the P/E absolute anchor

The first v2 anchor is deliberately **simple, fixed, transparent and explicitly
arbitrary**, rather than burying another complex assumption inside the first
version. It is then immediately an ML challenger in its own right.

Candidate anchors for later research: a fixed P/E; the long-run market P/E; the
sector's own history; a rate-adjusted valuation; or a nonlinear combination.

A more economically grounded future challenger, recorded as RESEARCH and
deliberately **not** in the first v2:

```
EarningsYield        = 1 / (P/E)
EarningsYieldSpread  = 1 / (P/E) - Y_Treasury
```

This asks whether the equity earnings yield compensates the holder relative to
bonds, and it naturally moves when rates move. It is elegant, which is exactly
why it must not be smuggled into the first specification on the strength of
sounding right.

---

# ADDENDUM — "observed-regime stability", not "regime robustness"

```
survived the regimes we observed  ≠  proven robust to regimes we never observed
```

The macro history reaches 1990; the SCOREABLE equity universe starts 2013. So
regime-conditional testing is bounded by the equity store, not the macro store,
and 2013-2026 contains exactly ONE full modern rate-cycle sequence — ZIRP,
taper, gradual hiking, inversion, COVID cuts, the inflation surge, rapid hiking,
stabilisation. Genuine variety; one cycle.

## Required fields on every Shaffer improvement proposal

| Field | For the equity model today |
|---|---|
| Scoreable test period | 2013–2026 |
| Regime-definition history | 1990–2026 |
| Observed regimes represented | whatever the macro classifier identifies inside 2013–2026 |
| **Unobserved major environments** | 2008-style systemic credit crisis; prolonged 1970s-style inflation; others named explicitly |

## Statuses

A challenger may earn:

```
OBSERVED_REGIME_STABLE
```

which means: **stable across the distinct macro environments represented within
the scoreable equity period.** It must NEVER be read as, or rendered as:

```
STRUCTURALLY_ROBUST_ACROSS_MARKET_HISTORY
```

## The additional warning for rate-sensitive features

A feature keyed to the Treasury yield — `1/(P/E) − Y_10Y` being the clearest
case — moves with a series that traces ONE arc in this store. A good backtest
can therefore partly reflect that exact path while looking universal. Such a
feature carries, even after passing the ordinary tests:

```
RATE_REGIME_GENERALIZATION_UNPROVEN
```

This is the same failure mode as the AI-Bubble cohort labels being perfectly
confounded with fiscal year, only subtler: the confound is a monetary era rather
than a calendar year.

## Why the regime labels come from 1990 macro

Defining regimes from 1990–2026 does not create equity outcomes we do not have.
It removes one layer of circularity: the regime boundaries are not fitted to the
same 13 years used to judge the models inside them.

---

# ADDENDUM — owner decisions 1, 2 and 3 of 2026-09-21

Recorded after the valuation gate surfaced a price/share-basis conflict. The
significance of the timing: `pit_feature`, `pit_score` and `pit_replay_run` were
all still at 0 rows, so no wrong number was ever written. Without this, the
replay would have produced a **completely clean, perfectly reproducible,
economically false** P/E and EPS-growth history.

## Decision 1 — the anchor stays at 20x, as a convention

```
PE_ABSOLUTE_ANCHOR_V1 = 20.0
```

A **policy convention**, justified by continuity with the already-published
calibration (10.14 / 25.00 / 2.47x were struck at parity = 20). **Not** an
empirically discovered fair multiple, and the code says so.

The semantic statement, which must never be rendered as a fair-value claim:

```
P/E = 20x  =>  PE_absolute = 0
```

is about **where neutral sits on this scale**. The anchor is rate-blind by
design, so it will deliberately read broad market multiple expansion as
increasing expensiveness — including expansion a falling discount rate
justifies. Anything built on it carries `RATE_REGIME_GENERALIZATION_UNPROVEN`.

**The sweep {14, 16, 18, 20, 22} is a sensitivity diagnostic, not an optimiser.**
It reports median-issuer sign by census date, the share of observations whose
sign changes, and score-level displacement. Ranking anchors by realized return
is forbidden in writing, because the anchor cannot change within-date ordering —
fitting it against survivor-only returns would be choosing the **zero point** of
the valuation scale using contaminated evidence that points in a known direction
(median survivor −7.45% at 12M). The anchor becomes an explicit early ML
challenger once a dead-company-inclusive priced universe exists.

## Decision 2 — two price concepts, never interchangeable

```
raw / as-traded price        ->  VALUATION
split/action-adjusted price  ->  RETURNS and LABELS
```

Different primitives, different names, and `pit_price_basis.assert_price_basis`
raises a `TypeError` on a swap in either direction — the wrong *kind* of thing,
not a bad value.

**The refinement that makes it correct on both sides of a split:** EPS is carried
to the **score-date share basis**, using only corporate actions with
`event_date <= as_of`. A future split must never back-adjust a historical P/E.
This is exactly why a fully adjusted historical series is dangerous here — it is
dangerous *because* it is complete.

```
(a) ONE DATE, TWO BASES — 2020-01-31
      as traded       309.51     / 11.89  =  26.03
      back-adjusted   309.51 / 4 / 11.89  =   6.51    same day, EXACTLY 4x apart

(b) TWO DATES ACROSS THE SPLIT, both raw
      2020-01-31       309.51 / 11.89        =  26.03
      post-split        77.00 / 11.89        =   6.48   EPS left in its own era
      post-split        77.00 / (11.89 / 4)  =  25.90   EPS carried to score date
```

All three valuation subfactors now declare `price_raw_as_traded` — including
EV/EBITDA, which previously named `price_adjusted` and would have carried the
same error into market cap.

## Decision 3 — the EPS pair hierarchy, and classify only after normalising

```
1. same-filing comparative pair   PREFERRED
2. different-filing pair          harmonise via PIT-safe corporate actions
3. unresolved                     REFUSED_SHARE_BASIS_UNKNOWN
```

Rung 1 is the primary path, not a corner case — measured over the whole store:

| | count | share |
|---|---|---|
| `(entity, accn, qtrs)` groups with ≥2 period_ends | 179,130 / 180,599 | **99.19%** |
| annual filings carrying a comparative | 28,364 / 28,734 | **98.71%** |
| entities with ≥1 same-filing pair | 2,395 / 2,411 | **99.34%** |

**The premise is proven, not assumed.** Apple's own filings:

```
filed 2019-10-31  10-K  FY2019  diluted  11.89
filed 2020-10-30  10-K  FY2019  diluted   2.97   <- the issuer's own restatement
filed 2020-10-30  10-K  FY2020  diluted   3.28
```

```
rung 1  same-filing comparative     +10.4377%
rung 2  cross-filing, harmonised    +10.3448%
rung 3  naive cross-filing pair     -72.4138%   FABRICATED
```

Rung 1 beats rung 2 by 9.29 bp because the issuer rounded 11.89/4 = 2.9725 to
2.97 — rung 1 is their restatement at their precision; rung 2 is our arithmetic
on their pre-split figure.

**Why the ordering rule is load-bearing.** 3.28 and 11.89 are both strictly
positive, so the naive pair classifies as `both_positive`, permits a percentage,
and prints −72.41% for a company that grew EPS 10%. No sign cross, no zero, no
near-zero base. **Nothing downstream of the classifier could have caught it** —
which is why the fence sits upstream. `eps_transition_from_pair()` is now the
only supported entry point.

Two refusal states were added because the classifier was conflating them:

| state | means |
|---|---|
| `endpoint_missing` | one figure absent — previously mislabelled `from_reported_zero`, contradicting the function's own refusal text |
| `share_basis_unresolved` | both present, not shown to share a basis — forbids percentage **and level and yield** arithmetic |

## The named convention

```
EPS_GROWTH_MIN_POSITIVE_BASE_V1 = 0.01
```

Rationale is **reporting precision**, not economic significance: per-share
amounts are reported to the cent, so a base at that quantum makes the ratio a
statement about rounding. $0.005 → $0.50 prints +9,900%; one further cent of
prior earnings halves it to +4,850%.

## What still binds

TTM constructions assembled **across** filings are not same-filing pairs, so the
corporate-action table remains directly binding there. And that table is
`inferred`, Yahoo-sourced, survivor-only: 1,270 splits across 1,903 of 2,576
listings. The 673 listings with no action rows read as `no_events_recorded`,
which is **refused**, not assumed clean.

---

# ADDENDUM — the coverage contract (owner invariant, 2026-09-21)

```
                  eligible observations with a usable value
    Coverage  =  -------------------------------------------
                    explicitly defined eligible population
```

**Both halves are required. A percentage published without both definitions is
invalid — not weak, invalid.**

Enforced in `pit_coverage_contract.py` as a **constructor precondition**, not a
review checklist: `CoverageClaim` cannot be built without both definitions, so an
undefined figure cannot reach the registry to be quoted from. A definition of
fewer than three words is refused as a label rather than a definition — "all
entities" does not say which observations qualify or by what rule.

## Why the denominator is the dangerous half

Hold the numerator fixed at 272,587 and change only the denominator:

| denominator | count | reads |
|---|---|---|
| peer entity-dates | 1,238,663 | 22.01% |
| priced entity-dates | 377,304 | **72.25%** |
| entity-dates with any in-scope count | 297,868 | 91.51% |

**69.51 points of spread from a choice that asks no question about share data at
all** — against 18.54 for the staleness rule, 24.14 for the entire ceiling range
and 1.84 for the guard. The denominator moved the figure by more than every rule
about the data combined.

## Units are part of the denominator

`listing_rows` is refused **by name**, carrying its measurement: `scored_universe_as_of`
returns 2,065 / 2,455 / 2,475 rows against 2,034 / 2,423 / 2,443 entities, and two
modules quoted different sides of that into ratios. A unit error, not a rounding
one. `rows` and `records` are refused as too vague to be units.

## Existence is not coverage

The EPS same-filing figure is registered as an **existence** claim and says so:
99.34% of entities have at least one such pair; the share of entity-**dates** with
a usable pair is a different number and is **UNKNOWN**. Registering the first
without that note would have been the `dei:TradingSymbol` mistake again — a real
measurement of one thing wearing another thing's label.

## What the registry does not claim

13 claims registered, every one **adapted** from the module that measured it —
nothing re-typed, so a figure cannot drift between its owner and the registry.
`audit()` scans 130 files and reports **75 candidates**, explicitly labelled
`CANDIDATES, NOT A COMPLETENESS CLAIM`, with the false-positive expectation stated
up front. An auditor that implied completeness would be making exactly the
undefended claim the contract refuses.

---

# FREEZE — `equity_shaffer_v2_pit_SURVIVOR_ONLY_DIAGNOSTIC`, 2026-09-21

```
digest      7a844dca245cfb37ea6722d8e34a9d9407f82e2210ec17f62605aa8ab40f7843
components  27
at freeze   pit_feature 0 | pit_score 0 | pit_replay_run 0
```

The survivorship limitation is **in the model version string**, so it cannot be
separated from a row, lost in a join, or dropped from a chart legend.

## What this freeze means, and what it does not

It means the economic definitions, PIT rules, factor eligibility, price/share
basis, missingness behaviour, provenance handling, peer coherence and coverage
definitions are sufficiently specified to **calculate the first diagnostic
replay**.

It does **not** mean the model is historically validated, that any Shaffer Score
has been shown to predict anything, or that survivor-only results may promote
anything. The specification is **fixed**, which is a different property from
finished: a successor is a new version beside it, never an edit of it.

## A freeze that cannot detect its own violation is not a freeze

`pit_frozen_spec.COMPONENTS` names 27 policy versions and calibrated constants,
read live from their owning modules; `digest()` hashes them; `verify()`
recomputes and locates drift per component. Proven in test:

```
P/E anchor 20.0 -> 20.5                        intact=False
  drifted: pit_valuation_spec:PE_ABSOLUTE_ANCHOR_V1  20.0 -> 20.5

debt rung REJECTED_MEASURED -> ELIGIBLE        intact=False
  drifted: pit_valuation_spec:DEBT_RUNG_EVEBITDA_V1
```

The second case is the one a version-string-only freeze would miss: promoting a
rejected debt rung changes no version string at all.

## Gate 5 closed with a restrictive decision — `DEBT_RUNG_EVEBITDA_V1`

| rung | status | pairs | near-agree | cheap-decile churn |
|---|---|---|---|---|
| exact three-way total | **ELIGIBLE** | (reference) | — | — |
| `SUM(LTDNoncurrent+LTDCurrent)` | **ELIGIBLE** | 43,408 | 98.27% | 14.13% |
| `LongTermDebtNoncurrent` | **REJECTED_MEASURED** | 43,181 | 97.71% | 22.55% |
| `LongTermDebt` | UNDETERMINED | 149 | — | — |
| `LongTermDebtAndCapitalLeaseObligations` | UNDETERMINED | 113 | — | — |
| `DebtLongtermAndShorttermCombinedAmount` | UNDETERMINED | 0 | — | — |

Three statuses, not two, because **"measured and it failed" is not "we have no
evidence"** — and they earn different refusal codes so a later reader can tell
which rungs were judged. Missing provenance is UNDETERMINED, never eligible: a
debt figure whose rung was not recorded cannot have been validated. An
*exact*-bound rung can still be UNDETERMINED — eligibility is about validated
ordering behaviour, not nominal tag completeness.

**The continuous Q was refused on the record.** `Q = {1.000, 0.983, 0.977}`
compresses exactly the distinction the test exposed: 0.983 against 0.977 makes
the two rungs look interchangeable while their cheap-decile churn is 14.13%
against 22.55%. It describes broad rank agreement while the factor's use is
tail-sensitive. `Q = 1 − churn` was also refused — one arbitrary mapping for
another. What is preserved instead: the raw rung, its provenance, the validation
statistics, the eligibility status. A continuous shrinkage remains a
**challenger** — deferred, not refuted.

The cost is stated: excluding `LongTermDebtNoncurrent` costs 7.8–9.7 points of
base and roughly halves the tail distortion.

## `threshold_history` is permanent research lineage

The first rule would have **promoted** `LongTermDebt` (+6.7 points of base,
98.66% near-agreement, 5.95% churn — on 149 pairs, one or two per date, where a
"cheapest decile" is one name out of two). `MIN_POOLED_PAIRS = 1000` withdrew
that verdict and **moved nothing else**. A second floor can only withdraw a
verdict, never grant one. That history is the answer to "why was this rung
excluded?" asked two years from now.

## Still blocked

| | |
|---|---|
| validation baseline | dead-company prices |
| hedge validation | option archive starts 2026-09-20 |
| three debt rungs | the absence sidecar — this store cannot tell an untagged zero from an untagged amount |
| the anchor level | a convention; sweep specified, not run, never scored against survivor returns |
| earnings growth across filings | cross-filing TTM still binds on the corporate-action table |
| rate-regime generalization | 2013–2026 is one modern rate cycle |

---

# SUPERSEDED — `spec_freeze_v1`, `FROZEN_BUT_NONEXECUTABLE`

```
digest   7a844dca245cfb37ea6722d8e34a9d9407f82e2210ec17f62605aa8ab40f7843
status   FROZEN_BUT_NONEXECUTABLE
reason   PILLAR_WEIGHT_SEMANTIC_CONFLICT
rows it ever produced   0
```

**Not repaired in place.** The record stands exactly as frozen, because
correcting a frozen digest would destroy the only evidence the defect existed
and would break the rule the freeze exists to enforce.

## The defect

```
code   (frozen v1)        0.35 V  +  0.25 G  +  0.15 P  +  0.10 D
design (approved)         0.35 E  +  0.25 V  +  0.15 G  +  0.10 Q
```

The vector `(0.35, 0.25, 0.15, 0.10)` is **identical**. Only the labels differ —
and the labels are the model. In code, valuation carried 0.35 and there was no
EBITDA block at all, inverting the one instruction the v2 redesign existed to
serve. A backtest would have run perfectly and answered the wrong question.

Its origin is visible in the old comment: *"Valuation demoted 0.40 → 0.35"* —
v1's four pillars reweighted, rather than v2's four blocks adopted.

**Why the first freeze missed it:** it covered `SIGNATURE_VERSION` and
`COMPANY_SCALE_V2` but not the weight **table**, because the weights lived in
prose and test assertions. A version string cannot detect a change to the thing
it versions.

---

# FREEZE — `spec_freeze_v2`, successor

```
digest      b2bc35e7c2690c3625f9cb9de378df109246079364a9062d75cbe27ffe76ed49
components  29   (+2: the weight TABLE and the block SET)
status      FROZEN_PENDING_FACTOR_BLOCK_MAP
```

## v2 has its own block set, with its own alphabet

| block | semantic id | letter | weight |
|---|---|---|---|
| EBITDA / operating strength | `ebitda_strength` | E | **0.35** |
| Valuation | `valuation` | V | 0.25 |
| Real growth | `real_growth` | G | 0.15 |
| Financial quality | `financial_quality` | Q | 0.10 |

Full v2 signature is **`EVGQ`**, not `VGPD`. v1's pillars are untouched —
`equity_shaffer_v1_pit` really does have valuation/growth/profitability/debt and
VGPD is correct for it. Two models, two block sets, two alphabets. Reading a v1
signature against the v2 table now **raises**.

## The weight-semantic invariant

```
weight key  +  pillar semantic id  +  weight
```

must match across code, documentation, replay metadata and frozen components.
`assert_weight_semantics()` enforces it and is tested against the exact defect —
a table carrying the right vector with valuation at 0.35 is **refused**, as is a
v1 pillar name smuggled into a v2 table, as is a partial table.

Comparing `[0.35, 0.25, 0.15, 0.10] == [0.35, 0.25, 0.15, 0.10]` held true for a
week while the two sides described different models. Numeric-vector equality is
not evidence of agreement; it is what this defect looked like from the inside.

## Consequences accepted

The v1↔v2 "same company, both paths" demonstration is **gone**, replaced by two
separate demonstrations with a note. One input cannot feed both models once the
block sets differ; manufacturing a mapping to keep the old side-by-side alive
would re-assert exactly the equivalence the correction denies.

`pit_score_signature.validate()` had fed **v1 pillar names to `score_v2`** — the
self-test carried the same assumption as the defect it should have caught, which
is why it passed. Both fixtures are now lineage-native.

## Still blocking execution — additions, not contradictions

- no factor-to-block map exists (14 factor specs, no assignment)
- `MIN_BLOCK_WEIGHT = 0.40` lives only in prose
- the EBITDA-growth double count is unresolved: 0.1325 = **15.6% of the company
  score**, the single largest input, split across two blocks and invisible as
  such

---

# SUPERSEDED — `spec_freeze_v2`, `FROZEN_BUT_NONEXECUTABLE`

```
digest  5219bdd5902a220bbce6fc4b3bb68aeac17ee34928d340103be24ef85fd31462
reason  MISSING_WITHIN_BLOCK_WEIGHTS
rows it ever produced   0
```

Sealed, not extended. It fixed the weight semantics, the factor-to-block map,
the double count and the coverage floor — and still could not say how five
EBITDA factors combine into one E score. **Fixed ≠ editable until executable.**

---

# FREEZE — `spec_freeze_v3`, executable

```
digest      2f9bba3136ac45f4bdc37c5197e646acf645410f1c36ded698fbdd368f86e764
components  38
```

## The complete company model

```
CompanyScore = 0.35 E + 0.25 V + 0.15 G + 0.10 Q          clamp +/-85

  E = .35 benchmark + .25 growth + .20 efficiency + .20 acceleration
  V = .50 PE_abs    + .30 PE_rel + .20 EV/EBITDA
  G = RealRevenueGrowth
  Q = .35 FCFConv   + .30 IntCov + .25 NetDebt/EBITDA + .10 Debt/MktCap

ShafferScore = CompanyScore + SectorScore                 clamp +/-15
```

`POLICY_WEIGHTS_V1`, tagged **`NOT_EMPIRICALLY_OPTIMIZED`** — a transparent
starting hypothesis, not a claim that 35/30/25/10 is optimal.

## Effective company weights

| | factor | share |
|---|---|---|
| E | ebitda_benchmark | 12.25% |
| E | ebitda_growth | 8.75% |
| E | ebitda_efficiency | 7.00% |
| E | ebitda_acceleration | 7.00% |
| E | **ebitda_scale** | **0.00%** — `SCORING_WEIGHT_ZERO_CHALLENGER` |
| V | pe_ratio (both legs) | 20.00% |
| V | ev_ebitda | 5.00% |
| G | real_revenue_growth | 15.00% |
| Q | fcf_conversion | 3.50% |
| Q | interest_coverage | 3.00% |
| Q | net_debt_ebitda | 2.50% |
| Q | debt_market_cap | 1.00% |
| | **total** | **85.00%** = `COMPANY_SCALE_V2` |

E's four scoring weights reproduce the candidate document's published effective
weights **exactly**, so adopting them keeps the calibration and the model in
agreement rather than quietly replacing one with the other.

## The zero-weight challenger

`ebitda_scale` is **owned by E and carries no weight**. Not refused — refusal
means no block; this has a block and has not earned weight in it. It contributes
nothing to E's score, nothing to E's availability, and nothing to E's within-block
denominator, while its value stays observable for research. Raw scale and its
monotone transformations can masquerade as economic signal while primarily
rewarding size, and a company is not a good investment for being large.

## Q is deliberately not a leverage block

Three of Q's four factors touch debt. `fcf_conversion` leads at 0.35 because it
is the only one asking whether reported performance becomes **cash**.
`debt_market_cap` gets 0.10 because it overlaps the leverage question *and*
imports price movement into a balance-sheet block — if the stock collapses while
the balance sheet is unchanged, it deteriorates mechanically.

## Within-block renormalisation, and the one thing it is not

Siblings inside a block may stand in for one another — three of E's four factors
score over a 0.80 denominator, and the within-block coverage travels on the row
so a thin block is distinguishable from a full one. **Across** blocks there is no
renormalisation: a missing block shrinks the score rather than promoting the
survivors.

## G's single-point dependency, declared

`real_revenue_growth` alone carries 0.15. If it is unavailable, G is unavailable —
there is no second reading. No second growth factor was manufactured to make the
architecture look symmetric. EPS growth may become a challenger later, now that
its share-basis machinery is correct.

## Status

`can_execute_replay()` → **True**. The economic specification is complete enough
to *calculate* the first survivor-only diagnostic replay. That is not validation,
and calculating it is a separate authorisation from specifying it.
`pit_feature` 0 | `pit_score` 0 | `pit_replay_run` 0.

---

# SUPERSEDED — `spec_freeze_v3`, `FROZEN_SUPERSEDED`

```
digest  2f9bba3136ac45f4bdc37c5197e646acf645410f1c36ded698fbdd368f86e764
reason  DIGEST_COVERED_VERSION_STRINGS_NOT_BODIES
main-store rows produced       0
isolated pilot rows stamped    198,042 / 60,936 / 5,582   (EG only, partial engine, disposable)
```

Superseded for a **governance** defect, not a modelling one: its 38 components
covered `pit_factor_spec` and `pit_normalization` by **version string only**.
`verify()` returned `intact=True` while two economic drifts sat underneath —
the same blind spot that let the v1 weight-semantic defect through, one level
down. Sealed, never widened.

# FREEZE — `spec_freeze_v4`, 2026-09-22 — SUPERSEDED the same day by `spec_freeze_v5`, see below

```
digest      912268b3ec11bfc641fcac78078a0fb288cc51ce5432f8c27f931a532d1faae9
components  47   (v3's 38 + 9: seven BODIES and two version strings -- corrected 2026-09-22, V4-GOVERNANCE-AUDIT v4-03; the line originally read "nine BODIES")
```

## What v4 digests that v3 did not

`SPECS_V3` (all 14 factor-spec bodies), `PRICED_EPS_V2`, `REPLACED_BY_VERSION`,
`candidate_v2_registry()` (the normalization registry body),
`FACTOR_DIRECTION_V1`, `SUBFACTOR_DIRECTION_V1`, `DIRECTION_POLICY_VERSION`,
`SUBFACTORS` (valuation subfactor bodies), `FACTOR_SPEC_VERSION_V3`.

Proven, not asserted — each of these breaks the freeze with **no version string
touched**:

```
remove revenue from ebitda_benchmark's spec body   → drifted: pit_factor_spec:SPECS_V3
flip one sign in the direction table               → drifted: pit_factor_blocks:FACTOR_DIRECTION_V1
flip one higher_is_better in the registry          → drifted: pit_normalization:candidate_v2_registry()
```

## D1 — `ebitda_benchmark` is the MARGIN reading (`factor_spec_v3`)

```
cohort   = peers in the 50th–75th percentile of EBITDA DOLLARS     (unchanged: Shaffer's identity)
scored   = EBITDA_i / Revenue_i  −  mean(EBITDA_j / Revenue_j  for j in cohort)
transform  BENCHMARK_ANCHORED, S = 100·tanh(x / 0.06)
```

The dollar reading is `FACTOR_SPEC_V2_KNOWN_LIMITATION`: measured rank-identical
to raw EBITDA dollars (design record §S1), it turns the largest single factor
weight (12.25%) into a second copy of `ebitda_scale` — the size factor that was
given zero weight *for being one*. CTSH 2019-06-28: a $12.4M benchmark against
$3.3B of own EBITDA saturates the clamp. Cost accepted: `revenue` joins the
primitives, eligibility becomes `EBITDA_AND_REVENUE`, a measured further 13.2%
coverage hole.

## The P/E declaration, corrected in v3 rather than bypassed

`PRICED_EPS` declared point-in-time EPS unavailable. `pit_eps_obs` holds 888,486
rows. Under v2, `eligible_peers('pe_ratio')` refused the whole P/E backbone in
0.00 s without a database read. `PRICED_EPS_V2` corrects it; v2's object is
preserved verbatim. Production replay derives eligibility from the corrected
spec — an engine override was explicitly refused by the owner.

## D2 — `FACTOR_DIRECTION_V1`

| factor | direction | source |
|---|---|---|
| ev_ebitda (supplement) | LOWER_IS_BETTER | owner, 2026-09-22 |
| fcf_conversion | HIGHER_IS_BETTER | owner |
| interest_coverage | HIGHER_IS_BETTER | owner |
| net_debt_ebitda | LOWER_IS_BETTER | owner; agrees with frozen v1 core |
| debt_market_cap | LOWER_IS_BETTER | owner; agrees with frozen v1 core |
| ebitda_* (5), real_revenue_growth | adopted live from the registry | `candidate_v2_registry()` |
| pe_ratio, pe_absolute, pe_relative | EMBEDDED_IN_TRANSFORM | the convex transform carries the sign |

**Direction is orientation only, applied after domain-validity and refusal
checks.** It never overrides invalid-input handling. `direction_of()` raises on
an undeclared factor; nothing defaults to "higher".

## D3 — UNDECIDED, on the record

| | A | B | engine today |
|---|---|---|---|
| ebitda_growth denominator | `abs(EBITDA_t−1)` — a rate (`pit_derive`, a formula string) | `Assets_t−4q` — an amount (registry + `pit_coverage`; rate meaningless for the 37.8% with EBITDA≤0) | **B** (`assets_lag1`, `R_NO_ASSETS_LAG1`) |
| interest_coverage numerator | `operating_income` (`pit_derive`, spec primitives, `pit_coverage` arithmetic) | `EBITDA` (`pit_coverage` FEATURE_NOTE only) | **neither** — NOT_COMPUTABLE |

Historical evidence: design record open question 6 supports growth-B; none
measured for interest coverage. Owner instruction: report, do not infer.
*(2026-09-22, later: DECIDED by the owner as A for both -- see the
`spec_freeze_v5` section below. This record is kept as written.)*


# SUPERSEDED — `spec_freeze_v4`, `FROZEN_SUPERSEDED` (2026-09-22, same day)

```
digest  912268b3ec11bfc641fcac78078a0fb288cc51ce5432f8c27f931a532d1faae9
reason  FROZE_UNRESOLVED_SEMANTIC_CHOICES_D3
main-store rows produced          0
isolated scratch rows stamped     650 / 200 / 25, 13,000 / 4,000 / 461 and 52,000 / 16,000 / 1,786
                                  per arm on 2019-06-28 (RSS harness and oracle DBs, disposable)
```

Superseded because it froze two semantic choices it had not made (D3: the
growth/acceleration denominator and the coverage numerator) and digested no
executable factor definition, so an engine with altered arithmetic passed its
gate (V4-GOVERNANCE-AUDIT, hole B) while the bar for the largest factor weight
sat outside the digest (hole A). It also carried an incident cause now
**REJECTED_BY_MEASUREMENT**: the engine's private commit measures ~245 MiB at
4,000 targets with the full 7,102-entity index resident, and ~4.5 GiB of commit
sat in three autostarted Python services with no replay running
(POST-REBOOT-REPORT §1 and §5; owner ruling §5a). The measurement artefacts that
state the old cause are left as written. Sealed, never widened.

# FREEZE — `spec_freeze_v5`, 2026-09-22

```
digest      58722f4c00b676fee76bcc10ba341f44ce18c540a2fbc90625570a51786a5b59
components  61   (v4's 47 + 14)
```

## What v5 digests that v4 did not

`SPECS_V4` / `FACTOR_SPEC_VERSION_V4` (the D3 bodies), `FORMULAS_VERSION_V1` /
`FORMULAS_V1` + `FORMULA_GOLDEN_V1` (the executable definition of each of the 13 replayed keys,
witnessed through `pit_replay.resolve_primitives` by `pit_replay.validate()`),
`EBITDA_GROWTH_BASE_POLICY_V1`, `INTEREST_COVERAGE_DOMAIN_V1`,
`BENCHMARK_BAND_V1` / `BENCHMARK_MIN_COHORT_V1` (lifted out of
`benchmark_margin_50_75`), `V2_BLOCKS`, `TRANSFORM_PARAMS_V1`, `PINNED_CURVE`,
`PAIR_PATHS`, `ENGINE_MUST_DERIVE_FROM`. `validate()` now also rejects a
`callable:` inside a body and proves `digest(_FROZEN_MANIFEST) == FROZEN_DIGEST`.

Proven, not asserted — each of these breaks the freeze with **no version
string touched** (`test_pit_frozen_spec`, section 2):

```
revert interest_coverage's v4 eligibility body   → drifted: pit_factor_spec:SPECS_V4
edit one FORMULAS_V1 string                      → drifted: pit_factor_spec:FORMULAS_V1
move max_abs_rate in the base policy             → drifted: pit_factor_spec:EBITDA_GROWTH_BASE_POLICY_V1
widen the benchmark band                         → drifted: pit_normalization:BENCHMARK_BAND_V1
plant a function in TRANSFORM_PARAMS_V1          → validate(): names the callable
```

## D3 — DECIDED (owner, 2026-09-22)

| | decision | engine (`pit_replay/1.1`, `factor_spec_v4`) |
|---|---|---|
| ebitda_growth | `(E_t − E_t−1) / abs(E_t−1)` under `EBITDA_GROWTH_BASE_POLICY_V1` | A; `ebitda_growth_base_zero` / `ebitda_growth_base_near_zero` are named refusals |
| ebitda_acceleration | `growth_t − growth_t−1` (NOT a second difference over assets) | A; three EBITDA observations, no asset base |
| interest_coverage | `operating_income / interest_expense`, interest > 0 | `InterestCoverageRank` (PERCENTILE_RANK); zero / negative / missing are named states; negative operating income is scored |

The owner's reasons, on the record: the factor is called growth and B is an
asset-scaled change in EBITDA, an efficiency-like concept E already carries;
A has the stronger lineage (pit_derive, the v1 core, the coverage contract,
acceleration as a change in rates); operating income over interest is the
conventional EBIT-style coverage and is supported by four authored artefacts
against one feature note.

**Base policy (`EBITDA_GROWTH_BASE_POLICY_V1`)**: denominator `abs(E_t−1)`; a
zero base is refused by name; `|rate| > max_abs_rate = 10.0` is refused by name
(a base too small to carry a rate, kept out of the cohort dispersion sample);
a sign transition (loss → profit) is computed as a positive rate with the base
sign written on the row (`bs`); both-negative is computed with `abs()` so a
shrinking loss is an improvement. **`max_abs_rate = 10.0` is a CONVENTION
awaiting the owner's confirmation** (a change is a v6). Alternatives
considered and not adopted: refuse the exact zero only (v1 semantics; leaves a
+201.0 rate in the cohort sample); refuse every non-positive base (would refuse
roughly a third of growth rows and contradicts the authored "shrinking loss is
an improvement" rationale); a dollar floor (not scale-free). The expected
refusal share under the adopted band is not estimable from the repository; a
bounded read-only count of `E_t / E_t−1 > 11 or E_t / E_t−1 < −9`
(equivalently `|E_t − E_t−1| > 10·|E_t−1|`) among growth-eligible pairs on
one census date would size it. The band is on the RATE, not on the base's
size: an 11x rise from a healthy base is refused under the same name, and
`|rate| == 10` exactly is computed.

**Coverage domain (`INTEREST_COVERAGE_DOMAIN_V1`)**, the owner's words made
executable: `interest_expense > 0` is the ordinary case; a tagged zero, a
negative value and an untagged value at the operating-income period are the
named states `interest_expense_zero`, `interest_expense_negative`,
`no_interest_expense_at_operating_income_period`; no operating-income period is
`no_operating_income_period`; negative operating income is a legitimate negative
coverage; the FIRST ladder rung carrying a value at the operating-income period
is used. The matched-period eligibility rule
(`OPERATING_INCOME_AND_INTEREST_MATCHED_V2`), the refusal vocabulary and the
PERCENTILE_RANK transform are the session's proposals, also awaiting
confirmation, as are two consequences the review surfaced: under a rank the
ordering among negative-OI rows is inverted relative to interest burden
(OI −100 / interest 1 ranks below OI −100 / interest 100), and Q is now
available on interest_coverage alone so E + Q = 0.45 clears MIN_BLOCK_WEIGHT
and companies without G are scored. All six are listed in
`pit_frozen_spec.SCORE_ASSEMBLY_GAP["owner_confirmation_pending_v5"]`.

Evidence note: 37.8% EBITDA ≤ 0 is the FY2014 n=2,756 valuation-leg
measurement; the design record's 34% is unsourced; the −$50k → +$10m example is
illustrative. Neither form was measured against the other. The v4 record was
silent on acceleration.

## Executability

v5 is executable only while `pit_replay.FACTOR_SPEC_VERSION ==
ENGINE_MUST_DERIVE_FROM` (`factor_spec_v4`); `pit_replay.validate()` and
`require_gate()` refuse otherwise. Census 6 / 1 / 6 (ebitda_benchmark still
refused because the MARGIN path is not built in the engine; the P/E chain not
built; four factors without a registered transform), reachable weight
E+G+Q = 0.60. Pilot rows will differ from `pit_replay/1.0` rows by design.
No replay authorisation exists.
