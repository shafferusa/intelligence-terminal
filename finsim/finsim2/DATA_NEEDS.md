# What the Shaffer System needs to work at its highest level

Written 2026-09-28. This is the complete list of data that would materially improve the Shaffer System
(SHAFFER_SYSTEM.md): what each source unlocks, whether it is free or licensed, and what "point in time" has to mean
for it. Every new source enters as a new family under the same protocol: pre-registered, walk-forward, adopted only
where it beats the calibrated prior out of sample.

**The rule that governs everything below:** a dataset is useful only if it records *what was known on each date*
(an as-of or vintage history). A database that overwrites old values with revised ones produces backtests that look
brilliant and fail live.

**Keys:** put them in environment variables with `setx NAME "value"`. Never paste them into a chat.

## What FinSim2 already has (free)

| Data | Source | Status |
|---|---|---|
| Daily prices (stocks, ETFs, indices, FX, crypto, commodities) | Yahoo Finance | 1993 → (SPY) / 1962 → (Treasury indices) |
| Macro and rates, first-release vintages | FRED | ~60 series |
| Fundamentals (as filed) | SEC XBRL company facts | 2009 → |
| Insider transactions (Form 4) | SEC | 2006 → (tested: no value) |
| 8-K event types, earnings dates | SEC + Finnhub | 1995 → |
| Futures positioning | CFTC COT | 2006 → |
| Energy inventories | EIA | 2010 → |
| Crypto funding and basis | Deribit / CME | 2018 → |
| Short-sale volume | FINRA Reg SHO | 2019 → |
| News headlines | Dow Jones RSS (being added) | forward only |

## Priority 1 — the foundation: a survivorship-free universe

**Why it comes first.** The research universe is today's ~500 most liquid US stocks. Every backtest therefore
excludes the companies that went bankrupt, were delisted or shrank, which biases all historical Alpha results
upward and understates risk. This is the single largest known distortion in the current research, and it affects
every Alpha finding, including "nothing beats production."

| Need | What it unlocks | Sources (licensed) |
|---|---|---|
| Daily prices **including delisted stocks**, with delisting returns | Unbiased Alpha backtests, honest expected returns, real bankruptcy / tail risk | **Norgate Data** (US stocks incl. delisted, from about US$300–600 a year), **Sharadar** Core US Equities via Nasdaq Data Link, CRSP (academic / institutional) |
| **Historical index membership** (S&P 500 / 400 / 600, Russell), by date | "What was in the index then" universes, no look-ahead in stock selection | Norgate (included), Sharadar, S&P / FTSE Russell |
| Historical shares outstanding and market cap | Size factor, point-in-time liquidity screens | Sharadar, Compustat |

## Priority 2 — forward-looking information (your stated next step)

| Need | What it unlocks | Sources |
|---|---|---|
| **Analyst estimate revisions, point in time**: consensus EPS / revenue by fiscal period by date, number of up / down revisions, dispersion, surprises, report dates | The best-documented stock-selection signal after momentum: revisions momentum, surprise drift, uncertainty (dispersion) for the Alpha 1M–12M | **Zacks** via Nasdaq Data Link (most affordable), **LSEG I/B/E/S** (the standard; via WRDS for academics), **FactSet Estimates**, S&P Capital IQ, Visible Alpha |
| **Historical options data**: daily implied-volatility surface (by moneyness and tenor), skew, term structure, open interest, volume | Implied volatility is the market's own move-size forecast: better Directional ranges, tail risk from skew, the variance risk premium, option-implied expected returns, realistic Shaffer Hedge pricing with real bid / ask instead of a flat-vol model | **ORATS** (retail-affordable history), **OptionMetrics IvyDB** (the academic standard), **Cboe DataShop**, Polygon options |
| **Point-in-time fundamentals** with restatement vintages | Valuation and quality families without restated-data look-ahead; pre-2009 history | **Sharadar SF1** ("as reported" dimensions, affordable), **Compustat Point-in-Time** / Snapshot, FactSet Fundamentals |

FinSim2 already has importers for estimates and options CSVs (`python -m finsim2 import-estimates` /
`import-options`; formats in NEW_DATA_SOURCES.md).

## Priority 3 — positioning, flows and crowding

