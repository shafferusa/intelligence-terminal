# MARKET SIGNAL ENGINE

A small, standalone quantitative equity scorer. Type a ticker, get a house
score from **-100 to +100** and a **Bullish / Semi-Bullish / Semi-Bearish /
Bearish** verdict, with every input and intermediate number shown.

Completely separate from the intelligence-terminal reporting routines — it
shares no state, no config and no code with them.

## Run it

```bash
cd market-signal-engine
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Streamlit opens http://localhost:8501. Enter a ticker (e.g. `NVDA`) and press
**ANALYZE**. No login, no API key, no database.

Run the scoring tests (pure stdlib — no install needed):

```bash
python3 test_scoring.py
```

## Files

| File | Role |
|---|---|
| `app.py` | Streamlit UI only. Layout, charts, formatting, caching. No math. |
| `scoring.py` | The model. **Zero third-party imports** — copy it straight into another project. |
| `market_data.py` | Yahoo access, normalisation, peer universe, sector medians. |
| `test_scoring.py` | 60 offline checks on the model and its edge cases. |
| `requirements.txt` | Dependencies. |

`scoring.py` never imports `market_data.py`, and neither imports `app.py`. To
reuse the engine elsewhere, take `scoring.py` and feed it numbers.

## The V1 model

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
