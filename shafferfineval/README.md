# SHAFFERFINEVAL

A small, standalone quantitative scorer. Type a ticker and get:

1. a **company score** (-75..+75) from valuation, growth, profitability and debt
   measured against industry peers,
2. a **sector overlay** (-25..+25) from the sector engine, and
3. the **final equity score** (-100..+100) with a Bullish / Semi-Bullish /
   Semi-Bearish / Bearish verdict,

with every input and intermediate number shown.

```
FinalEquityScore = CompanyScore + SectorOverlay
```

Completely separate from the intelligence-terminal reporting routines — it
shares no state, no config and no code with them.

## Run it

```bash
cd shafferfineval
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Streamlit opens http://localhost:8501. Enter a ticker (e.g. `NVDA`) and press
**ANALYZE**. No login, no API key, no database.

Run the tests (pure stdlib — no install needed):

```bash
python3 test_scoring.py           # classification + shared statistics
python3 test_sector_scoring.py    # sector model
python3 test_company_scoring.py   # company model
```

## Files

| File | Role |
|---|---|
| `app.py` | Streamlit UI only. Layout, charts, formatting, caching. No math. |
| `company_scoring.py` | Company model. **Zero third-party imports**, no market-data imports. |
| `sector_scoring.py` | Sector model. Same contract. |
| `statlib.py` | Shared statistics (winsorize, ranks, percentiles) so both engines normalise identically. |
| `scoring.py` | Shared four-band classification. |
| `universe.py` | Tradeable-universe workbook loader (stdlib `.xlsx` reader) and the non-equity pre-filter. |
| `market_data.py` | Yahoo access and normalisation. Retrieval only. |
| `test_scoring.py` | Offline checks on classification and `statlib`. |
| `test_sector_scoring.py` | 134 offline checks on the sector model. |
| `test_company_scoring.py` | 150 offline checks on the company model. |
| `requirements.txt` | Dependencies. |

`scoring.py` never imports `market_data.py`, and neither imports `app.py`. To
reuse the engine elsewhere, take `scoring.py` and feed it numbers.

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

Yahoo Finance, exclusively. No API key, no account, no crumb/cookie handshake,
and no scraping of the HTML site — just three public query endpoints:

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

The sector model scores the companies in *your* tradeable universe, read from
the asset workbook rather than a hardcoded list.

Place it at `data/universe.xlsx` (also accepted: `universe.csv`,
`tradeable-assets.xlsx`, `assets.xlsx`, `instruments.xlsx`, in `.`, `data/`, or
`config/`), or point `$SHAFFERFINEVAL_UNIVERSE` at it. The loader finds the
header row even under title rows, and matches `ticker`/`symbol`/`code`,
`name`/`company`/`security`, `sector`, and `type`/`asset class` columns however
they are spelled.

`.xlsx` is parsed with stdlib `zipfile` + `xml.etree` — no openpyxl or pandas
dependency.

Filtering happens in two stages:

1. **Cheap pre-filter** (`universe.py`) drops rows whose type column or symbol
   shape marks them as ETFs, indices, bonds, preferreds, options, futures,
   crypto, currencies, commodities or mutual funds. This exists only to avoid
   spending HTTP requests on instruments that cannot qualify.
2. **Authoritative filter** (`market_data.py`) keeps only symbols Yahoo reports
   with `quoteType == "EQUITY"` and a real sector.

Stage 1 is an optimisation. Stage 2 is the rule.

**If no workbook is found**, the engine falls back to the built-in large-cap
list so it still runs, and the UI shows a prominent PROVISIONAL warning naming
every place it looked. The "Universe used for the sector model" panel always
reports which universe produced the scores, how many rows were read and kept,
what was excluded and why, and how many symbols resolved.

Universe fetches are capped at `MAX_UNIVERSE_COMPANIES` (400) and cost 3
requests per company, so a cold whole-market build takes roughly 50-60 seconds.
It is cached for 6 hours.

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

Steps 1-3 are done.

1. **Sector score** — done.
2. **Individual-company score** — done.
3. **Sector overlay + company score combined** — done.
   `FinalEquityScore = CompanyScore + SectorOverlay`.
4. Hedge-selection engine — next.

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
