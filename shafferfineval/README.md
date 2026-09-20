# SHAFFERFINEVAL

A personal multi-asset research and risk terminal, built around two outputs:

**SHAFFER SCORE** (-100..+100) and **SHAFFER HEDGE**.

```
Tradeable Universe -> Market/Fundamental Data -> Asset Class Scoring Engine
  -> Shaffer Score -> Position -> Shaffer Hedge

ShafferScore = CompanyScore (-75..+75) + SectorOverlay (-25..+25)
```

Three pages: **Market DB** (every asset in the workbook, searchable and
sortable), **Watchlist** (watchlist plus positions with exact hedge tickets),
and **ML Lab** (does any of this actually predict returns?). Opening an asset
from either of the first two drills into **Asset Detail**.

Scores are stored in SQLite with immutable daily snapshots, refreshed by a
scheduled job after the close. Pages read the database, so the terminal stays
fast with the whole universe loaded.

Completely separate from the intelligence-terminal reporting routines — it
shares no state, no config and no code with them.

## Run it

**The launcher does everything.** First run builds the virtual environment and
installs the two dependencies; every run after that just starts the terminal
and opens a browser window once the server is actually answering.

| Platform | Do this |
|---|---|
| macOS | double-click **`run.command`**, or `./run.sh` in Terminal |
| Linux | `./run.sh` |
| Windows | double-click **`run.bat`** |

```bash
./run.sh              # start the app
./run.sh --refresh    # score everything first, then start
```

It opens at http://127.0.0.1:8501. The workbook universe loads automatically
and the database is created on first run. No login, no account, and no API key
is needed to start — `FRED_API_KEY` only unlocks the macro engines.

Set `SHAFFERFINEVAL_PORT` to move it off 8501. Set `PYTHON` if `python3` is not
the interpreter you want (`PYTHON=python3.12 ./run.sh`).

The launcher requires **Python 3.10+** and says so rather than failing later:
the engines use `list[dict]` and `X | None` annotations that older versions
reject at import. If the dependency install fails it stops there with the
likely cause, and does not start a half-built app.

### Doing it by hand

