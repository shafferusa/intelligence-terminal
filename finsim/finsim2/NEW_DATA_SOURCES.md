# New data sources — audit (2026-09-25)

What new information the Shaffer research can actually obtain, from the domains FinSim2 is allowed to fetch
(`docs/RUNBOOK.md`) with the keys that exist (checked by presence only; key values are never printed or stored).
Every source was probed on 2026-09-25 with at most a few requests; nothing was scraped and nothing unavailable was
reconstructed.

**Status**
* **AVAILABLE** — point-in-time history long enough for the full unseen-era protocol (training before 2009 / 2018).
* **LIMITED HISTORY** — genuine point-in-time data, but it starts too late for the pre-2018 discovery test. It gets a
  separate, clearly labelled track (recent eras and the live shadow) and is never presented as historically verified;
  the historical gates are not relaxed for it.
* **LIMITED** — only a recent window or a daily quota; usable for live collection, not for historical research.
* **BLOCKED** — not obtainable from an allowed source (paid tier, authentication, or a domain not on the allowlist).
* **NOT RESEARCHED** — not probed.

| Family | Data | Provider / endpoint | Historical depth | Point-in-time | Timestamp quality | Rate limit / cost | Licensing | Status |
|---|---|---|---|---|---|---|---|---|
| Analyst consensus | Consensus EPS at report, reported EPS, surprise | Alpha Vantage `EARNINGS` | ~2000 → | estimate is the pre-release consensus as published by the vendor; not independently verifiable | report date (time of day not guaranteed) | **25 requests / day** on the free key | free tier, attribution | LIMITED (45 equities ≈ 2 days of quota; resumable collection possible) |
| Analyst consensus | Last 4 quarters' estimate / actual / surprise | Finnhub `/stock/earnings` | 4 quarters | yes | period | 60 / min | free tier | LIMITED |
| Analyst revisions | Forward EPS / revenue estimates, revision history, breadth | Finnhub `/stock/eps-estimate`, `/stock/upgrade-downgrade` | — | — | — | **403 (paid)** | premium | BLOCKED |
| Analyst recommendations | Buy / hold / sell counts | Finnhub `/stock/recommendation` | last 4 months | yes | month | 60 / min | free | LIMITED |
| Earnings events | Quarterly EPS, revenue, net income, equity with filing dates | SEC `data.sec.gov` companyfacts (already stored) | 2009 → (XBRL) | yes (first-reported, keyed by filing date) | filing date | ≤ 10 / s | public | AVAILABLE |
| Earnings events | Event-day return and volume | own price / volume panel | 2000 → | yes | daily close | — | — | AVAILABLE |
| Options surface | Chains: strike, expiry, bid / ask, IV, Greeks, volume, OI | Yahoo `v7/finance/options` | current only | — | — | **401 (needs authentication)** | — | BLOCKED |
| Options surface | Historical chains | Cboe (`cdn.cboe.com` options statistics retired; no chain history), Polygon / Massive options (paid) | — | — | — | paid | — | BLOCKED — option pricing stays "MODEL-PRICED — FLAT VOLATILITY ASSUMPTION" |
| Implied-volatility indices | VIX, VIX3M, VXN, RVX, GVZ, OVX, single-stock VIX | FRED (already stored) | 1990s → | yes | daily | FRED key | public | AVAILABLE (already used by candidate families) |
| Futures curves | Dated contracts (e.g. `CLZ26.NYM`) | Yahoo `v8/finance/chart` | only contracts not yet expired | no (expired contracts are gone, so past curves cannot be rebuilt) | daily | informal | Yahoo terms | BLOCKED for history; LIMITED for live collection |
| Positioning | CFTC Commitments of Traders | `cftc.gov` | 1986 → | yes (Friday release) | good | free | public | BLOCKED — domain not on the allowlist (add `www.cftc.gov` to enable) |
| Positioning | **Short-sale volume** (Reg SHO daily: short-marked volume / total volume) | FINRA `cdn.finra.org/equity/regsho/daily` | **2019 →** (earlier files return 403) | yes (published after the close; stored with published = next day) | daily | none (paced 0.35 s) | public | **LIMITED HISTORY** — collected: 1,937 days × 121 US-listed equities / ETFs |
| Positioning | **Short interest** (shares sold short, settlement dates) | Nasdaq `api.nasdaq.com/.../short-interest` | ~1 year (25 settlements) | yes (bi-monthly settlement) | settlement date + publication lag | informal | Nasdaq terms | LIMITED |
| Positioning | Securities lending / borrow utilisation, dealer gamma | — | — | — | — | paid only | — | BLOCKED |
| Flows | ETF flows, creations / redemptions, fund flows | — | — | — | — | no free source | — | BLOCKED |
| Breadth | Whole-market EOD aggregates (advance / decline, highs / lows) | Massive (ex-Polygon) grouped daily | 2 years on the free key | yes | daily | 5 / min | free tier | LIMITED |
| Breadth | Breadth of the FinSim2 universe (45 large-cap equities) | own price panel | 2000 → | yes | daily | — | — | AVAILABLE (narrow: large caps only) |
| Credit | Moody's Aaa and Baa yields and spreads to the 10-year | FRED `DAAA`, `AAA10Y`, `DBAA`, `BAA10Y` | 1983 / 1986 → | yes | daily | FRED key | public | AVAILABLE |
| Credit | ICE BofA IG / HY option-adjusted spreads | FRED `BAMLC0A0CM`, `BAMLH0A0HYM2` | **3 years only** (FRED licence) | yes | daily | FRED key | ICE licence | LIMITED |
| Credit | CDS, CDX / iTraxx, ratings actions, default probabilities | — | — | — | — | paid only | — | BLOCKED |
| Rates | Treasury curve 1M–30Y, real yields 5 / 10 / 30Y, breakevens | FRED `DGS*`, `DFII5/10/30`, `T5YIE`, `T10YIE` | 1962 / 2003 / 2010 → | yes | daily | FRED key | public | AVAILABLE |
| Rates | Term premium (Kim-Wright 10Y) | FRED `THREEFYTP10` | 1990 → | **medium**: a model estimate re-fitted over time; only the latest vintage is available | daily, ~1 week publication lag (stored with 7 days) | FRED key | public | AVAILABLE (flagged) |
| FX | Policy / overnight rates | FRED `DFF`, `ECBDFR`, `IUDSOIA` (daily); `IRSTCI01*` (AU, CA, CH, JP monthly) | 1954 / 1999 / 1997; monthly first releases from 2013 | yes | daily; monthly first-release dates | FRED key | public | AVAILABLE (EUR, GBP) / LIMITED HISTORY (AUD, CAD, CHF, JPY from 2013) |
| FX | 3-month interbank rates EZ / UK / JP | FRED `IR3TIB01*` | first releases from 2013 | yes (first release) | monthly | FRED key | OECD | LIMITED HISTORY |
| FX | Forward points, foreign inflation (current) | — / FRED OECD CPI (GB to 2025-03, JP to 2021) | stale | — | — | — | — | BLOCKED (forwards) / LIMITED (CPI stale) |
| Commodity fundamentals | EIA crude / gasoline / distillate inventories, SPR, refinery utilisation, natural-gas storage | `api.eia.gov` (the weekly series are not on FRED) | 1980s → | yes (Wednesday release) | good | free key | public | BLOCKED — domain not on the allowlist (add `api.eia.gov` to enable) |
| Commodity fundamentals | Rig counts, metal inventories, USDA crop reports | Baker Hughes, LME, USDA | — | — | — | — | — | BLOCKED (not on the allowlist / paid) |

**Short-sale volume is not short interest.** FINRA's Reg SHO file counts every trade marked short on a day —
including market makers' and arbitrageurs' short sales made to supply liquidity or hedge — so its ratio is high for
almost every name (in the stored 2025–26 data the per-asset median ratio is 49%, with 80% of the 120 assets between 41%
and 61%) and says little about how much stock is borrowed and held short. Short *interest* (shares sold short and not yet
covered) is a different, bi-monthly quantity; only its last year is obtainable. The research treats the short-volume
ratio as a noisy flow proxy, never as short interest, and because it starts in 2019 it cannot take part in any
discovery test before 2018.

**What would add the most future value** (in order): an analyst-revisions source with point-in-time history (e.g. a
paid estimates API), CFTC positioning (`www.cftc.gov` on the allowlist), EIA inventories (`api.eia.gov`), and a
historical option-chain source. Each of these is a data decision for the owner, not something the research can work
around.
