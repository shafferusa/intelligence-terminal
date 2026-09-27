# New data sources — audit (2026-09-25)

## 2026-09-27 — first run on the laptop, and what it changed

| Seen | Cause | Fix |
|---|---|---|
| CFTC: 9 of 31 contracts "code not mapped" | the CFTC renamed markets (crude oil → `WTI-PHYSICAL`, notes → `UST 10Y NOTE`, dollar index → `USD INDEX`, `NAT GAS NYME`, `DJIA x $5`) | each contract accepts a list of names; stray rows are skipped; each contract/report resumes from its own last date, so the failed ones get full history |
| 35 "no fundamentals in companyfacts" | foreign private issuers (TSM, ASML, SAP, …) file IFRS on 20-F / 40-F, no US-GAAP | reported as skipped (foreign filer), not an error; re-checked weekly |
| new stocks had no insider history | quarterly SEC files were marked done for the old issuer set | issuers added since the last full pass are backfilled from every stored quarter (for them only) |
| IAU, GLDM added as "equities" | SEC lists exchange-traded trusts like companies | fund filter (sponsor names, SIC 6221); `universe --expand` and plain `python -m finsim2 universe` move already-added funds to the ETF class |
| crypto: 4 Binance errors every run | Binance refuses US connections (HTTP 451) | reported once under `unavailable`; SOL funding now from Deribit `SOL_USDC-PERPETUAL`; runs are incremental |
| EIA / Finnhub key "set" but not working | `setx` with the placeholder text | placeholder values count as missing and the message says so |

## 2026-09-27 — new sources built (`python -m finsim2 data`)

The residual / meta-learning program showed that E is close to the limit of what the current 74 signals hold, so the
next gains have to come from information FinSim2 does not have. These sources are now ingested into the point-in-time
`alt_data` store (every row carries the date it became public). They are **data only**: no signal family uses them
yet — each will be tested the usual way (a protocol fixed before results, the new-information gates, FDR) before it can
touch a score.

| Source | Dataset | Module | Key / host | Depth | Point in time | Status (2026-09-27) |
|---|---|---|---|---|---|---|
| SEC Form 4 insider transactions — open-market buys / sells, value, role (officer / director / CEO-CFO / 10%), 10b5-1 plans, size vs holding | `sec_insider` | `data/secevents.py` | none (SEC UA) | 2006 → (quarterly data sets) + recent filings one by one | filing date; used from the next day | **LIVE-TESTED** — 81 quarters, ~101k rows for the 45 equities in 3 minutes |
| SEC 8-K event types — item codes (2.02 results, 5.02 officer changes, 1.01 agreements, …), New York release time | `sec_8k` | `data/secevents.py` | none | 1994 → | acceptance time: before 16:00 NY = that day, later = next day | **LIVE-TESTED** — ~48k rows; Apple's results at 16:30 NY every quarter |
| Event calendar — FOMC decisions (scheduled / with projections / unscheduled), CPI, jobs, GDP, PPI, retail sales release days | `event_calendar` | `data/calendar.py` | FRED_API_KEY; federalreserve.gov | FOMC 2000 → 2027, data releases 1947 → 2027 | scheduled: known 30 days ahead; unscheduled FOMC: next day | **LIVE-TESTED** — 258 FOMC days (2008 and 2020 emergency actions included), 4,051 release days |
| Earnings calendar — scheduled dates, before / after the open, consensus EPS / revenue at collection | `earnings_calendar` | `data/calendar.py` | FINNHUB_KEY | forward 90 days, collected daily from now (history comes from 8-K 2.02) | collection date | **LIVE-TESTED** — builds a forward history from today |
| CFTC Commitments of Traders — managed money, producers, swap dealers, leveraged funds, asset managers, dealers, open interest; 31 contracts linked to 50 assets | `cftc_cot` | `data/cftc.py` | none; `publicreporting.cftc.gov` | 1986 → (legacy), 2006 → (disaggregated / TFF) | Tuesday positions used from Saturday; shutdown weeks from the catch-up date | **FIXTURE-TESTED** — the host is blocked in the cloud research environment; runs on your machine |
| EIA weekly energy — crude / gasoline / distillate / Cushing / SPR stocks, refinery utilisation, production, imports, exports, product supplied, natural-gas storage | `eia_weekly` | `data/eia.py` | **EIA_API_KEY** (free); `api.eia.gov` | 1980s → | petroleum: Thursday after the week; gas: Friday | **FIXTURE-TESTED** — needs the free key; latest vintage (rare, small revisions) |
| Crypto derivatives — perpetual funding (Deribit, else Binance), premium index (Binance), CME basis (Yahoo) | `crypto_deriv` | `data/cryptoderiv.py` | none; `www.deribit.com`, `fapi.binance.com`, Yahoo | 2019 → | UTC day used from the next day | **PARTIAL** — CME basis live-tested (BTC 2019 →, ETH 2021 →; noisy: CME settles 16:00 NY, spot closes at midnight UTC); funding fixture-tested. **Binance refuses US connections**, so from a US machine funding comes from Deribit (BTC, ETH) and SOL has none. CME bitcoin positioning comes from CFTC. |
| Analyst estimates — consensus EPS / revenue by horizon, dispersion, analyst count, revisions | `analyst_estimates` | `data/imports.py` | licensed (FactSet, LSEG I/B/E/S, Zacks) | whatever you buy | the as-of date of the consensus (+1 day unless the file says) | **IMPORTER READY** — `python -m finsim2 import-estimates file.csv` |
| Historical options — ATM IV 30/60/90, 25-delta skew, put / call volume and OI, IV rank | `options_summary` | `data/imports.py` | licensed (ORATS, Cboe DataShop, OptionMetrics) | whatever you buy | the trading day (+1 day unless the file says) | **IMPORTER READY** — `python -m finsim2 import-options file.csv` |
| Wider universe — the N most liquid NYSE / Nasdaq common stocks (e.g. 500 – 1,500) with CIK and SIC sector | assets | `data/expand.py` | none; SEC + Yahoo | each stock's full Yahoo history | **survivorship bias**: today's listings ranked by today's liquidity — delisted names are missing; added assets carry `meta.expanded` | **READY** — `python -m finsim2 universe --expand 500` (≈ 1 hour to rank ~5,800 candidates once) |

News: a consumer WSJ / MarketWatch / Barron's subscription does not permit automated downloading (Dow Jones'
subscriber terms), so FinSim2 does not log in to it. Licensed news with timestamps (Dow Jones Factiva / Newswires / DNA)
would be the route for text data; the free, point-in-time alternative already here is the 8-K event stream.

Hosts added for FinSim2 by the owner's decision (2026-09-27): `publicreporting.cftc.gov`, `api.eia.gov`,
`www.deribit.com`, `fapi.binance.com`, `finnhub.io`, `www.federalreserve.gov`. SEC and FRED rules are unchanged (the
LoganTerminal user agent, ≤ 10 SEC requests / second).


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