```bash
cd shafferfineval
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Two dependencies, `streamlit` and `requests`. Everything else — the
statistics, the ML models, the `.xlsx` reader — is written against the standard
library, so there is no numpy/pandas/scikit-learn to install or keep compatible.

### Save it as an app

Streamlit is a web server, so the way to get a real dock or taskbar icon is to
install the page as an app. Start it first, then:

- **Chrome / Edge** — open http://127.0.0.1:8501, then ⋮ menu →
  *Cast, save and share* → **Install page as app** (Edge: *Apps → Install this
  site as an app*). You get a standalone window with no browser chrome, and an
  icon in the Dock, Start menu or taskbar.
- **Safari** — *File → Add to Dock*.

The installed icon only opens the window; it does not start the server. Launch
`run.command` / `run.bat` first, or have it start automatically:

- **macOS** — System Settings → General → Login Items → **+** → `run.command`.
- **Windows** — press `Win+R`, run `shell:startup`, and put a shortcut to
  `run.bat` in the folder that opens.
- **Linux** — a systemd user unit running `run.sh`, or add it to your desktop
  environment's startup applications.

`.streamlit/config.toml` binds the server to `127.0.0.1` and keeps it there.
This is a single-user local tool holding a personal trading book; nothing in it
is built to face a network, so do not expose the port.

### Refresh the scores

The UI reads the database. Scoring happens in the refresh job.

```bash
python3 daily_job.py              # official immutable close snapshot
python3 daily_job.py --intraday   # replaceable working snapshot
python3 daily_job.py --symbols NVDA,AAPL
python3 daily_job.py --date 2026-09-20
```

**Scheduling this is the single thing that makes the rest of the project
work.** Every ML status reads INSUFFICIENT DATA until snapshots accumulate,
and a snapshot can only be taken on the day it describes — there is no way to
reconstruct one later, because backfilling fundamentals that were not actually
known on a past date is exactly what this project refuses to do. A month not
scheduled is a month of evidence that cannot be recovered.

Schedule it after the US close (the sidebar shows every class's schedule).

**macOS / Linux** — `crontab -e`:

```cron
CRON_TZ=America/New_York
30 16 * * 1-5 cd /path/to/shafferfineval && .venv/bin/python daily_job.py >> refresh.log 2>&1
```

On macOS, cron needs Full Disk Access (System Settings → Privacy & Security)
and the machine must be awake at 4:30pm ET. A laptop that is usually asleep is
better served by `launchd` with `StartCalendarInterval`, which runs the job at
next wake instead of skipping it.

**Windows** — Task Scheduler, or from an admin prompt:

```bat
schtasks /create /tn ShafferFinEval /tr "C:\path\to\shafferfineval\.venv\Scripts\python.exe C:\path\to\shafferfineval\daily_job.py" /sc weekly /d MON,TUE,WED,THU,FRI /st 16:30
```

Use the venv's own `python`, not the system one — the system interpreter does
not have `requests` installed.

The sidebar also has **Refresh Asset / Refresh Watchlist / Refresh All** for
manual runs. Note that every refresh sweeps the whole supported universe even
when you ask for one ticker: the score is peer-relative, so a ticker scored
against a peer set of one would be meaningless. The sweep is cached per day
per process, so repeated manual refreshes in a session are cheap.

Run the tests (pure stdlib — no install needed):

```bash
python3 test_scoring.py           # classification + shared statistics
python3 test_sector_scoring.py    # sector model
python3 test_company_scoring.py   # company model
python3 test_hedging.py           # hedge engine + workbook strategy parsing
python3 test_terminal.py          # universe, database, routing, refresh
python3 test_prediction_ml.py     # predicted return + ML Lab
python3 test_multi_asset.py       # asset-class equations, GPI, routing
python3 test_research.py          # synthetic validation, blotter, counterfactuals
python3 test_pipeline_growth.py   # short-horizon labels, trade grading, macro wiring
```

## Files

**UI** — no calculation lives here.

| File | Role |
|---|---|
| `app.py` | Shell: navigation, global search, refresh controls. |
| `views/market_page.py` | Page 1 — Market DB: universe, search, filters, sorting. |
| `views/watchlist_page.py` | Page 2 — Watchlist and positions with exact tickets. |
| `views/asset_detail_page.py` | Asset Detail drill-down — score breakdown, hedge, forecast, what changed, history. |
| `views/hedge_view.py` | Shared HedgeResult renderer. |
| `views/common.py` | Formatting helpers and terminal CSS. |

**Engines** — pure stdlib, no Streamlit, no network.

| File | Role |
|---|---|
| `company_scoring.py` | Company model (valuation/growth/profitability/debt). |
| `sector_scoring.py` | Sector model (growth/ROE/ROA/debt, percentile-ranked). |
| `hedging.py` | Hedge engine: conflict, ratio, eligibility, tickets, 7-factor scoring. |
| `statlib.py` | Shared statistics so every engine normalises identically. |
| `scoring.py` | Shared four-band classification. |

**Data, storage, orchestration**

| File | Role |
|---|---|
| `universe.py` | Workbook loader (stdlib `.xlsx` reader), all 8 asset-class sheets. |
| `strategy_catalog.py` | "Playbook strategies" loader and leg parser. |
| `market_data.py` | Yahoo quotes/fundamentals. Retrieval only. |
| `option_data.py` | Yahoo option chains (crumb handshake). Retrieval only. |
| `storage.py` | SQLite schema and repositories. |
| `routers.py` | Asset-class dispatch for scoring and hedging. |
| `refresh.py` | Daily refresh, change detection, score deltas, macro routing. |
| `daily_job.py` | Cron entry point. |
| `macro_data.py` | FRED + Yahoo macro adapter (15 FRED series, 14 Yahoo symbols). Retrieval only. |
| `macro_factors.py` | Turns the macro snapshot into rates/gold/vol/credit factor scores. |
| `trade_research.py` | Entry-state freeze, closed-position grading, hedge-effectiveness dataset. |
| `asset_models.py` | Shaffer v1 arithmetic for all 19 asset classes + derivative overlays. |
| `political.py` | Geopolitical & Policy Impact (GPI v1). |
| `prediction.py` | Shaffer Predicted Return (score → 12M price return). |
| `synthetic.py` | 11 synthetic ML validation tests. |
| `blotter.py` | FinSim blotter import, trade grouping, hedge linking. |
| `counterfactual.py` | Replay every eligible hedge against the realised path. |
| `mllib.py` | Pure-stdlib ML models and metrics. |
| `ml_lab.py` | Dataset, walk-forward validation, calibration, registry. |
| `ml_job.py` | ML CLI: labels, training, promotion. |
| `views/ml_page.py` | Page 3 — ML Lab. |

**Tests** — `test_scoring.py`, `test_sector_scoring.py`, `test_company_scoring.py`,
`test_hedging.py`, `test_terminal.py`, `test_prediction_ml.py`,
`test_multi_asset.py`, `test_research.py`, `test_pipeline_growth.py`. All pure
stdlib; each is a script you run directly and each prints `FAILURES: 0`.

> `views/` is deliberately not called `pages/`: Streamlit treats a top-level
> `pages/` directory as an auto-multipage app and would run each module as its
> own script.

`scoring.py` never imports `market_data.py`, and neither imports `app.py`. To
reuse the engine elsewhere, take `scoring.py` and feed it numbers.

## Shaffer v1 — the production arithmetic

**Shaffer v1 is the current production arithmetic. It is a starting hypothesis,
not an assumed optimum.** The ML Lab's job is to find where it is wrong. It
cannot change it: promotion is always explicit.

19 asset-class equations, every one verified to sum to 1.0:

| Class | Equation | Version |
|---|---|---|
| Common equity | `0.75 × (0.40V + 0.25G + 0.20P + 0.15D)` | `equity_shaffer_v1` |
| REIT | `0.35V + 0.25G + 0.20Q + 0.20D` | `reit_shaffer_v1` |
| Preferred | `0.35C + 0.30Y + 0.20R + 0.15Call` | `preferred_shaffer_v1` |
| ETF / index | `0.80U + 0.20B` | `etf_shaffer_v1` |
| Government bond | `0.27CB + 0.22INF + 0.18G + 0.13Y + 0.08Curve + 0.12Policy` | `rates_shaffer_v1` |
| Corporate bond | `0.32C + 0.23S + 0.18R + 0.09CF + 0.08T + 0.10GPI` | `corp_credit_shaffer_v1` |
| Agency MBS | `0.30OAS + 0.25Rates + 0.20Prep + 0.15Vol + 0.10Carry` | `mbs_shaffer_v1` |
| Structured credit | `0.30Spread + 0.25Collateral + 0.20Coverage + 0.15Structure + 0.10Liquidity` | `structured_shaffer_v1` |
| FX pair | `0.25RR + 0.20CB + 0.13G + 0.09CA + 0.08V + 0.08Carry + 0.17GPI` | `fx_shaffer_v1` |
| Crude oil | `0.22Inv + 0.17Supply + 0.17Demand + 0.12Curve + 0.08Ref + 0.07USD + 0.17GPI` | `oil_shaffer_v1` |
| Natural gas | `0.25Storage + 0.17Weather + 0.17Supply + 0.13LNG + 0.08Curve + 0.05Demand + 0.15GPI` | `natgas_shaffer_v1` |
| Gold | `0.27RealYield + 0.18USD + 0.13Flows + 0.13Inflation + 0.09Risk + 0.08Mom + 0.12GPI` | `gold_shaffer_v1` |
| Industrial metal | `0.23PMI + 0.18Inv + 0.17Supply + 0.13China + 0.09USD + 0.08Curve + 0.12GPI` | `industrial_metal_shaffer_v1` |
| Agriculture | `0.27StocksUse + 0.22Weather + 0.18Prod + 0.13Exports + 0.08Curve + 0.12GPI` | `ag_shaffer_v1` |
| Livestock | `0.25Herd + 0.20Feed + 0.20Slaughter + 0.15Demand + 0.10Curve + 0.10Seasonality` | `livestock_shaffer_v1` |
| Crypto | `0.25Liq + 0.20Mom + 0.15Flows + 0.15Network + 0.15Leverage + 0.10Supply` | `crypto_shaffer_v1` |
| Volatility | `0.30VV + 0.25Curve + 0.20Stress + 0.15VoV + 0.10Positioning` | `vol_shaffer_v1` |
| Futures | `0.85Underlying + 0.10CarryCurve + 0.05Liquidity` | `futures_shaffer_v1` |
| Listed option | `0.55D + 0.25VV + 0.10Theta + 0.10Liquidity` | `option_shaffer_v1` |

Derivative overlays inherit their underlying rather than running a competing
economic model, so a derivative can never contradict it:

```
BuyProtection  = -CreditStrength + CDSRelativeValue     cds_shaffer_v1
ReceiveTRS     =  Underlying - CarryCost                trs_shaffer_v1
PayTRS         = -Underlying - CarryCost
ReceiveFixed  ~=  RatesScore                            irs_shaffer_v1
PayFixed      ~= -RatesScore
FXForward      =  0.85 FXScore + 0.15 ForwardValue      fx_forward_shaffer_v1
```

### Direction is never ambiguous

Every model carries an explicit direction label, because a positive score means
different things: bullish the asset, bullish bond *total return* (not yield),
attractive to BUY protection, attractive to RECEIVE fixed, attractive to be
LONG volatility.

### Normalization

Cross-sectional factors use percentile rank (`200p − 100`, or `100 − 200p` where
lower is better, average rank for ties). Time-series factors use historical
percentile or a tanh-squashed z-score. Raw units never enter a weighted
equation. A missing factor is **dropped and the remaining weights
renormalized** — never a silent zero — and coverage, missing factors and
confidence are tracked on every result.

### What has data, and what does not

Every equation is implemented and tested. What separates a live engine from a
blocked one is whether its inputs have a wired feed — never whether the maths
exists. A blocked engine returns a structured `awaiting_inputs` result naming
exactly what is missing, and `macro_factors.BLOCKED_ENGINES` lists the reason
for each one so the gap is visible in code rather than implied.

**Live end to end** (`macro_factors.LIVE_ENGINES`, fed by `macro_data.py` from
FRED and Yahoo):

| Engine | Factors filled | Source |
|---|---|---|
| `equity_shaffer_v1` | 4 of 4 + sector overlay | Yahoo fundamentals |
| `sector_model_v1` | 4 of 4 | Yahoo fundamentals, peer-ranked |
| `rates_shaffer_v1` | 5 of 6 (all but the policy leg) | FRED: fed funds, 2y, 10y, 10y breakeven, unemployment |
| `gold_shaffer_v1` | 5 of 7 (no central-bank flows, no GPI) | FRED: 10y real yield, broad dollar, 10y breakeven; Yahoo: VIX, gold |
| `vol_shaffer_v1` | 3 of 5 from the snapshot, 4 when an underlying series is passed | Yahoo: VIX, VIX3M |
| `corp_credit_shaffer_v1` | spread + rates legs only | FRED: IG and HY OAS, plus the rates leg above |

The volatility engine is fed but **not yet routed**: `refresh.MACRO_ROUTES`
maps Treasuries and corporate bonds, and gold is routed by symbol, but no
instrument in the universe currently dispatches to `vol_shaffer_v1`. Its
factors are computable today — the routing is the missing piece, and saying so
is more useful than a table row implying volatility scores are being written.

Corporate credit deliberately scores only its market-level legs: issuer credit
strength, cash-flow quality and technicals need per-issuer data that is not
wired, and the engine reports those as missing rather than renormalising the
weights onto two factors and calling it a full score.

That has a consequence worth stating plainly: **every corporate bond currently
scores the same.** An AAPL bond and an AAL bond get the same number because
only the market-level spread and rates legs exist and the universe carries no
credit rating, so investment grade and high yield cannot even be told apart per
issuer. That is a data gap, not a view that those are equivalent credits, and
the IG spread is applied to all of them rather than a high-yield spread being
assigned to bonds nothing has established are high yield.

Every macro-scored row therefore carries a **caveat string**
(`refresh.MACRO_CAVEATS`) stored in its own `raw_inputs` and shown on the asset
page above the score, so the limitation travels with the number instead of
living in a run log. Treasuries carry a different one: they share a score *by
design*, because the rates view is curve-wide and duration changes expected
return rather than conviction.

**Blocked, with the reason stated:**

| Engine | Blocked on |
|---|---|
| `fx_shaffer_v1` | foreign real rates and policy paths; only US macro is wired |
| `oil_shaffer_v1` | EIA inventories, OPEC output, refinery utilisation |
| `natgas_shaffer_v1` | storage vs seasonal norm, HDD/CDD forecasts, LNG flows |
| `industrial_metal_shaffer_v1` | PMI, exchange inventories, China demand |
| `ag_shaffer_v1` | stocks-to-use, crop weather, export demand |
| `livestock_shaffer_v1` | herd counts, slaughter rates, feed costs |
| `crypto_shaffer_v1` | stablecoin liquidity, on-chain activity, funding/OI |
| `etf_shaffer_v1` | constituent holdings; Yahoo's holdings module is crumb-gated |
| `mbs_shaffer_v1` | option-adjusted spread and prepayment speeds |
| `structured_shaffer_v1` | deal-level collateral, OC/IC tests, DSCR/LTV |
| `preferred_shaffer_v1` | issuer credit, yield/spread, call schedules |
| `reit_shaffer_v1` | AFFO, NAV, cap rates, occupancy |

Supplying `factor_values` scores any of them immediately — the arithmetic is
real and tested, only the feeds are absent.

**Two routing rules that exist to stop a plausible-looking wrong answer:**

- Only US instruments route to `rates_shaffer_v1`. A bund or a JGB is priced
  off the ECB and the BoJ, so feeding it the US curve would produce a
  confident score about the wrong economy. Non-US government bonds stay
  unscored until foreign macro is wired.
- Only gold routes to `gold_shaffer_v1`. Silver, platinum and palladium are
  half industrial metals; they need their own subtype model and are listed as
  needing one rather than inheriting gold's real-yield factors.

## Geopolitical & Policy Impact (GPI v1)

Two deliberately separate numbers:

```
PoliticalRiskLevel  [0, 100]     how significant is the EVENT
PoliticalImpact     [-100, +100] what it does to a SPECIFIC asset