| Need | What it unlocks | Sources |
|---|---|---|
| Short interest history (bi-monthly), days to cover | Crowding / squeeze risk, short-side Alpha | FINRA (recent history free), Nasdaq; longer history from Sharadar or S&P |
| Securities-lending borrow fees and utilisation | The most direct crowding and short-cost signal; realistic short P&L | S&P Global (IHS Markit) Securities Finance, Ortex, Hazeltree |
| 13F institutional holdings (quarterly, 45-day lag) | Ownership concentration, crowding | SEC 13F (free, FinSim2 can add it), WhaleWisdom (cleaned) |
| ETF and fund flows | Flow pressure on sectors and bonds | ICI weekly (free, aggregate), FactSet / ETF.com (licensed) |

## Priority 4 — text and events

| Need | What it unlocks | Sources |
|---|---|---|
| Timestamped newswire full text | News sentiment and novelty with exact timestamps, beyond RSS headlines | **Dow Jones Newswires / Factiva API** (licensed), Reuters / LSEG News |
| Pre-computed news analytics (entity-tagged sentiment, novelty, relevance) | Ready-made PIT news features | RavenPack, Bloomberg Event-Driven Feeds |
| Earnings-call transcripts | Management tone, guidance language | S&P Global transcripts, FactSet CallStreet, Seeking Alpha |
| Macro consensus forecasts (economists' median for CPI, jobs, GDP…) | Macro *surprise* (actual − consensus), which moves markets, rather than the level | Bloomberg / LSEG consensus, Trading Economics API, the Philadelphia Fed SPF (free, quarterly) |

## Priority 5 — better series for non-equity classes

| Need | What it unlocks | Sources |
|---|---|---|
| Full futures curves (every contract, back-adjusted continuous series with roll dates) | Commodity carry / term structure (the main documented commodity signal), true futures returns instead of front-month splices | CSI Data, Norgate futures, Barchart, Databento |
| FX forward points / deposit rates by currency | FX carry (a documented FX premium), correct forward pricing | LSEG, Bloomberg; partial: FRED policy rates (already in FinSim2) |
| CDS spreads, corporate bond prices (TRACE) | Credit signals for equities and bonds; credit hedge pricing | S&P (IHS Markit) CDS, FINRA TRACE (free with delay / enhanced licensed) |
| VIX term structure, SKEW, put/call ratios | Market-wide tail and sentiment features | Cboe (free historical CSVs), FRED (VXVCLS etc.) — FinSim2 can add these |
| Crypto on-chain metrics and options history | Crypto flows, holder behaviour, implied vol | Coin Metrics (community tier free), Glassnode; options history via Tardis.dev |

## Priority 6 — intraday, for Shaffer Directional (1D–1W)

| Need | What it unlocks | Sources |
|---|---|---|
| Minute or tick bars with volume; opening auction / imbalance | Overnight vs intraday decomposition, gap behaviour, intraday volatility for better 1D ranges | Polygon.io (affordable), Databento, NYSE TAQ (institutional) |

## Massive (formerly Polygon.io): the cheapest single route to priorities 1, 3 and 6

Massive (massive.com, the 2025 rename of Polygon.io) covers stocks, options, indices, currencies and crypto, and
more recently futures.

**Free tier (verify on their pricing page; it blocks this environment).**
- About 5 API calls a minute, end-of-day data, and about 2 years of history. FinSim2's own record of the free key
  says 2 years, 5 a minute.
- As far as I know, the free options tier does not include historical implied volatility, greeks or quotes.
- That is enough to *try* the API, not to research with: the walk-forward needs 15–25 years.

**Paid tiers (individual plans from roughly US$29–199 a month).** These add what matters:
- Many more years of stock history, **including delisted tickers**. That removes survivorship bias (priority 1),
  provided the tier reaches back far enough (10–20 years).
- Options history with IV, greeks and open interest (priority 3). Options history starts later, around the
  mid-2010s.
- Minute bars (priority 6).
- Bulk "flat files" for fast backfills.

**What it does not cover:**
- Point-in-time analyst estimate revisions (priority 2).
- Point-in-time fundamentals with restatement vintages. Check whether any partner add-on offers either, as of
  your sign-up date.

**To use it:** a key in `MASSIVE_API_KEY` (`setx MASSIVE_API_KEY "…"`), plus `api.massive.com` added to the
cloud environment's allowed domains. FinSim2 would then get a Massive importer for delisted-inclusive daily bars
and option-chain history, tested under the same protocol.

## FirstRate Data: "complete" bundle + fundamentals (reviewed from the samples, 2026-09-28)

These findings come from the vendor's sample files. The pricing page is blocked from the cloud environment, so
price and license terms still need checking there.

| Part | What the samples and readme show | Fills priority | Value for the Shaffer System |
|---|---|---|---|
| Stocks | ~16,300 active + **7,000+ delisted** US stocks, daily and intraday from 2000, unadjusted / split / split + dividend adjusted | **1 (survivorship)** | High: an unbiased, 5× broader cross-section for Alpha, and honest expected returns and tails |
| Options | EOD chains for 5,800 underlyings (+4,000 delisted) **from 2010**: bid / ask, bid and ask IV, delta / gamma / vega / theta / rho, open interest, volume. The sample is clean near the money: put and call IVs agree, skew is present | **3 (options)** | High for Directional ranges (implied vs realised volatility) and for the Shaffer Hedge (real prices instead of flat volatility). Modest and uncertain for Alpha (skew, IV spread) |
| Futures | 261 contracts, **each contract separately** plus continuous series, from 2008, with open interest | **5 (curves / carry)** | Medium: commodity carry and term structure, true futures returns |
| Intraday bars | 1-min … 1-hour | 6 | Medium: overnight / intraday split, 5-minute realised volatility for the move-size model |
| Earnings | Consensus EPS estimate + actual + before / after the market, per report, 2000 → | part of 2 | Medium: earnings surprise (SUE) and drift. **Not** an estimate-revision history. The sample has an odd duplicate: an unreported 2026-05-07 row after the reported 2026-04-30 one |
| Valuation metrics | Daily trailing / forward P/E, P/S, EV, 2000 → | part of 2 | Low–medium: the implied forward earnings change only in quarterly steps (13 changes a year in the AAPL sample), so it is a coarse revisions proxy at best. Whether it is point in time must be confirmed |
| Financials | 70+ fields with **filing dates** (point-in-time usable), "from 2000" (the AAPL sample starts 2011) | 4 | Medium: extends the SEC XBRL history (2009 →) |
| Short interest / volume | 2021 → | 5 | Low (short history) |
| Social sentiment | 2025 → | — | Forward research only |

**Not included:** historical index membership, and analyst estimate revisions over time.

**Practical notes:**
- Full 1-minute data for 23,000 stocks and full option chains since 2010 run to hundreds of GB or more. FinSim2
  would import daily bars for every stock, and reduce each day's chain to per-underlying features: 30-day ATM IV,
  25-delta skew, term slope, IV − RV, put / call OI. It would not store every contract.
- The data stay out of the repository, following the vendor's license.

## Free additions FinSim2 can make without any purchase

If you want them, I can add these under the same protocol:

1. **ALFRED** real-time vintages for every macro series (FRED's archive).
2. **SEC 13F** holdings.
3. **Cboe** VIX term structure (VIX9D / VIX3M / VIX6M), SKEW, and put/call ratio history.
4. **FINRA** short interest (the recent public window).
5. **Philadelphia Fed SPF** consensus.
6. **ICI** weekly fund flows.
7. **Treasury** auction results (TreasuryDirect).

Each needs its host added to the environment's allowed domains in the cloud. On your laptop nothing is needed.

## What would help most, in order

1. **Survivorship-free prices + historical index membership** (Norgate or Sharadar). This fixes the biggest bias in
   everything measured so far.
2. **Point-in-time analyst revisions** (Zacks via Nasdaq Data Link, or I/B/E/S). This is the highest-value new signal
   for Shaffer Alpha.
3. **Historical options surfaces** (ORATS). These are the best inputs for Shaffer Directional ranges and for the
   Shaffer Hedge.
4. **Point-in-time fundamentals** (Sharadar SF1).
5. Borrow fees and short interest.
6. Newswire full text / transcripts.
7. Futures curves.
8. Intraday bars.

**Being honest about expectations:** better data raises the ceiling, but it does not guarantee the Shaffer System
finds an edge. Every addition is tested the same way, and it enters the forecast only if it beats the calibrated
prior out of sample.
