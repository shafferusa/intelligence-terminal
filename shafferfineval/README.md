# SHAFFERFINEVAL

A small, standalone quantitative scorer. Type a ticker and get:

1. a **sector score** (-100..+100) and the **company overlay** (-25..+25) it implies, and
2. a **company house score** (-100..+100) with a Bullish / Semi-Bullish /
   Semi-Bearish / Bearish verdict,

with every input and intermediate number shown.

The two are currently independent: the sector overlay is **calculated and
displayed but not yet applied** to the company score. Wiring them together is
the next step.

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
python3 test_scoring.py          # company model, 60 checks
python3 test_sector_scoring.py   # sector model, 134 checks
```

## Files

| File | Role |
|---|---|
| `app.py` | Streamlit UI only. Layout, charts, formatting, caching. No math. |
| `scoring.py` | Company model. **Zero third-party imports.** |
| `sector_scoring.py` | Sector model. **Zero third-party imports**, and no market-data imports either — structured data in, structured data out. |
| `universe.py` | Tradeable-universe workbook loader (stdlib `.xlsx` reader) and the non-equity pre-filter. |
| `market_data.py` | Yahoo access and normalisation. Retrieval only. |
| `test_scoring.py` | 60 offline checks on the company model. |
| `test_sector_scoring.py` | 134 offline checks on the sector model. |
| `requirements.txt` | Dependencies. |

`scoring.py` never imports `market_data.py`, and neither imports `app.py`. To
reuse the engine elsewhere, take `scoring.py` and feed it numbers.

## The company model (V1, unchanged)

Two factors, both measured **against sector peers** rather than in absolute
terms. Lower leverage than the peer group is bullish; lower valuation than the
peer group is bullish.

```
DebtRevenue  = Total Debt / Annual Revenue

L = -50 x ln( DebtRevenue_company / DebtRevenue_sector )
V = -50 x ln( ForwardPE_company  / ForwardPE_sector  )

EquityScore = 0.55L + 0.45V        clamped to [-100, +100]
```

| Score | Label |
|---|---|
| +40 to +100 | BULLISH |
| 0 to +40 | SEMI-BULLISH |
| -40 to 0 | SEMI-BEARISH |
| -100 to -40 | BEARISH |

The `-50` scale means a company at `1/e` of the sector ratio scores `+50`, and
one at `e` times the sector ratio scores `-50`.

## How messy data is handled

Nothing is ever silently replaced with a made-up number. Every fallback is
labelled on screen.

| Situation | Behaviour |
|---|---|
| Total debt is zero | Treated as extreme low leverage, pinned to **+100**. No `ln(0)`. |
| Revenue zero or missing | Debt/Revenue marked **unavailable**; leverage is dropped from the average and the remaining weight is renormalised. |
| Forward P/E missing, zero, negative, infinite or absurd | Valuation scores a **disclosed -50** with the message *"Forward P/E unavailable/invalid due to negative or unavailable expected earnings."* |
| Both factors unscoreable | No house score. The page says so. |
| Peer with bad data | Excluded from the median but still shown in the peers table, so you can see the exclusion. |
| Fewer than 5 usable peers | Benchmark flagged **LOW CONFIDENCE** in the output and in the written explanation. |
| Ticker does not resolve | `Ticker not found.` — no traceback. |
| Symbol is an ETF, index, currency, future | Identified and reported; **not** scored with the equity model. |

Factor scores are clamped to `[-100, +100]` individually as well as in
aggregate, since a logarithm of an extreme ratio is otherwise unbounded.

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

## Sector peers

Yahoo's free tier will not enumerate a whole sector, so V1 uses a **curated
peer universe**: roughly 20 large, liquid names per Yahoo sector, in
`SECTOR_PEER_UNIVERSE` at the top of `market_data.py`. Edit that dict to change
the benchmark — it is the single place peer selection lives.

The group is then **narrowed to the company's own industry** when at least 5
of those names share it. That is how NVDA is benchmarked against
*Semiconductors* rather than all of *Technology*. Below that threshold it falls
back to the full sector and says so.

Medians, never means, so one extreme company cannot move the benchmark. The
**PEERS USED** table at the bottom of the page lists every peer with its
Debt/Revenue and forward P/E, so the sector number is never mysterious.

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

## Known limits of V1

- **Debt/Revenue is a poor leverage measure for banks.** Revenue is not a
  meaningful denominator for a balance-sheet business, so financials score
  with very high ratios across the board. The comparison is still peer-relative
  and therefore internally consistent, but treat the leverage leg for
  Financial Services as weak evidence until Debt/EBITDA and a
  financials-specific branch land.
- The peer universe is US large-cap. A small-cap or non-US listing is measured
  against large-cap peers in its sector.
- These are public but *unofficial* Yahoo endpoints. They are not covered by a
  support contract and their shapes can change without notice. Fields go
  missing; the app labels them rather than guessing.
- Forward P/E is a consensus estimate, not a fact.

## Build order

This is step 1 of 4. The company formula has **not** been changed.

1. **Sector score** — done. `SectorOverlay` is exposed and displayed.
2. Individual-company score (expanded model).
3. Sector overlay + company score combined.
4. Hedge-selection engine.

## Planned expansion

`scoring.py` is already shaped for it. `build_equity_score` consumes an
arbitrary list of `FactorResult` objects and renormalises weights over whatever
is available, so adding a factor means appending one `FactorResult` and
widening `FACTOR_WEIGHTS`. The target shape is in `FUTURE_FACTOR_WEIGHTS`:

```
EquityScore = 0.25 Valuation + 0.20 Leverage + 0.20 Growth
            + 0.20 Quality   + 0.15 Momentum
```

- **Valuation** — Forward P/E, EV/EBITDA, Price/Sales, FCF yield
- **Leverage** — Debt/Revenue, Debt/EBITDA, Net Debt/EBITDA
- **Growth** — revenue growth, EPS growth, forward earnings growth
- **Quality** — ROIC, ROE, FCF margin, operating margin, interest coverage
- **Momentum** — 1/3/6/12-month return, moving averages, estimate revisions

The instrument router in `market_data.py` (`INSTRUMENT_TYPES`,
`SUPPORTED_INSTRUMENTS`) is where ETF, sector, bond, currency and commodity
engines plug in. V1 recognises those instruments and declines to score them
rather than applying an equity model that does not fit.

## Not investment advice

This is a transparent arithmetic opinion built from two ratios. It is not a
recommendation.