GPI severity = 0.30 Conflict + 0.20 Sanctions + 0.20 Regulation
             + 0.15 Fiscal + 0.10 CapitalControl + 0.05 Uncertainty

PoliticalImpact = DecayedSeverity × AssetExposure × Confidence
AssetExposure   = 0.35 Revenue + 0.25 SupplyChain
                + 0.20 Production + 0.20 Regulatory      in [-1, +1]
```

One strait-disruption event at severity 50.5 scores **oil +25.9, airlines
−20.4, gold +9.3, software −0.5**. Same severity; the sign comes from exposure.

Impact decays as `exp(-ln2 · t / halflife)` by event class — a headline fades in
days, a proposed regulation in months, an enacted law in years — and
structurally active events (live sanctions, a running conflict) do not decay
while active.

For equities the overlay is a **separate layer**, capped at ±10 by default
(±15 configurable):

```
FinalShafferEquityScore = CompanyScore + SectorOverlay + PoliticalOverlay
```

The company arithmetic is untouched. FX uses the relative difference
`GPI_pair = GPI_base − GPI_quote`.

Political raw features are stored individually, not just the final overlay, so
the Lab can test *which* political input mattered. **There is no automated
event feed** — events are entered explicitly, and nothing is inferred from news.
The engine scores measurable economic consequences only; it does not score
ideology, parties or voter preference.

## The hedge engine

```
d = +1 long, -1 short
AdverseScore = max(0, -d x ShafferScore)
HedgeRatio   = clamp((AdverseScore - 15) / 85, 0, 1)

HedgedShares  = |shares| x HedgeRatio
HedgeNotional = HedgedShares x price
```

An adverse score of 62 on a long 10,000 at $180 gives a 55.3% hedge, 5,529
shares, $995,294 notional and 55 contracts — the worked example in the spec.

### The workbook is the strategy universe

All 60 strategies in the **Playbook strategies** sheet are parsed from their
machine-readable leg definitions. Nothing is hand-written:

```
buy stock; buy option P @ pct:-7          -> protective put
buy option P @ pct:-7; sell option P @ pct:-20   -> put spread
sell trs                                   -> pay total return
sell option C @ zero_cost                  -> zero-cost collar call
sell option C @ pct:+12 x2.0               -> ratio leg
```

All 60 parse cleanly; any that did not would be reported rather than skipped.

### Eligibility is a payoff test, not a name test

A strategy qualifies only if its **overlay** — the structure minus the position
leg you already hold — makes money at the adverse stress point (±25%) *and does
not also gain* on the favourable side. So:

- **Admitted**: protective puts, put spreads, collars, zero-cost collars,
  put-spread collars, tail hedges, short stock, pay-TRS, short futures.
- **Rejected**: covered calls (payoff at -25% is zero), iron condors (the sold
  put adds downside), short strangles, long calls, and long straddles/strangles
  — those protect the downside but their call leg doubles the bullish bet,
  which is a volatility view, not a hedge.

The ±25% stress sits beyond the widest strike the workbook uses (a 20% tail
call), so a tail structure is measured where it actually pays rather than
exactly at its own strike where its payoff is zero by construction.

Relative-value pairs are excluded with a reason: the workbook defines the
structure but not which name to pair against, and shorting the position's own
ticker would misrepresent it.

### Dynamic strikes

```
OTMPercent  = 15% x (1 - AdverseScore / 100)
TargetStrike = spot x (1 - OTMPercent)    put
             = spot x (1 + OTMPercent)    call
```

The override applies **only where the workbook used its group default** (-7%
put, +10% call). A deliberately different strike is the product's identity, so
the 2% tight put, the ATM put and the 17% tail hedge keep theirs — otherwise
every put strategy would collapse into the same ticket. In a put spread the
upper strike moves with the score and the sold -20% leg stays put, exactly as
the spec's example shows.

Both the theoretical target and the nearest actual listed strike are displayed,
with the percentage from spot.

### Strategy score

```
0.30 Effectiveness + 0.20 Cost + 0.15 Upside retained + 0.10 Liquidity
   + 0.10 Capital efficiency + 0.10 Tenor fit + 0.05 Basis quality
```

Effectiveness and upside are measured from the **resolved ticket** (the strikes
actually selected), not the workbook template. Unavailable factors are dropped
and the rest renormalise — with two deliberate exceptions, because that rule
alone would reward a strategy for missing the very data that should mark it
down:

- **Effectiveness is required.** A hedge whose effectiveness cannot be measured
  is excluded, not ranked.
- **An unsized leg excludes the strategy.** A futures hedge with no proxy
  instrument, beta or contract multiplier is dropped with that reason, rather
  than scoring well on the factors that remain.

Confidence (HIGH/MEDIUM/LOW) is reported separately and never silently changes
the score.

### Coverage: two different numbers

**Protection-floor coverage** is shares actually protected after contract
rounding (55 contracts = 5,500 shares). **Initial delta coverage** is
`contracts x 100 x |delta|`. They are shown separately and never conflated.

### No-hedge case

An adverse score at or below 15 produces a 0% hedge and the engine says **NO
FUNDAMENTAL HEDGE REQUIRED** rather than manufacturing one. Optional insurance
remains inspectable, clearly separated from the model requirement.

## Shaffer Predicted Return

```
PredictedReturnPct = 0.20 x ShafferScore          (score in -100..+100)
PredictedPrice     = CurrentPrice x (1 + PredictedReturnPct / 100)
```

So +100 → +20%, +50 → +10%, 0 → 0%, −50 → −10%, −100 → −20%. NVDA at $180 with
a +60 score predicts +12.0% and a 12-month price of $201.60.

This is an **absolute 12-month price return from today's market price**. It is
deliberately none of the following, and is never relabelled as any of them:

| Number | Question it answers |
|---|---|
| **Peer-implied value** | What does this company's valuation look like against its industry EBITDA cohort *today*? |
| **Shaffer 12M price** | Where does the score-to-return mapping put the market price in *twelve months*? |
| VTI excess return | Stored as a secondary research label only. |
| Total return | Not modelled — this is price only, no dividends. |

Asset Detail shows Current Price, Peer-Implied Value, Shaffer Score and Shaffer
12M Forecast as four separate values.

The 0.20 slope is a transparent placeholder, tagged `shaffer_score_linear` /
`v1_0.20` on every snapshot it produces. Until enough realised outcomes exist
to justify an interval, the prediction carries **MODEL ESTIMATE — UNCERTAINTY
NOT YET CALIBRATED** rather than a fabricated range.

## ML Lab

A **research and challenger** system. It measures whether Shaffer Scores and
their factors actually predict returns, learns alternative mappings, and
compares challengers against the human-designed model.

**It cannot change production.** The Shaffer Score, its factor weights, the
classification bands, the hedge formula and the V1 calibration are untouched by
anything in the Lab. A model reaches `PRODUCTION` only through an explicit
`storage.set_model_status(...)` call that no training path invokes.

```
Collect -> Observe Outcomes -> Train -> Backtest -> Validate -> Compare -> Propose
```

### Models

All pure stdlib — this environment has no numpy or scikit-learn, and the
project stays dependency-light. Each is implemented directly and is
deterministic.

| Model | Purpose |
|---|---|
| **Shaffer V1 baseline** | `0.20 × score`. The benchmark every challenger must beat. |
| Linear regression | Interpretable coefficients. |
| Ridge | Stabilises correlated financial factors. |
| Lasso / Elastic Net | Which factors carry no incremental signal. |
| Gradient boosting | Nonlinear interactions (valuation past a threshold, debt when profitability is weak). |
| Random forest | Comparison. |

No neural networks — the dataset will be far too small to justify one for years.

### Point-in-time rule

Features come **only** from immutable stored snapshots, so every value is what
was genuinely known that day. Revised fundamentals can never leak backwards.
Outcome labels are future prices relative to a snapshot, which is what a label
is — using today's price history for them is not leakage.

### Walk-forward validation

Expanding chronological windows: train on the past, test on the next period,
repeat. **Never** a random split. The test asserts that every training window
strictly precedes its test window.

### Horizons — evidence in weeks, not a year

The Shaffer Predicted Return is calibrated to 12 months, and that stays the
primary horizon. But a 12M label needs a snapshot to be a year old before it
can be graded, which means the first honest out-of-sample evidence about the
score would arrive a year after the first snapshot. So every snapshot is
labelled at **six** horizons:

| Horizon | Trading days | Calendar days | Sampling |
|---|---|---|---|
| `1D` | 1 | 1 | daily |
| `5D` | 5 | 7 | daily |
| `1M` | 21 | 30 | weekly |
| `3M` | 63 | 91 | monthly |
| `6M` | 126 | 182 | monthly |
| `12M` | 252 | 365 | monthly |

`1D`/`5D`/`1M`/`3M` are the **early horizons** (`prediction.EARLY_HORIZONS`).
Snapshots started today begin grading themselves tomorrow, and a January
snapshot already has matured 1D, 5D, 1M and 3M labels by September while its
12M label is still nine months from existing.

What the short horizons are and are not:

- They are a **read on whether the score has any cross-sectional signal at
  all**, months before the primary horizon can say anything.
- They are **not** a reason to re-calibrate the 0.20 slope. A 1-day label is
  mostly noise and says nothing about a 12-month return. `PRIMARY_HORIZON`
  stays `12M`, and nothing about promotion changes because a 1D model looks
  good.
- Short horizons sample daily precisely because they barely overlap; long ones
  stay on monthly sampling for the reason below.

A label is only written if a price exists close enough to the target date to
justify the label's name: within 10% of the horizon, with a four-day floor for
the weekend-and-holiday case. A three-week hole in a price series therefore
produces no `1D` label rather than a `1D` label measured over three weeks.

### Overlapping labels

Daily snapshots produce nearly identical 12-month labels, which inflates the
apparent sample. Long horizons default to **monthly** sampling, and the
effective (non-overlapping) observation count is always shown next to the raw
count. That count discounts *time* overlap; it does not discount
cross-sectional correlation, so a single snapshot date is reported
INSUFFICIENT however many names it carries — it cannot form walk-forward folds.

### Status thresholds (deterministic)

```
INSUFFICIENT DATA     < 200 effective observations, or < 3 folds
EXPERIMENTAL          trainable, not yet trustworthy
VALIDATED CHALLENGER  >= 750 effective observations, >= 3 folds, positive
                      out-of-sample Spearman beating the baseline by >= 0.02
```

Feature importance is labelled **predictive importance**, never a cause of
returns. Tree importance is never converted into proposed formula weights.

### Running it

```bash
python3 ml_job.py --status     # what data exists today
python3 ml_job.py --labels     # grade aged snapshots against realised prices
python3 ml_job.py --train      # walk-forward train + evaluate challengers
python3 ml_job.py --train --horizon 3M
python3 ml_job.py --promote 7  # explicit, manual, the only path to production
```

Training is deliberately separate from the daily refresh: `daily_job.py`
collects prediction data, `ml_job.py` trains. The daily job never retrains.

### Synthetic validation

Eleven tests generate data with a KNOWN built-in relationship and confirm the
pipeline recovers it. **All 11 pass.**

| Test | What it proves |
|---|---|
| Linear recovery | OLS/Ridge/ElasticNet recover signs and ranking (R²≈0.88) |
| Nonlinear interaction | GBM beats linear on a `V>50 AND G>40` threshold |
| Useless factor | Lasso zeroes pure noise; permutation ranks it last |
| Wrong weight | Production over-weights Valuation; challenger finds Growth stronger |
| Wrong sign | A factor assumed positive comes back negative |
| Political interaction | `GPI × InventoryTightness` — trees beat linear |
| Regime change | Walk-forward exposes instability a random split hides |
| Leakage prevention | Timestamp guard rejects future features; leaked R²=1.00000 |
| Hedge selection | Hedges cut the worst loss; upside ordering unhedged > put > TRS |
| Proxy sizing | Beta-adjusted residual variance 55% below dollar-matched |
| Option value | Model recovers the IV−RV edge (Spearman 0.99) |

**These validate the SOFTWARE, not the investment model.** A green board means
the machinery works. It says nothing about whether the Shaffer Score predicts
real markets. Synthetic observations are never written to live tables and never
mixed into production training.

### FinSim blotter integration

`blotter.py` imports trade history from CSV, TSV, Excel or SQLite. The adapter
is field-tolerant — it maps whatever schema FinSim provides and preserves
unknown columns rather than discarding them.

- **Security mapping** resolves FinSim symbols onto the Shaffer universe.
  Unmapped trades are kept and flagged, never dropped.
- **Trade grouping** collapses several trades into ONE economic position:
  long stock + protective puts is one hedged position; long and short the same
  future is a calendar spread; stock + pay-TRS is an exposure hedge. Explicit
  grouping (`trade_group_id`, `position_id`, parent/hedge chains) wins over
  inference.
- **Hedge links** record primary ↔ hedge with the implied hedge ratio.
- **Entry snapshots** capture the contemporaneous Shaffer state — score,
  factors, prediction, hedge, GPI, model versions — and are **immutable**, so a
  decision is judged on what was known at the time, never on revised data.

**My trades are not a random sample of the market.** The trade dataset is used
for execution, hedging and behaviour analysis only — never to train a general
asset-return model.

### Counterfactual hedges

Only the recommended hedge is ever traded, so `counterfactual.py` records what
EVERY eligible strategy was at decision time and replays each against the
realised path.

Rows are labelled **ACTUAL** (really traded) or **SIMULATED COUNTERFACTUAL**
(scored but not traded). The two are never blurred, and a simulated payoff is
intrinsic-value arithmetic — it assumes no slippage or liquidity constraint.

Research metrics: protection benefit, net protection benefit, hedge efficiency
(`downside reduction / hedge cost`, undefined rather than infinite at zero
cost), upside sacrificed, downside avoided, drawdown reduction.

### Hedge-effectiveness learning from closed positions

`trade_research.py` closes the loop between a hedge decision and what that
hedge actually did. It is the one dataset that does not have to wait a year:
a position held for three weeks grades itself three weeks later.

**At entry** — `import_and_freeze()` imports the blotter and freezes, per
trade, `Trade_t + Score_t + Prediction_t + HedgeRecommendation_t`: the traded
price and size, the Shaffer score and its factors, the predicted return, the
preferred hedge and its eligible alternatives, the GPI state, and every model
version. The freeze is write-once — re-importing the same blotter reports
`already_frozen` and a later rescore cannot reach back and change what the
decision was made on.

**At exit** — `evaluate_closed_position()` grades it:

```
underlying P&L      what the position did unhedged
hedge P&L           what the hedge legs actually paid
net P&L             the two together, minus hedge cost
drawdown            peak-to-trough, with and without the hedge
upside sacrificed / downside avoided / hedge efficiency
prediction error    realised return - predicted return
score agreement     did the score agree with how the position was held?
```

It then replays **every strategy that was eligible at entry** against the same
realised path, so the question "would a different hedge have been better?" is
answered from the frozen decision set rather than from hindsight. Those rows
land in `hedge_effectiveness_dataset()` carrying their ACTUAL / SIMULATED
COUNTERFACTUAL label.

`trade_performance_summary()` aggregates the behavioural split the trade
dataset exists for: mean P&L when the score agreed with the position versus
when it did not, mean hedge cost, mean drawdown reduction.

**An option is never inferred.** A call and a put are opposite trades, so the
right, the strike and the expiry come only from data the blotter actually
supplied — an explicit right column, a `Put`/`Call` asset class, or an OCC
symbol (`NVDA  260116P00170000`), which carries all three. An option's traded
`price` is its premium, never its strike. A trade that says only `Option` with
no right is flagged at import (`cannot be replayed: no right (put or call)`),
its leg returns an unknown payoff, and the hedged drawdown is left
**unavailable** rather than reporting the unhedged path under a hedge's name.
The alternative — defaulting to a call — would have invented the entire payoff
of the position.

### Is there enough data yet?

**No.** The database holds a single day of snapshots, so there are zero
labelled 12-month outcomes and nothing can be trained. The Lab reports
`INSUFFICIENT DATA FOR RELIABLE 12M ML TRAINING`, the Proposals tab returns
nothing, and no backtest is fabricated. Real results need the daily job running
for months — 12-month labels need twelve months.

The synthetic suite proves the machinery is ready for that evidence. It is not
a substitute for it.

### The intended loop

```
Human finance logic -> Shaffer v1 -> Real predictions -> Real outcomes
  -> ML evidence -> Better challenger -> HUMAN APPROVAL -> Shaffer v2
```

Never `Train -> Silently Rewrite`. A test asserts that training challengers and
running the full synthetic suite leaves every production constant byte-identical:
equity weights, the 0.75 scale, company weights, the 0.20 return calibration,
the hedge ratio curve and the seven hedge strategy weights.

## The company model

Four factors, weighted 40/25/20/15. Every component except valuation is
percentile-ranked against companies in the **same industry**, falling back to
the sector when the industry is too thin. The fallback is always labelled.

```
CompanyRawScore = 0.40V + 0.25G + 0.20P + 0.15D

    G = 0.45 RevenueGrowth + 0.35 RevenueAcceleration + 0.20 EBITDAGrowth
    P = 0.65 EBITDAMargin  + 0.35 ROA
    D = 0.60 NetDebt/EBITDA + 0.40 Debt/MarketCap     (both LOWER is better)

CompanyScore     = 0.75 x CompanyRawScore       clamped [-75, +75]
FinalEquityScore = CompanyScore + SectorOverlay  clamped [-100, +100]
```

Fully expanded:

```
CompanyRawScore = 0.40V + 0.1125RG + 0.0875RA + 0.05EG
                + 0.13EM + 0.07ROA + 0.09ND + 0.06DM
```

### Valuation (40%)

The only factor not scored by percentile rank. It asks what the share price
*would* be if the company traded at its peer cohort's EV/EBITDA:

```
peers     = same Yahoo industry (sector fallback if thin)
cohort    = peers whose EBITDA sits in the 50th-75th percentile
EV_i      = MarketCap_i + TotalDebt_i - Cash_i
Benchmark = winsorized mean of cohort EV_i / EBITDA_i

ImpliedEV          = CompanyEBITDA x Benchmark
ImpliedEquityValue = ImpliedEV - CompanyDebt + CompanyCash
ImpliedSharePrice  = ImpliedEquityValue / SharesOutstanding
ValuationGap       = (ImpliedSharePrice - CurrentPrice) / CurrentPrice

V = 100 x tanh(2 x ValuationGap)
```

A positive gap means the company looks undervalued against the cohort. `tanh`
saturates, so a wild gap cannot dominate: +25% scores about +46, +50% about
+76, and the curve never exceeds ±100.

**The cohort is deliberately the 50th-75th percentile band, not the top
quartile.** That band structurally holds only ~25% of the peer set, so a usable
cohort needs roughly 12+ valid peers in the industry. Below that the engine
widens to the sector and says so; below that again, valuation is reported
unavailable and its 40% is renormalised across the other three factors.

### Score classification

| Score | Label |
|---|---|
| +40 to +100 | BULLISH |
| 0 to +40 | SEMI-BULLISH |
| -40 to 0 | SEMI-BEARISH |
| -100 to -40 | BEARISH |

### Missing data

Never a silent zero, at either level. A missing component is dropped and the
remaining weights inside its category are renormalised; a missing major factor
is dropped and V/G/P/D are renormalised across what is left. Everything dropped
is named on screen, and the benchmark level (Industry or Sector Fallback) is
always shown.

## Data source

Equity fundamentals come from Yahoo Finance; macro comes from FRED. Those are
the only two feeds, and each lives behind exactly one module —
`market_data.py` and `macro_data.py` — so nothing downstream knows where a
number came from.

### Yahoo (equities, ETFs, prices)

No API key, no account, no crumb/cookie handshake, and no scraping of the HTML
site — just three public query endpoints:

| Endpoint | Supplies |
|---|---|
| `v8/finance/chart` | current price, currency, and the cleanest existence check (404 on a bad symbol) |
| `v1/finance/search` | company name, `quoteType`, sector, industry |
| `ws/fundamentals-timeseries` | revenue, total debt, forward P/E, market cap, trailing P/E, EPS |

`fetch_raw_info` in `market_data.py` assembles those into one flat payload and
is **the only function in the project that touches the network**. Point it
somewhere else and nothing downstream changes.

Yahoo's search is fuzzy, so only an *exact* symbol match is accepted —
otherwise `ASDFXYZ` would silently resolve to whatever Yahoo suggests.

Fundamentals are taken from the most current variant available: `trailing*`
(rolling, as-of-today) for revenue and forward P/E, and the latest reported
quarter for total debt, falling back to the fiscal year. Each of those carries
its own **as-of date**, shown in the DATA USED panel — a figure's retrieval
time is not the same as its reporting date, and the model shows both.

Requests fail over between `query2` and `query1`, retry twice per host, back off
on HTTP 429, and are throttled to roughly 16/second across all threads.

One analysis costs 3 requests for the company plus 2 per peer (peers skip the
price call), so about 40 requests — roughly 2.5–3 seconds cold, instant on a
cache hit.

Forward EPS is not published on these endpoints, so it is *derived* as
`price / forward P/E` and labelled as derived wherever it appears.

### FRED (macro)

`macro_data.py` pulls 15 FRED series plus the Yahoo macro symbols into a single
`MacroSnapshot`, which is what every macro engine scores from. It needs
`FRED_API_KEY` in the environment — the key is never written to the repo, a
config file, or a log. A failed request is scrubbed before its message is
stored or displayed, because a `requests` exception carries the full request
URL and that URL carries the key.

| Group | FRED series |
|---|---|
| Policy and curve | `DFF` effective fed funds, `DTB3` 3m bill, `DGS2`, `DGS10`, `DGS30` |
| Inflation | `CPIAUCSL` headline CPI, `PCEPILFE` core PCE, `T10YIE` 10y breakeven, `DFII10` 10y real yield |
| Credit | `BAMLC0A0CM` IG OAS, `BAMLH0A0HYM2` HY OAS, `MORTGAGE30US` |
| Growth and dollar | `UNRATE`, `PAYEMS`, `DTWEXBGS` broad dollar |

FRED does not carry spot instruments, so 14 more come from Yahoo: `^VIX`,
`^VIX3M`, `^TNX`, `^IRX`, `DX-Y.NYB`, and the front futures for WTI, Brent,
natural gas, gold, silver, copper, corn, wheat and soybeans.

A series that does not come back is reported as unavailable by name. It is
never forward-filled past its own last observation, and never substituted with
a related series that happens to be present.

## The sector model

Four factors, each normalised to [-100, +100] by **percentile rank across
sectors** — never by raw magnitude, so one extreme observation cannot dominate.

| Factor | Weight | Direction | Raw value |
|---|---|---|---|
| Growth acceleration vs VTI | 40% | higher better | market-cap-weighted sector acceleration − VTI acceleration |
| Mean ROE | 21% | higher better | winsorized mean of net income / shareholders' equity |
| Mean ROA | 14% | higher better | winsorized mean of net income / total assets |
| Debt / Market Cap | 25% | **lower better** | winsorized mean of total debt / market cap |

```
ascending rank across sectors, average ranks for ties
p = (rank - 1) / (N - 1)

higher-is-better:  FactorScore = 200p - 100
Debt/MarketCap:    FactorScore = 100 - 200p

SectorRawScore = 0.40G + 0.21ROE + 0.14ROA + 0.25D    clamped [-100, +100]
SectorOverlay  = SectorRawScore / 4                   clamped [ -25,  +25]
```

**Growth acceleration** is price performance, not revenue. Using ~3-month
trading windows on adjusted closes:

```
recent   = P_t      / P_(t-63)  - 1
previous = P_(t-63) / P_(t-126) - 1
acceleration = recent - previous          (market-cap weighted per sector)
GrowthAccelerationRaw = sector acceleration - VTI acceleration
```

Positive means the sector's price performance is accelerating relative to the
total US market. Companies with fewer than 127 days of history are excluded and
the sector's market-cap weights are renormalised over the remainder.

### Winsorization — a deliberate deviation worth knowing about

Company observations are winsorized at the 5th/95th percentile before the
sector mean is taken. The implementation is **count-based** (the
`scipy.stats.mstats.winsorize` convention): the k lowest and k highest
observations are pulled in to the next value inward.

An *interpolated* percentile would not have done the job. With n = 20 and a 95%
bound, linear interpolation lands between the top two observations, so an
outlier partly sets its own cap — in testing, a single 500× ROE still dragged a
sector mean from 0.10 to 1.35. Count-based bounds are taken from observations
strictly inside the tail, which removes that entirely.

One further deviation: `floor(0.05 * n)` is **0 for every n < 20**, which would
leave a 12-company sector with no outlier protection at all. `MIN_WINSOR_TRIM`
forces at least one observation in from each tail once the sample can afford it.
Both choices are in `sector_scoring.winsorize` with the reasoning inline.

### Missing data

Identical principle to the company model: **missing never becomes zero.** An
unavailable factor is dropped and the remaining weights are renormalised
proportionally. If ROA is unavailable, G/ROE/D are reweighted over 0.86, and the
UI names the dropped factor.

A sector is reported as *unscoreable* rather than guessed at when it has fewer
than 3 eligible companies, or when fewer than 2 factors could be calculated.
Individual factors need 5 usable observations.

### Confidence

HIGH / MEDIUM / LOW, from factor availability, per-factor coverage and eligible
company count. It is **informational only and never modifies the directional
score**, per spec.

## The tradeable universe

`data/universe.xlsx` is the source of truth. All eight asset-class sheets load:

| Sheet | Asset class | Count | Scored today |
|---|---|---|---|
| Equities & funds | Equity (Stock/ADR/REIT) | 645 | **yes** |
| Equities & funds | ETF / Crypto / Index / Preferred | 179 | no |
| Bonds | Bond | 321 | no |
| Futures | Future | 301 | no |
| CDS names | CDS | 164 | no |
| Commodities | Commodity | 57 | no |
| OTC derivatives | OTC Derivative | 28 | no |
| Currencies | FX | 26 | no |

**1,721 assets total; 645 currently carry a real Shaffer Score.** Everything
else appears in the terminal with *"Score model not yet implemented"* — never a
fabricated number.

Symbols are translated to Yahoo form: `0700-HK` becomes `0700.HK`, while a
share class like `BRK-B` is left alone (only known exchange suffixes convert).
Bonds, OTC products and CDS reference entities get no Yahoo symbol at all,
because guessing one would be fabrication.

Seven symbols legitimately exist in two asset classes — `CL` is Colgate *and*
WTI crude, `ZS` is Zscaler *and* soybeans, `NOK` is Nokia *and* the Norwegian
krone. The schema keys on (symbol, asset class); a bare lookup prefers the
scoreable equity and both remain discoverable.

The workbook's sector column is GICS ("Information Technology"); Yahoo uses its
own vocabulary ("Technology"). Yahoo's classification stays authoritative,
since that is what the sector model was built and validated on.

## Storage and the daily snapshot

SQLite, created on first run at `shafferfineval.db`.

| Table | Holds |
|---|---|
| `assets` | the workbook universe |
| `current_scores` | latest mutable state, refreshed as often as you like |
| `score_history` | **immutable** daily snapshots |
| `positions` | direction, quantity, average cost, notes |
| `watchlist` | assets you follow |
| `hedge_recommendations` | preferred strategy, ticket, breakdown |
| `fundamental_history` | observed fundamentals and what changed |
| `refresh_runs` | job log |

### Immutability

Once a **close** snapshot exists for (asset, date, model version) it is never
rewritten. A second close refresh on the same day returns `exists` and changes
nothing. If Yahoo later revises accounting history, the old row keeps what was
known at the time — that is what makes the score honestly backtestable.

**Intraday** snapshots are a separate, explicitly replaceable kind, so repeated
manual refreshes during the day cannot disturb the official close.

Every snapshot carries its model version (`equity_model_v1`, `sector_model_v1`,
`hedge_model_v1`), so old scores are never reinterpreted under a newer formula.

### Change detection

Fundamentals (revenue, EBITDA, net income, total assets, total debt, cash,
shares outstanding) are stored each run and compared. A move beyond 0.1%
records a change with its before/after and shows up in **What changed** — no
earnings calendar needed. A first observation is not a change. Price-driven
score moves need no fundamental change at all, which is why the score can move
daily.

### Schedules

Per asset class, so future engines can settle on their own clocks. US equities
are active at **16:30 America/New_York**; FX (17:00 ET), commodities (14:30 ET),
futures (17:00 ET), crypto (00:05 UTC) and bonds (15:30 ET) are declared but
inactive until their models exist.

## Caching

`app.py` wraps each network call in `@st.cache_data(ttl=3600)` — one hour,
which suits fundamentals. Peer rows are cached separately from company data, so
analysing a second company in the same sector reuses the peer fetch. Keeping
the cache decorators in `app.py` is what leaves `market_data.py` free of any
Streamlit dependency.

A cold analysis is roughly 2.5–3 seconds; re-analysing the same ticker, or a
different one in the same sector, is effectively instant for the next hour.

## Yahoo data limitations found while building this

- **Yahoo does not publish ROE or ROA** on the public query endpoints. Those
  live in the crumb-gated `financialData` module. Both are therefore computed
  from statement fields: TTM net income over the latest reported quarter's
  shareholders' equity / total assets. Spot-checked against JPM (ROE 17.4%,
  ROA 1.30%) — correct for a large bank. `company_roe` / `company_roa` still
  prefer a supplied value first, so a future source can short-circuit the
  calculation.
- **No forward-EPS field**, so forward EPS is derived as `price / forward P/E`
  and labelled as derived.
- **Total debt has no `trailing` variant**; the latest reported quarter is used,
  falling back to the fiscal year. This matters: NVDA's annual figure is $11.0B
  against $38.4B last quarter, which would have scored it roughly 30 points too
  bullish on the company model.
- **Negative or zero shareholders' equity** makes ROE meaningless (a negative
  denominator flips the sign), so those companies are excluded from the factor
  rather than contributing a misleading positive.
- Sector and industry come from `v1/finance/search`, which is fuzzy — only an
  exact symbol match is accepted.
- **Yahoo publishes no EBITDA for banks.** In a live run, 0 of 7
  bank-industry companies had an EBITDA figure. For a bank this removes
  Valuation (40%), EBITDA margin (13% of the total) and Net Debt/EBITDA (9%) —
  about 62% of the model — leaving ROA, revenue growth/acceleration and
  Debt/Market Cap to carry the score after renormalisation. JPM scores this way
  today. Treat bank scores as materially thinner evidence than industrials or
  technology, and read the dropped-factor notes on screen.
- **No ROE/ROA fields**, as noted above; both are computed from statements.
- Enterprise value is computed explicitly as `MarketCap + TotalDebt - Cash`,
  where cash prefers *cash and short-term investments*. That choice reproduces
  Yahoo's own `trailingEnterpriseValue` exactly — NVDA reconciles to the dollar
  (5,343,035,690,000), which is how the EV arithmetic is verified.
- Growth uses the **annual** revenue and EBITDA series (3 and 2 points
  respectively) rather than trailing figures, so the periods being compared are
  genuinely comparable. Fiscal year-ends differ between companies; each
  company's growth is measured against its own prior year.

## Build order

1. **Sector score** — done.
2. **Individual-company score** — done.
3. **Sector overlay + company score combined** — done.
4. **Hedge-selection engine** — done.
5. **Multi-asset terminal** (universe, database, three pages, daily refresh) — done.
6. **Shaffer Predicted Return + ML Lab** — done, and accumulating evidence.
7. **Multi-asset v1 arithmetic, GPI, synthetic validation, blotter,
   counterfactuals** — done.
8. **Daily snapshot accumulation at six horizons** — done. This is the thing
   that has to run every day; nothing downstream exists without it.
9. **FinSim blotter connected, entry states frozen, closed positions graded** —
   done.
10. **Non-equity classes, in order of what the data allows** — rates, gold,
    volatility and the market-level credit legs are live off FRED. ETFs, FX,
    oil, natural gas, the other metals and structured credit stay blocked and
    say why; none of them is faked to look finished.

**Not done, and deliberately so:** GPI stays manual. It takes structured event
fields — type, severity components, exposure, half-life — not an LLM's opinion
about whether a headline sounds bullish. And no historical fundamentals are
backfilled: prices and returns can be reconstructed honestly, but if we do not
know what revenue, EBITDA or debt were *known* to be on a past date, that row
does not go into a training set pretending to be point-in-time.

The V1 company model (forward P/E and Debt/Revenue vs sector peers) is
**retired**. `scoring.py` now holds only the shared four-band classification;
the model that replaced it is `company_scoring.py`.

## Extending the model

Both engines blend an arbitrary set of scored parts and renormalise over
whatever is available, so adding a factor means adding one entry to the weights
dict and one `Component`/`FactorResult` — no restructuring.

There is deliberately **no technical momentum factor** in the company model.
The design is Valuation + Growth + Profitability + Debt, with the sector
environment supplied by the overlay.

The instrument router in `market_data.py` (`INSTRUMENT_TYPES`,
`SUPPORTED_INSTRUMENTS`) is where ETF, bond, currency and commodity engines
plug in. Today the app recognises those instruments and declines to score them
rather than applying an equity model that does not fit.

## Not investment advice

This is a transparent arithmetic opinion built from two ratios. It is not a
recommendation.
