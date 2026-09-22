# ShafferFinEval — Historical Point-in-Time Data Map & Schema

**Status: PART BUILT. The foundation — schema, policies, identity, DERA ingest, calendar, option
archive — is implemented and tested. The replay itself is not written.** §0.1 is the line between
the two, and it is the first thing to read.

Date of the survey: 2026-09-20. Every empirical claim was verified by live fetch on this machine on
that date. The design was written, then adversarially attacked by five independent reviewers
(leakage, schema, claim accuracy, guardrails, completeness), then corrected. **Three headline claims
in the first draft were wrong and are corrected below** — they are called out explicitly rather than
quietly fixed, because the corrections are themselves findings.

This document answers one question:

> What history can ShafferFinEval obtain **honestly**, and what shape must it be stored in so that a
> replay at date *t* sees only what was knowable at date *t*?

**Where the document and the code disagree, the code wins.** This document is a description of a
system that now exists, not a proposal for one. The latency rule in §2.1.1 was rewritten on
2026-09-20 for exactly that reason: it carried a draft rule (`16:00`, measured from `filed`) that
the implementation never used.

---

## 0.1 Built versus planned

| Area | Status | Where it lives |
|---|---|---|
| PIT schema, triggers, CHECKs, selectors | **BUILT** | `pit_store.py` — 30 tables, 4 immutability triggers |
| Information latency policy `information_latency_policy_v1` | **BUILT** | `pit_policy.py` §1; tested in `test_pit_policy.py` |
| Concept ladders `concept_ladder_v1` (13 concepts, ASC 606 gate) | **BUILT** | `pit_policy.py` §2 |
| Staleness bounds + EBITDA assembly spec | **BUILT** | `pit_policy.py` §3–4 |
| Entity / listing identity, exit dating, peer + scored universes | **BUILT** | `pit_identity.py`; gated by `test_pit_identity.py` |
| Survivorship gate (the build gate) | **BUILT AND PASSING** | `test_pit_identity.py` — see §5 |
| Trading calendar | **BUILT** — 8,467 sessions 1993-01-29 → 2026-09-18, 165 month-ends from 2013 | `pit_ingest.py calendar` → `pit_calendar` |
| DERA bulk fundamentals ingest | **BUILT**, archive run in progress | `pit_dera.py`, driven by `pit_ingest.py dera` |
| Option-chain archive | **BUILT AND RUNNING from 2026-09-20** | `pit_options.py` → `pit_option_snapshot` + gzipped JSONL |
| Model lineage record | **BUILT** | `pit_lineage_seed.py`, `docs/MODEL-LINEAGE.md` |
| The two leakage fences (`--date` trap, unpurged splitter) | **BUILT** | `refresh.resolve_snapshot_date`, `test_pit_hazards.py` |
| Per-issuer share-count complement (cover-page shares) | **IN FLIGHT** — mandatory, see §2.1.4 | `pit_shares.py` (not reviewed here) |
| Prices, corporate actions → `pit_price_bar`, `pit_corporate_action` | **PLANNED** | tables exist, empty |
| Macro vintages, curve → `pit_macro_obs`, `pit_curve_obs` | **PLANNED** | tables exist, empty |
| Peer sets → `pit_peer_set` | **PLANNED** (the selector exists; the snapshotting run does not) | — |
| **Replay** → `pit_feature`, `pit_score`, `pit_sector_score` | **PLANNED — not one line written** | — |
| Labels, purged folds, challengers, proposals | **PLANNED** | tables exist, empty |

**Nothing in the Shaffer production core has been changed by any of this.** Factor weights, scaling
constants, sector formulas, classification bands, the 0.20 calibration, the hedge arithmetic, the GPI
rule and the asset-class v1 formula are all exactly as they were. The findings in this document are
**recorded, not patched**.

---

## 0. Guardrail status — honest, not aspirational

The first draft claimed all six guardrails were met. Audited, that was too generous. What a guardrail
needs is a **mechanism** — something that fails loudly when violated — not a rule in prose.

| # | Guardrail | Enforcement | Status |
|---|---|---|---|
| 1 | SEC facts by filed date / accession | `pit_fact` UNIQUE includes `accn`; `pit_store.fact_as_of` orders by `available_date DESC, filed DESC, accn DESC` where `available_date <= t`; it is the only sanctioned read path | **BUILT** — mechanism in place. Direct `period_end` queries are still banned by convention, not by test |
| 2 | Point-in-time peer sets | `pit_peer_set` snapshotted per as-of date, keyed on `entity_id`; SIC read per filing | **Selector BUILT** (`pit_identity.peer_universe_as_of`, verified row-for-row against `pit_store.sic_as_of`); **snapshotting PLANNED** |
| 3 | Corporate actions reconstructed | `pit_corporate_action`; splits/dividends stored raw; exit dated from three sources | **Exit dating BUILT and gated** (§5.1). Splits/dividends **PLANNED**. Ticker changes, ADR changes: **no free dated source, permanently partial** (§5) |
| 4 | True macro vintages | `pit_macro_obs` keyed `(series_id, obs_date, vintage_date)` + an explicit vintage selector + per-series archive floors | **Schema and selector BUILT** (`pit_store.macro_value_as_of`); **ingest PLANNED** |
| 5 | As-of timestamp on every row | `CHECK (feature_available_date <= as_of_date)` in SQLite, plus a future-data canary test | **Column BUILT; selector untested** — the CHECK validates a value, not the query that chose it, and the canary test in §10 cannot be written until the replay exists |
| 6 | Purge/embargo matched to horizon | `ml_fold` records `purge_days`, `embargo_days`, `embargo_rule`, `n_purged` | **Table BUILT; splitter PLANNED.** The unpurged `ml_lab.walk_forward_splits` **is now fenced** — `test_pit_hazards.py` fails if the fence is removed |

The honest summary: **guardrail 1 is a working mechanism today. 2, 4 and 6 have their schema and
their selectors but not their producers. 3 is partial for reasons no money can fix, with the hardest
half — exit dating — built and gated. 5 is enforced at the column and will not be tested at the
selector until there is a selector to test.**

The two pre-existing leakage hazards named in §6 are **both fenced**: `refresh.resolve_snapshot_date`
plus a `snapshot_provenance` column closes the `--date` relabel trap, and `test_pit_hazards.py`
exists specifically to fail if either fence is deleted.

---

## 1. The three corrections to the first draft

Stated first, because each changes a recommendation.

**1. It is 67% of the equity score that depends on EBITDA, not 62%.** The first draft omitted EBITDA
growth (`GROWTH_WEIGHTS['ebitda_growth'] = 0.20`, verified in `company_scoring.py:51-55`):

```
0.40 (V)  +  0.25 × 0.20 (EBITDA growth)  +  0.20 × 0.65 (EBITDA margin)  +  0.15 × 0.60 (net debt/EBITDA)
= 0.40 + 0.05 + 0.13 + 0.09 = 0.67
```

The draft's fallback weights were also wrong. When valuation drops, the remainder renormalises over
**0.60**, not 0.75 — running the real function returns `{growth: 0.4167, profitability: 0.3333,
debt: 0.25}` and the engine's own note reads *"Remaining weights renormalised over 0.60."*

**2. Credit spread history IS obtainable — just not as an OAS.** The first draft said "no HY spread
history at any price of effort." That is true only of ICE BofA OAS. Verified live today:

| Series | Observations | From |
|---|---|---|
| `BAMLH0A0HYM2` (ICE BofA HY OAS) | **795** | 2023-09-19 |
| `BAA10Y` (Moody's Baa − 10y) | **10,621** | **1986-01-02** |
| `AAA10Y` (Moody's Aaa − 10y) | **11,404** | **1983-01-03** |

Moody's seasoned-corporate spreads are free, keyless, daily and unrevised, covering 2008, 2020 and
2022. They must be labelled for what they are — **an option-unadjusted seasoned-corporate yield
spread over the 10-year, never an OAS** — but a credit-regime feature is feasible back to 1986.

**3. Petroleum inventories are point-in-time obtainable.** The draft said the EIA revision had "no
vintage archive." False — the Weekly Petroleum Status Report release archive is live and
**release-date keyed in the URL path**:
`eia.gov/petroleum/supply/weekly/archive/2020/2020_04_22/csv/table1.csv`. The release date *is* the
`feature_available_date`. Crude and gas inventories are usable PIT from 2013.

---

## 2. Source map

### 2.1 SEC EDGAR fundamentals — **FIRST WAVE — BULK PATH BUILT**

| | |
|---|---|
| PIT key | **`(filed, accn)`** — originals are never overwritten or deleted |
| Bulk route | DERA *Financial Statement Data Sets*, one ZIP per quarter, **2009q2–2026q2 = 69 quarters, 5.26 GiB measured** (HTTP HEAD `Content-Length` on every quarter, 2026-09-20). **PIT by construction**: each ZIP contains only filings filed in that quarter, with `filed`, `accepted`, and a per-filing `sic`. **BUILT** — `pit_dera.py` |
| Per-company route | `companyfacts` / `companyconcept` — preserves every vintage; the complement for gaps, and the **only** source of cover-page share counts (§2.1.4). **IN FLIGHT** |
| Cross-validation | AAPL FY2009 10-K + 10-K/A: 208 matched consolidated facts, **zero value mismatches** |
| Revision risk | 17.6% of multi-filed headline concepts have their first value superseded; median change 2.02%, p90 49.6% |
| Survivorship | **None in the fact store** — dead issuers keep their full history |
| Access | Free, keyless. UA `ShafferFinEval/1.0 (loganshaffer87@gmail.com)`, ≤10 req/s |

**The verified selector — BUILT as `pit_store.fact_as_of`, the only sanctioned way to read a fact**
(KHC FY2016 net income: replay at 2019-06-06 → $3,632M; at 2019-06-07 → $3,596M):

```sql
SELECT * FROM pit_fact
 WHERE entity_id = ? AND tag = ? AND unit = ?
   AND period_end = ? AND qtrs = ?
   AND segments = '' AND coreg = ''        -- consolidated only; see ingest rule 1
   AND available_date <= ?                 -- the guardrail, at the query
 ORDER BY available_date DESC, filed DESC, accn DESC
 LIMIT 1;
```

Note what changed from the draft: the key is **`entity_id`, not `cik`** (§5), and the consolidated
filter is **in the selector as well as the ingest** — segment and co-registrant rows are stored but
are never returned here. It returns **the latest restatement KNOWN then** — not today's value and not
the original. Never select by period alone, and never order by `filed` without the `accn` tie-break.

The `accn` tie-break is not theoretical: AAPL filed a 10-K/A and a Q1-FY2010 10-Q on the same date,
2010-01-25.

### 2.1.1 Information latency — `information_latency_policy_v1` — **BUILT**

**`filed` is a date, not a timestamp.** 65.9% of periodic filings are accepted at or after 16:00 ET.

> **This section was rewritten on 2026-09-20.** The draft rule it replaced read
> `available_date = filed if accepted_at < 16:00 ET else next_trading_day(filed)`. That rule was
> never implemented, and it differed from the built policy in three ways, each of which changes an
> availability date: the cutoff is **15:30**, not 16:00; the boundary is **inclusive** (`<=`), not
> strict; and the policy measures from the **acceptance day**, not from `filed`. The implementation
> is the authority. It lives in `pit_policy.available_date_detail`.

**The governing concept: `available_date` means WHEN SHAFFERFINEVAL COULD ACTUALLY HAVE KNOWN IT.**
It is not the SEC's later displayed `filed` date, and it is not the acceptance instant either. It is
the first trading session by whose close a reader who was watching the wire could have had the number
in hand. Every other definition quietly hands the backtest information the strategy could not have
traded on.

This matters most at the end of the day, because **EDGAR assigns the NEXT business day as `filed` to
anything accepted after 17:30 ET**. A filing accepted 18:00 on Friday 2019-06-07 carries
`filed = 2019-06-10`. Measuring latency from `filed` would therefore *double-count* the delay for
every late filing — pushing a Friday-evening filing to Tuesday — while measuring it from the
acceptance day alone would let an EDGAR-rolled `filed` date sit *after* the availability date and
break the store's own `CHECK`. The policy measures from the acceptance day **and clamps the result to
`>= filed`**, which is exactly why that clamp is load-bearing rather than cosmetic.

**The rule as implemented:**

```
accepted_eastern <= 15:30 America/New_York   ->  first trading session ON OR AFTER the acceptance day
accepted_eastern >  15:30 America/New_York   ->  first trading session ON OR AFTER the day AFTER it
acceptance timestamp missing or unparseable  ->  first trading session STRICTLY AFTER `filed`
                                                 -- always, with no same-day case

then, in every branch:  available_date = max(result, filed)
```

Three details the shorthand loses, all of them in the code:

- **"That session" is a session, not a calendar day.** The before-cutoff branch does not return the
  acceptance day; it returns the first session on or after it. A filing accepted 10:00 on Independence
  Day 2019-07-04 is available 2019-07-05, not on a day the market never opened.
- **The sessions come from `pit_calendar`, through a callable.** `pit_policy` holds no database code,
  so the caller passes a `next_session(iso) -> iso | None` resolver (`pit_dera.session_resolver`).
  With no resolver, or past the end of the calendar, the plain calendar day is returned and the
  record carries `session_resolved: False`. That fallback selects identically on a session-grid
  replay — every as-of date *is* a session, so a Saturday availability date and the following Monday
  pick the same rows — but it is **flagged, not hidden**, because it stops being equivalent the
  moment a calendar-day grid is used. **This is why `pit_ingest.py calendar` must run before
  `pit_ingest.py dera`: `pit_fact` is immutable, so a wrong availability date cannot be repaired
  without a full re-ingest.**
- **Unparseable is not midnight.** A timestamp that fails to parse is treated as *missing*, not as
  `00:00` — which would otherwise be the most permissive value in the day. The record says
  `interpretation: "unparseable"` so the two cases stay distinguishable.

**Why 15:30 and not 16:00 — and why it is a judgement, not a measurement.** The 30 minutes are a
deliberate processing allowance. A filing that hits the wire seconds before the closing bell could not
realistically be retrieved, parsed, scored and traded into that close, and a backtest that assumes
otherwise is buying at a price the strategy could not have reached. **No measurement produced the
number 30.** Nobody timed a fill. It is an owner's judgement about a plausible processing lag,
recorded as `LATENCY_BUFFER_MINUTES = 30` and versioned so that the rows it produced can always be
identified. The *direction* of the judgement is the defensible part: it can only ever withhold
information, never grant it early. If it is later measured and found wrong, the fix is an
`information_latency_policy_v2` beside v1 — **a policy is never edited in place**, because a replay
that cannot be reproduced is not evidence.

**Why the missing-timestamp case is the pessimistic one.** 65.9% of periodic filings are accepted at
or after 16:00 ET. Assuming same-day eligibility for a filing with no timestamp would therefore be
wrong about two times in three, and wrong in the direction that flatters the backtest.

### 2.1.2 The three-form acceptance audit trail — **BUILT**

`pit_fact` stores the acceptance timestamp in **three** forms, not one:

| Column | What it holds | Why it exists |
|---|---|---|
| `accepted_raw` | The source's own string, **verbatim and never parsed** — e.g. DERA's `2021-05-06 16:28:00.0` | The parse is the part a future reader will doubt. Keeping the raw string means a later correction to the *interpretation* can be re-derived from the stored rows without re-fetching 5 GiB of ZIPs |
| `accepted_eastern` | The derived Eastern wall clock, naive ISO — `2021-05-06T16:28:00` | The value the cutoff comparison actually used. Storing the derivation separately from the input makes the two checkable against each other |
| `available_date` | The trading session the policy assigned | What every selector filters on |
| `latency_policy_version` | `information_latency_policy_v1` | Which rule produced the date. A v2 alongside v1 is then readable row by row |

The reason for three columns rather than one is that **SEC timestamp semantics differ across sources**
— DERA's `sub.txt` `accepted` column and the submissions API's `acceptanceDateTime` are not written
the same way (§2.1.3) — and `pit_fact` is **immutable**, enforced by
`trg_pit_fact_no_update` / `trg_pit_fact_no_delete`. There is no second chance to reinterpret a
timestamp in place. Keeping the raw string is what makes a correction possible at all: a future
`available_date_v2` column can be computed from `accepted_raw` for every row already stored.

### 2.1.3 How a source timestamp is encoded and interpreted — **BUILT**

EDGAR publishes `acceptanceDateTime` as an **Eastern-Time wall clock**. In the submissions API it is
frequently stamped with a `Z` designator anyway, **and that `Z` is a lie**: the surge of acceptances
in those values sits at and after 16:00, which is the US market close, not noon. So the reading rules
are asymmetric on purpose:

| Input form | Read as | `interpretation` recorded |
|---|---|---|
| Naive (`2021-05-06T16:28:00`, or DERA's space-separated form) | Eastern wall clock | `naive_read_as_et` |
| `Z`, `z`, or any **zero** UTC offset (`+00:00`) | Eastern wall clock — **the designator is ignored** | `utc_designator_read_as_et` |
| A genuine **non-zero** offset (`+02:00`) | Converted to Eastern with `astimezone` | `offset_converted_to_et` |
| Absent, empty | Missing → the always-next-session branch | `missing` |
| Present but not parseable | Missing → the always-next-session branch | `unparseable` |

Note that it is *any zero offset*, not the letter `Z` alone, that is overridden — `+00:00` and `Z`
are the same claim and are treated the same way.

**Why ignoring a UTC designator is safe: the error is one-directional.** Suppose a timestamp really
were UTC and we misread it as Eastern. Eastern is UTC−4/−5, so reading a UTC clock as Eastern makes
the filing look **4 or 5 hours EARLIER in the day than it was**, which can only move it toward — or
past — the cutoff in the direction of *waiting longer*:

- `20:05Z` is genuinely 16:05 ET, already after the cutoff. Read as 20:05 ET it is still after the
  cutoff. **Same answer.**
- `19:00Z` is genuinely 15:00 ET and would have been eligible that session. Read as 19:00 ET it waits
  for the next session. **One session late — information withheld, never granted early.**

There is no input for which the misreading makes a filing available *sooner*. The converse mistake —
trusting the `Z` and converting 16:05 ET to 12:05 ET — turns a late filing into a same-day eligible
one, which is a look-ahead leak and would manufacture profit. Given that EDGAR demonstrably mislabels
Eastern as UTC, and that one of the two errors is free while the other is a leak, the policy takes the
free one.

A genuine non-zero offset is converted properly. That path needs the `America/New_York` zone, which on
Windows `zoneinfo` reads from the `tzdata` package — present here only transitively
(streamlit → pandas → tzdata) and **not in `requirements.txt`**. So the zone is built **lazily on
first use, not at import**, and raises a named error if it cannot be loaded. Every ordinary EDGAR
filing takes the naive or zero-offset path and needs no zone rules at all; `import pit_policy` on a
bare interpreter therefore cannot fail.

### 2.1.4 The DERA shares-outstanding hole — measured, unfixable from DERA

**DERA's compact `num.txt` carries essentially no cover-page facts.** Measured on 2021q2, across
**2,833,915 rows**: **3** `EntityCommonStockSharesOutstanding` rows and **ZERO** `EntityPublicFloat`
rows.

Shares outstanding therefore **cannot come from the bulk route at all.** This is not a tuning problem
and no tag choice fixes it — the `dei` cover-page facts are simply not in the compact data set. The
consequences are specific:

- The `shares_outstanding` ladder's leading rung (`dei:EntityCommonStockSharesOutstanding`) will be
  empty for every DERA-sourced entity. The ladder falls through to
  `us-gaap:CommonStockSharesOutstanding` (per share class, so the feature builder must sum classes)
  and then to `WeightedAverageNumberOfDilutedSharesOutstanding`, which is a **period average, not a
  point count**.
- Share count multiplies into market cap, so a wrong or stale one mis-scales valuation for the whole
  company rather than degrading one factor. That is why the concept carries a tighter staleness
  override (12 months annual / 4 quarterly against the 15/6 default).
- **The per-issuer complement is therefore not optional.** It moves from "nice to have for gaps" to a
  required second source for one specific concept, fetched per issuer from the XBRL `companyconcept`
  endpoint. Until it lands, market-cap-dependent features must be labelled `unavailable`, not
  estimated from a diluted average. (`pit_shares.py` exists and is being written against exactly this
  hole; it was not reviewed for this document and nothing here should be read as vouching for it.)

**Three fields that look like as-of keys and are not.**

- **`frame` — poison.** All 7,613 frame-carrying value-changed groups had the frame on the *latest*
  value. `frames/us-gaap/GrossProfit/USD/CY2017` returns Kraft Heinz at 9,033,000,000 — the June-2019
  restatement — not the 9,703,000,000 on the wire in February 2018. Frame *membership* is not PIT
  either: only 1.3% of the CY2020 revenue frame came from an accession filed in 2020; one member
  arrived via a **2026** accession. **Do not use the frames API for replay, and do not use it to
  size coverage** (the first draft did exactly that — see §9).
- **`fy`/`fp`** describe the *filing*, not the fact.
- **`prevrpt`** in DERA `sub.txt` means "later amended" — computed from the future. **Drop it.**

**Three ingest rules the first draft omitted, each of which silently corrupts the fact store:**

1. **47% of DERA `num.txt` rows are dimensional** (1,333,074 of 2,833,915 in 2021q2) — one segment's
   revenue, one share class. Without `segments`/`coreg` in the key, a segment value collides with the
   consolidated value and an upsert silently stores the segment as the company total. **Filter to
   `segments == '' AND coreg == ''`.**
2. **`qtrs` is not 0/1/4.** Values of 2, 3, 74, 89, 90 and 123 occur. **`CHECK (qtrs IN (0,1,2,3,4))`.**
3. **DERA rounds the period to month end** — Apple's FY2009 ended 2009-09-26, DERA records
   `ddate=20090930`. Every 52/53-week filer gets two non-joining rows unless normalised.

All three are **BUILT** into `pit_dera.ingest_quarter` and the `pit_fact` DDL.

### 2.1.5 What the DERA ingest actually costs — measured, not projected — **BUILT**

Every number below traces to one of two real measurements taken on 2026-09-20: an HTTP `HEAD` of
every quarter in the archive, and a full parse plus a real SQLite load of 2021q2 into a throwaway
database. Reproduce the parse with `python pit_dera.py --measure <2021q2.zip>`.

| | Measured |
|---|---|
| Archive window | **2009q2 → 2026q2 = 69 quarters** |
| Download | **5.26 GiB** (5,644,394,266 bytes), 136.7 s at the measured 41.3 MB/s |
| `num.txt` rows across the window | 176,040,046 projected from the measured per-quarter ratio |
| **Rows retained into `pit_fact`** | **12,563,014** — a **14.0×** reduction; 7.14% of source rows |
| Database size | **4.36 GiB** — 3.34 GiB table + UNIQUE, 1.02 GiB secondary indexes, at 372.5 bytes/row |
| Wall clock | **~32 min** (32.2): 137 s download + 1,405 s parse + 390 s insert |
| Insert rate | **32,191 rows/s** (`executemany`, 20,000-row batches, entities pre-registered) |
| Parse rate | 125,339 rows/s |
| **The tag filter** | **38 tags** — 36 `us-gaap` + 2 `dei` — against `tag.txt`'s 97,000, of which 86,781 (89.5%) are company extensions |

**The tag filter is the whole reason this is affordable.** 2021q2's `num.txt` holds 2,833,915 rows;
consolidated-only leaves 1,500,841; the 38-tag filter leaves 219,207; the `qtrs` CHECK and the value
and period guards leave **202,241** periodic rows. The 38 tags are not chosen by hand — they are
**computed from `pit_policy`'s concept ladders**, so a tag cannot be retained that no ladder reads,
and a ladder cannot name a tag the ingest silently drops.

Idempotency is measured, not asserted: a second pass over the same quarter inserted **0 of 202,241**
rows, which is what makes the 69-quarter run resumable by simply running it again.

Three known holes in the bulk route, recorded rather than papered over:

1. **Cover-page `dei` facts are absent** — 3 shares-outstanding rows and 0 public-float rows in 2.83M.
   See §2.1.4. This is the one that forces `companyfacts`.
2. **2,009 consolidated rows in 2021q2 carry a us-gaap ladder tag name under an `ifrs/*` version**
   (`ProfitLoss` 795, `Assets` 604, `GrossProfit` 403, `InterestExpense` 207). `pit_store.fact_as_of`
   does not filter on taxonomy, so these are **excluded at ingest** rather than stored and hoped about.
3. **XBRL phase-in makes the early window a large-cap sample, not a cross-section.** Mandatory tagging
   reached >$5B float for periods ending 2009-06-15, all large accelerated filers 2010-06-15, all
   filers 2011-06-15. 2010q1 carries **484 distinct CIKs against 6,518 in 2021q2.** The warm-up
   quarters are usable as warm-up and must never be quoted as coverage.

**The tag that dies quietly — the worst failure mode found.** SVB Financial's `us-gaap:NetIncomeLoss`
has 14 observations, all filed 2010-08-06 to 2012-02-28, while the company filed 10-Ks through
2023-02-24 using `ProfitLoss` instead. A single-tag extractor at as-of 2019-06-30 returns the FY2011
value: a real number, correctly PIT-selected, passing every guardrail in this document, **and eight
years stale.** Two defences, both required, **both BUILT in `pit_policy.py`**:

- **Concept ladders** — `concept_ladder_v1`, **13 concepts**: revenue, net income, operating income,
  D&A, cash, total debt, total assets, equity, operating cash flow, capex, shares outstanding,
  interest expense, gross profit. The winning rung's key goes into `pit_feature.source_tag`; a rung
  change between adjacent as-of dates is a **discontinuity, not a fundamental change**, and sets
  `source_tag_changed` so growth factors can exclude the step. A rung may be **composite**: total
  debt's first rung is the arithmetic sum of `LongTermDebtNoncurrent + LongTermDebtCurrent +
  ShortTermBorrowings`, **all three required** — two of three is a wrong number, not a partial one.
  Two ladders (`interest_expense`, `gross_profit`) are flagged `measured = False`: the tag sets are
  authored, no coverage survey has been run on them, and **no coverage number may be quoted from
  them**.
- **A maximum-age bound** on the backing fact: `period_end` within **15 months** of `as_of` for annual
  (`qtrs >= 4`), **6 months** for everything shorter including the instantaneous balance-sheet facts
  (`qtrs = 0`). The boundary is **inclusive** — FY2019 is usable through 2021-03-31 and stale from
  2021-04-01 — and is exact calendar-month arithmetic, not a day count. `shares_outstanding` overrides
  it to **12 / 4** because the cover-page count is refreshed at *every* filing, so a 15-month-old one
  is a missed filing rather than a slow-moving number. The value is stored on the row as
  `fact_max_age_days` (months × 31, deliberately the loosest reading, so the SQL pre-filter can only
  ever be more permissive than the month rule that is the authority).

**Reading the future through a tag that did not exist yet** is the third defence, and it is a property
of the rung rather than a comment: **99.0% of the CY2017 values carried by the two ASC 606 revenue
tags were filed in 2019 or later** — they are comparatives inside a later filing, not what was on the
wire in 2017. Those rungs carry `min_filed_date = 2018-01-01` and are **invisible** at an earlier
as-of. At 2016 the revenue ladder is four rungs long and starts at `Revenues`; at 2019 it is six and
starts at the ASC 606 tag.

**Three null states, not one:** `never_tagged` / `tagged_zero` / `company_had_none`. AAPL's first
long-term-debt observation is 2013-07-24 because Apple genuinely had no debt before 2013. "Absent"
and "zero" are different facts.

### 2.2 Yahoo Finance — prices — **FIRST WAVE (prices only)**

| | |
|---|---|
| Depth | KO/GE/IBM/XOM/^TNX to 1962-01-02; ^GSPC to 1927-12-30; AAPL 11,534 bars from 1980-12-12 |
| PIT key | **None.** Adjustment is retroactive and unavoidable |
| Survivorship | **Severe.** 21 of 26 delisted tickers return HTTP 404 |
| Rate limit | None observed — 30 requests at 7.98 req/s, zero throttles |

- **Never `range=max`** — it silently coarsens to monthly (AAPL: 169 bars instead of 11,534). Always
  epoch bounds, and **assert `meta.dataGranularity == "1d"`** on every response.
- **`adjclose` for return labels** — verified window-invariant to 2.5e-7 and correctly invariant to
  post-as-of splits and dividends (a common factor cancels in the ratio).
- **`close` for price-level features, only after un-applying future splits.** AAPL 2015-06-30 reports
  31.3575 even in a window ending 2019; × 4 = 125.43, the true close. Narrowing the window does **not**
  recover the as-traded price.
- **The ticker is not a join key.** Six confirmed reuse cases: `BBBY`, `FB`, `INFO`, `AMR`, `AMTD`,
  `SPWR`. `BBBY` today returns HTTP 200, `longName` "Bed Bath & Beyond, Inc.", NYSE, `EQUITY`, with 45
  bars starting 2026-07-17 — ticker, name *and* exchange all match. Only `meta.firstTradeDate` catches
  it. Gap and level-jump detection catch **none** of the six.
- **Spin-offs arrive as splits.** `T` carries `splitRatio` "1324:1000" on 2022-04-11 in the same array
  as GE's genuine 1:8 reverse split. The splits array is a valid **price** factor and an invalid
  **share-count** factor — dividing AT&T's share count by 1.324 is wrong by 32.4%.
- **`CL=F` closes at −37.63 on 2020-04-20.** Any log return or ratio breaks on exactly one day in 6,626.

### 2.3 FRED / ALFRED — macro — **FIRST WAVE**

Free and **keyless** (`FRED_API_KEY` is not set in this environment and is not needed).

- **NOT_REVISED** — values readable once from plain FRED: `DGS10, DGS2, DGS3MO, DGS30, DTB3, DFII10,
  T10Y2Y, T10YIE, DFF, SOFR, VIXCLS, DEXUSEU, DEXJPUS, DCOILWTICO, DHHNGSP, MORTGAGE30US`, plus
  **`BAA10Y, AAA10Y, DBAA, DAAA`** (added by correction 2).
- **REVISED** — must be fetched per as-of vintage: `DTWEXBGS, CPIAUCSL, PCEPILFE, PAYEMS, UNRATE, GDP,
  INDPRO, RSAFS`, **and any new series by default**.

**"Not revised" means values, not the observation set.** `DCOILWTICO` values present in the 2015-01-05
vintage are blank today (2014-10-14: 81.72 → gone). `DGS10`'s first vintage starts 1962-02-01 while
today's starts 1962-01-02 — back-history was *added* after 2005.

**Archive floors are per-series and are a hard constraint on the start date.** The first draft's
"archive generally starts 2005-06-28" was wrong in both directions:

| Series | First vintage | | Series | First vintage |
|---|---|---|---|---|
| INDPRO | 1927-01-26 | | T10Y2Y / T10YIE | 2014-01-27 |
| PAYEMS | 1955-05-06 | | VIXCLS | 2010-11-22 |
| UNRATE | 1960-03-15 | | SOFR | 2019-03-29 |
| CPIAUCSL | 1972-07-21 | | **DTWEXBGS** | **2019-02-04** |

`DTWEXBGS` — the one series proven revised on **every** observation (143/143 differ at the 2020-01-02
vintage) — has no vintage before 2019-02-04. **It is the binding constraint on any start before 2019**,
and the rule is: below a series' floor the feature is `unavailable`, **never** back-filled from plain
FRED.

**ALFRED silently succeeds when it should fail.** Verified: `vintage_date=2030-01-02` returns **HTTP
200** with header `DTWEXBGS_20260920` and today's fully revised values. So, parallel to the Yahoo
granularity assert: **assert the CSV header's second field equals `<ID>_` + the requested vintage with
dashes stripped.** On 404 (pre-archive), write `NULL` and a note — never fall back to the current series.

**Never compute a transform across two vintages.** INDPRO rebased 2012=100 → 2017=100 and PCEPILFE
2009=100 → 2017=100 *inside* the replay window; a cross-vintage MoM manufactures a shock that never
happened. Every MoM/YoY is computed **entirely within one vintage**.

**Publication lags are measured, not assumed:** `DGS*` +1 business day, `DCOILWTICO` +2, **FX +5 to +9
calendar days** (H.10 is weekly — in the 2024-03-13 vintage the newest `DEXUSEU` was Friday 2024-03-08).

### 2.4 Treasury curve, volatility, FX, crypto, inventories — **FIRST WAVE**

- **Treasury**: one CSV per year, 1990+, never revised. **Parse by column name, never position** — the
  "30 Yr" column vanishes from the 2003 and 2005 files and returns 2006-02-09. Gate each tenor on its
  introduction date. A missing year returns **HTTP 200 with a zero-length body**.
- **Volatility**: CBOE's own CSVs from 1990-01-02 (0 mismatches against FRED across 9,275 days; Yahoo
  differs on 13). Full VIX9D/VIX/VIX3M/VIX6M term structure only from 2011-01-04.
- **FX**: FRED `DEX*` (1971+) as sole source. Yahoo `=X` differs by a 0.151–0.285% median and fabricates
  weekend bars under naive UTC conversion (`EURUSD=X` is `Europe/London`).
- **Crypto**: Bitstamp BTC/USD from 2011-09-13, labelled venue-specific and survivorship-incomplete.
- **Petroleum inventories**: EIA WPSR release archive, 2013+, release date in the URL path.

### 2.5 Sources evaluated and deferred

Named so the boundary of what was actually tested is visible: **SEC N-PORT-P** (dated authoritative
fund/ETF holdings, 2019Q4+, measured 49–62 day lag — the only honest route to ETF constituent factors);
**CFTC Commitments of Traders** (verified reachable, free, dated — feeds the `positioning` factor);
**USDA WASDE/FAS** (agriculture/livestock — unprobed); **NOAA HDD/CDD** (natural-gas weather —
unprobed); **EDGAR ABS-EE** (structured credit asset-level — unprobed).

### 2.6 The AI-Bubble-Research dataset — **evaluation metadata only**

You asked to hold this as secondary until its identifiers and corporate-action history were mapped.
The audit is blunter than expected: **no component is point-in-time, and two are actively
anti-point-in-time.**

- `crash_prices.json` was built with `auto_adjust=True` — every price is back-adjusted to the
  2026-08-17 download date, measured re-revising by up to 0.82% in 34 days because a dividend went ex.
- `xbrl_cache.json` discards `filed` and `accn`, so it cannot tell an original from a restatement.
- The cohort labels are **perfectly confounded with fiscal year** — every BUBBLE row is FY2021, and
  `−|fy − 2021|` separates the classes perfectly.
- It is also a survivor set: 43 tickers, 41 still quoting, **zero delisted**.

**The first draft made a mistake here that the leakage reviewer caught.** It proposed importing the
curated crash-era calendar as "regime metadata", protected only by "non-numeric" and "strip the ticker
column". Neither addresses hindsight: an era boundary drawn in 2026 attached to a 2008-06-30 feature row
is a pure look-ahead flag, and the `CHECK` constraint **cannot catch it** — an ingester would naturally
stamp `feature_available_date` with the regime date itself, which satisfies the constraint perfectly.
This is the same failure the same section diagnoses in the cohort labels, re-created one paragraph later.

**Corrected rule.** The crash calendar is **evaluation metadata**: joined to results *after* scoring, to
group outcomes by era. It is banned from `pit_feature` by an explicit source blocklist. A regime
*feature* must be constructed causally from data available at *t* (trailing drawdown, realised vol,
term-spread sign) with its own `feature_available_date`, or discovered unsupervised **fit on the fold's
training window only** — recorded per fold. `pit_regime` carries a `defined_on_date`, so a hindsight
label fails the CHECK automatically.

Import exactly two things: the **53 no-ticker casualty names as a survivorship test list** (any PIT
universe that cannot produce them for their era is failing by construction), and
`backtest_result.json` + `backtest_engine.py` as a **frozen regression fixture** (the new stdlib
implementation must reproduce AUC 0.8233 at w=0 before anything changes).

---

## 3. Feasibility by model — all 19 models and 6 overlays

The first draft gave verdicts for 6 of 25. That was the largest omission. **FIRST WAVE** = build now;
**PARTIAL** = build with named factors missing and labelled; **NOT FEASIBLE** = no free PIT source;
**UNPROBED** = plausibly feasible, not yet tested — do not assume either way.

| Model | Verdict | Binding constraint |
|---|---|---|
| **EQUITY** | **PARTIAL — first wave** | The 67% EBITDA dependency (§4) |
| **RATES** (govt bond) | **FIRST WAVE (1990+)** | Treasury curve + ALFRED vintages + DFF — all clean |
| **VOLATILITY** | **FIRST WAVE (2011+ term structure)** | `positioning` obtainable from CFTC CoT |
| **FX** | price 1971+, **model PARTIAL** | Foreign policy rates, foreign CPI vintages, current account unsourced; `gpi` (0.17) unavailable |
| **GOLD** | **PARTIAL (~0.75 of weight)** | Real yield 2003+, USD, inflation, risk, momentum available; `flows` and `gpi` not |
| **OIL** | **PARTIAL** | Inventories PIT from 2013, spot 1986+; curve/refining/gpi (~0.36) unavailable |
| **NATGAS** | **PARTIAL** | Spot 1997+; storage vintage path unverified (404 today); weather via NOAA unprobed |
| **CORP_CREDIT** | **PARTIAL** | Spread via BAA10Y/AAA10Y from 1986 (not an OAS); `gpi` (0.10) unavailable |
| **ETF** | **PARTIAL from 2019-06-30** | N-PORT-P is the only honest constituent source; 49–62 day lag |
| **FUTURES** | **PARTIAL (0.85 underlying only)** | `carry_curve` (0.10) needs a term structure that does not exist free |
| **CRYPTO** | price 2011+, **model NOT FEASIBLE** | liquidity / flows / network / leverage unsourced |
| **REIT** | **NOT FEASIBLE** | P/AFFO, NAV, cap rate, occupancy, AFFO margin/growth are **non-GAAP company extensions**, which `companyfacts` omits entirely. Only the 0.20 debt leg is sourceable — **80% of the weight is missing.** REITs must be *explicitly excluded* from the equity universe, not left to the SIC ladder |
| **INDUSTRIAL_METAL** | **NOT FEASIBLE** | No free PMI, LME inventory or China demand series |
| **MBS** | **NOT FEASIBLE** | OAS windowed; no free prepayment speeds |
| **OPTION** | **NOT FEASIBLE BACKWARDS; FEASIBLE FORWARDS FROM 2026-09-20** | Expired contracts 404; no historical chain at any price. The archive (§9.1) is the only supply, and it accrues one day at a time |
| **PREFERRED** | **NOT FEASIBLE (unevidenced)** | No probe tested preferred prices, yields or call schedules |
| **STRUCTURED** | **NOT FEASIBLE (unevidenced)** | EDGAR ABS-EE exists and was never probed |
| **AGRICULTURE** | **UNPROBED** | USDA WASDE/FAS archives are dated and free — the map did not ask |
| **LIVESTOCK** | **UNPROBED** | USDA cattle-on-feed archives, same |
| *Overlay:* **IRS** | **PARTIAL 2000-07-03 → 2016-10-28** | FRED `DSWP10` is discontinued; proxy-only after |
| *Overlay:* **FX FORWARD / NDF** | **PROXY-ONLY** | Synthesised by covered-interest parity; must be labelled synthetic |
| *Overlay:* **CDS, TRS, SWAPTION** | **NOT FEASIBLE** | No free financing-spread or swaption-vol history |

**Consequence for sequencing.** First wave is **equities, rates, volatility, FX prices, gold and credit
regime**. Everything else is second wave, unprobed, or excluded — and excluded classes should be
*absent* from the historical store, not present with silent nulls.

---

## 4. The finding that forces a decision: 67% of the equity score depends on EBITDA

```
0.40 (V) + 0.25×0.20 (EBITDA growth) + 0.20×0.65 (EBITDA margin) + 0.15×0.60 (net debt/EBITDA) = 0.67
```

**EBITDA is not a concept in the XBRL taxonomy.** It must be assembled as `OperatingIncomeLoss` + a D&A
tag. Measured coverage at CY2019Q4I, against 6,180 companies reporting `Assets`:

| Component | Coverage |
|---|---|
| D&A ladder (any variant) | **6,144 (99.4%)** |
| `OperatingIncomeLoss` | **5,234 (85%)** |
| Full six-tag debt ladder | **3,459 (56%)** |
| *Intersection (the real EBITDA number)* | **not yet measured — the one number still to compute** |

So the honest characterisation is **"EBITDA is assemblable for at most ~85% of the cross-section, with
`OperatingIncomeLoss` binding — not D&A"** — materially better than the first draft's impression of
catastrophe. Two specific claims in that draft were overstated: MSFT's missing D&A is **fixed by the
DERA route** (which carries extensions), and "every financial fails" generalised from one company.

What remains true and unfixable:

1. **A historical score will not equal today's live score**, because live uses Yahoo's EBITDA and today's
   peer set while history uses an XBRL-assembled EBITDA and a PIT peer set.
2. The replay therefore **carries its own model version** — `equity_shaffer_v1_pit` — and must never be
   written into `score_history` beside live rows.
3. Valuation needs EBITDA for the **peers too** (`build_ebitda_peer_cohort` requires ≥3 peers with a
   valid EV/EBITDA), so thin coverage kills V even for a company whose own EBITDA is fine.
4. When V drops, the model becomes structurally different (weights 0.4167/0.3333/0.25 over 0.60, and the
   sub-factors collapse). Every row stores `weights_used`, `benchmark_level` and peer counts.

**This is decision #1 in §11.**

---

## 5. Identity — where the first draft broke — **NOW BUILT AND GATED**

**The disqualifying defect.** The first draft keyed every PIT table on `assets(asset_id)`. But `assets`
is `UNIQUE (symbol, asset_class)` — **the ticker is its natural key** — it has no `cik` column, holds
1,721 current rows, and is upserted nightly with `active=1`. Consequences, all silent:

- The foreign key **physically refuses** any company not in today's live universe. Querying `assets` for
  LEH, SIVB, FRC, BBBY, ATVI, TWTR, SHLD, WAMU returns **zero matches**. The survivorship test list
  could not pass and the −100% outcomes could not be stored.
- Two issuers sharing a ticker collapse onto one `asset_id` — the exact corruption §2.2 spends a page
  proving.
- The CIK-keyed peer universe had **nowhere to live**, so the cheapest fix for an implementer would be
  to restrict peers to names with an `asset_id` — reinstating survivorship bias **without an error**.
- The nightly job rewrites `name`, `sector`, `industry` underneath historical rows.

**The fix: entity identity is primary. BUILT in `pit_identity.py`.** Every PIT table keys on
`entity_id` (CIK-backed). Prices key on a `pit_listing` disambiguated by `meta.firstTradeDate`.
`assets` is linked, if at all, by a nullable dated bridge the replay never reads — and
`pit_identity.py` does not import or query `assets` at all, so today's live universe cannot decide
whether a historical entity may exist.

The implemented surface is:

| Function | What it decides |
|---|---|
| `ingest_entity(conn, cik)` | Registers the issuer and its **dated** attributes: `pit_entity_name` from `formerNames`, `pit_entity_sic` from each filing's own `<ASSIGNED-SIC>` header. The submissions API's current `tickers`, `exchanges`, `sic`, `name` and `entityType` are recorded **only in the ingest report, which no selector reads** |
| `resolve_listing(conn, symbol, entity_id, as_of)` | The **`meta.firstTradeDate` gate**. Rejects with a named reason: `no_yahoo_data`, `no_meta`, `not_daily_granularity`, `no_first_trade_date`, `first_trade_after_as_of`, `instrument_type_mismatch`, `otc_where_national_expected` |
| `exit_date_for(conn, cik)` | `earliest defensible of {8-K Item 3.01, last bar with volume > 0, common-stock Form 25-NSE}`, with `exit_date_source` recorded and `no_exit_record = 1` quarantining an issuer that simply stopped |
| `peer_universe_as_of(conn, as_of)` | Every CIK with a periodic filing inside `DEFAULT_PEER_MAX_FILING_AGE_DAYS` (400) and no exit before *t*. **No ticker required** |
| `scored_universe_as_of(conn, as_of)` | The subset with a gated listing and a real bar within `DEFAULT_PRICE_MAX_AGE_DAYS` (10) |

### 5.1 The survivorship gate — **RUN AND PASSING**

`test_pit_identity.py` is a **build gate**, not a unit test: if it fails, bulk historical scoring must
not begin, because a store that cannot represent a company which no longer exists cannot measure
anything about failure. It runs against the **live** SEC and Yahoo endpoints on purpose — the defects
being guarded against are properties of the sources, not of the code — and caches every response so a
re-run is cheap and polite.

Observed result, this machine, 2026-09-20: **0 failures. `GATE PASSED: the store can represent
companies that no longer exist.`** The cases that matter:

| Case | CIK | What the gate proves |
|---|---|---|
| **Old GM vs new GM** | 40730 / 1467858 | **Two distinct `entity_id`s.** Old GM is named `GENERAL MOTORS CORP` in 2008 and Motors Liquidation today; new GM has **no 2008 name — it did not exist.** Old GM exits in 2009 despite an estate that kept filing 10-Qs until 2021-02-12 |
| **Enron** | 1024401 | **Quarantined with no fabricated exit date.** Enron filed neither a Form 25 nor a Form 15; `exit_date IS NULL` and `no_exit_record = 1`. It remains a **peer** and is **never scored** |
| **Berkshire** | 1067983 | **NOT delisted.** Its six Form 25 filings were examined and all rejected as non-common (senior notes), and its Item 3.01 notices created no exit. Form 25-NSE is **per security, not per company** |
| **BBBY, today's ticker** | 886158 | Today's `BBBY` returns HTTP 200, `longName` "Bed Bath & Beyond, Inc.", NYSE, `EQUITY` — name, exchange *and* instrument type all match the dead retailer. It is **rejected for a 2015 as-of** by `first_trade_after_as_of`, because its first bar is 2026-07-17. Gap and level-jump detection catch none of this |
| **BBBY, the real exit** | 886158 | Dated in 2023 from the **earliest defensible** source, weeks ahead of the 2023-07-10 Form 25 |
| **Lehman, SVB, Sears, First Republic** | 806085 / 719739 / 1310067 / 1132979 | All resolvable as entities with full history, none of which exists in today's `assets` table |

The gate also verifies that peer membership needs no ticker at all, that BBBY and Sears **leave** the
peer set after their exits while Berkshire and new GM remain, that a quarantined issuer is a peer but
never scored, that re-ingest is idempotent (no new entity, name or SIC rows), and that the run wrote
to **neither live database**.

**There is no dated ticker↔CIK map anywhere, free or otherwise.** EDGAR gives dated *names*
(`formerNames`) but `tickers` is a bare current-state list with no validity interval;
`companyconcept` for `dei/TradingSymbol` 404s. Measured cost of ignoring this: of 9,320 CIKs that filed
in 2008Q4, only **2,193 (23.5%)** appear in today's `company_tickers.json` — **three of every four
companies discarded.**

Live breakages in today's map that would have shipped: `XOM` → CIK 2115436 ("ExxonMobil Holdings Corp",
oldest filing 2026-07-01) while Exxon's real history sits at CIK 34088 with `tickers=[]`; `BK` absent,
`BNY` present; `SUNE` → "SUNation Energy", not SunEdison. And 10,438 tickers map to only **8,031 CIKs**
— counting companies by ticker over-counts by ~30%.

**CIK is not perfectly stable either.** CIK 933136 filed a 10-K in 2008 as Washington Mutual; today it is
"Maverick Merger Sub 2, LLC". Entity continuity needs its own dated intervals.

### The two-universe split

Six of the seven equity sub-factors need **no price at all**:

- **Peer universe** — every CIK with PIT fundamentals at *t*. CIK-keyed, no ticker needed,
  **survivorship-free by construction**. Used for the percentile normalisation behind G, P and D.
- **Scored universe** — names where ticker↔CIK is *proved* at *t* and prices exist. Smaller, labelled,
  and the only set that gets a score, a label or a position.

A feature row with `listing_id IS NULL` is **normal and expected** for most of the peer universe.
Valuation, which needs peer market caps, uses the price-resolvable cohort only and records its size
separately.

**Correction to the first draft's §8.2.** It claimed S&P 500 dated membership gives "defensible
ticker↔CIK". It gives a defensible dated **ticker** set; the CIK link is the unsolved half — 37% of the
2008 list is unresolvable through today's SEC map. And LEH, BSC, FNM, FRE, MER and WB **pre-date XBRL
entirely** (Lehman's `companyfacts` 404s) and all 404 on Yahoo, so none can be scored or labelled at any
start date. They belong on the survivorship *test* list, not in the payoff argument.

**PIT classification is solvable.** Every EDGAR accession carries `<ASSIGNED-SIC>` in a ~900-byte
`.hdr.sgml`, verified back to 1994, and DERA `sub.txt` gives it in bulk. Use the SIC **from the filing** —
13.6% of CIKs changed 4-digit SIC and 11.4% changed 2-digit between 2011q3 and 2026q2. Peer ladder,
widening to ≥20 members: 4-digit SIC → 3-digit → 2-digit → SEC Office. **The crosswalk file's hash is
part of `model_version`**, because changing it re-labels history.

**Corporate actions, one by one** (guardrail 3, honestly):

| Action | Source | Status |
|---|---|---|
| Splits | Yahoo `events.splits` | Good — but spin-offs masquerade as splits |
| Dividends | Yahoo `events.dividends` | Good; silently incomplete on microcaps (SPWR moves −50% with an empty splits array) |
| Bankruptcy / delisting | 8-K Item 3.01, last bar with volume > 0, Form 25-NSE | Good — take the **earliest defensible**, record which won |
| Acquisitions | Form 25-NSE `ruleProvision` 12d2-2(a)(3) | Workable |
| **Ticker changes** | — | **No free dated source.** `formerNames` dates *name* changes; FB→META and SQ→XYZ are separate, unrecorded events |
| **ADR changes** | — | **Not answered.** ADRs are excluded (they file `ifrs-full` in TWD/JPY/CNY), which is an exclusion, not a solution |

**Exit dating.** Form 25-NSE is **per security, not per company** — Berkshire has six and is not
delisted; Lehman's earliest is 2002-01-07, six years early. And it **lags the last trade**: BBBY stopped
trading ~2023-05-03, its 25-NSE was filed 2023-07-10 (68 days late). Enron filed **no exit form at all**.
So: `exit_date = earliest defensible of {8-K Item 3.01, last bar with volume > 0, common-stock 25-NSE}`,
with `exit_date_source` recorded and a `no_exit_record` flag quarantining the Enron case.

---

## 6. Architecture

```
Raw PIT Sources → PIT Feature Store → Historical Shaffer v1 Replay
                → Forward Outcome Labels → Purged Walk-Forward → ML Challengers → Proposals
```

**Production separation is absolute.** The replay writes to `pit_score`, never to `score_history`. They
are different model versions from different sources and must never be pooled.

**Two existing hazards had to be fenced first. Both are now fenced — `test_pit_hazards.py` fails if
either fence is removed:**

1. **The `--date` trap. FENCED.** `daily_job.py --date 2020-01-01` used to succeed and write a
   `score_history` row stamped 2020-01-01 containing **today's live fetched data**. Nothing on the
   table distinguished a genuine close from a backfill. Now: a `snapshot_provenance` column (added by
   the existing ADD-COLUMN convention, defaulting to `'live_close'`) plus
   `refresh.resolve_snapshot_date`, which refuses a past `snapshot_date` without an explicit override.
2. **The unpurged splitter. FENCED.** `ml_lab.walk_forward_splits` has no purge and no embargo and is
   what `train_all` calls. Adding a correct splitter beside it would not have been enough, so the
   existing one now **refuses point-in-time input outright** — a dataset carrying
   `SOURCE_POINT_IN_TIME` is rejected with *"REFUSED: walk_forward_splits has NO PURGE and NO
   EMBARGO"* rather than quietly evaluated, and the function is marked deprecated.

Both tests are of the same shape and exist to FAIL if the fence is deleted — a happy-path test would
not notice, because both hazards produce a result that looks well-formed and is quietly a lie.

---

## 7. Schema

House style: stdlib `sqlite3`, TEXT dates, `_json` suffix, `CREATE TABLE IF NOT EXISTS`, UNIQUE for
idempotent upsert. **Immutability is enforced by a SQLite trigger, not by prose** — the first draft
relied on a convention any contributor bypasses with one `INSERT OR REPLACE`.

> **BUILT.** `pit_store.SCHEMA` is the authority and holds **30 tables** and **4 immutability
> triggers**, all created in `shafferfineval_pit.db` — a research file entirely separate from the
> production `shafferfineval.db`. What follows is a reader's copy. Where it differs from
> `pit_store.py`, `pit_store.py` is right; `pit_fact` below has been resynchronised with it and
> carries the three-form acceptance trail of §2.1.2. The tables marked PLANNED in §0.1 exist here and
> are empty — the shape is settled, the ingest is not written.

```sql
-- ============ IDENTITY (primary; never the ticker) ============

CREATE TABLE IF NOT EXISTS pit_entity (
  entity_id INTEGER PRIMARY KEY AUTOINCREMENT,
  cik TEXT NOT NULL UNIQUE,
  first_filing_date TEXT, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pit_entity_name (
  entity_id INTEGER NOT NULL REFERENCES pit_entity(entity_id),
  name TEXT NOT NULL, valid_from TEXT, valid_to TEXT, source TEXT NOT NULL,
  UNIQUE (entity_id, name, valid_from)
);

CREATE TABLE IF NOT EXISTS pit_entity_sic (
  entity_id INTEGER NOT NULL REFERENCES pit_entity(entity_id),
  sic TEXT NOT NULL, filed TEXT NOT NULL, source_accession TEXT NOT NULL,
  UNIQUE (entity_id, source_accession)
);

CREATE TABLE IF NOT EXISTS pit_entity_exit (
  entity_id INTEGER PRIMARY KEY REFERENCES pit_entity(entity_id),
  exit_date TEXT, exit_type TEXT, exit_date_source TEXT,   -- 8k_item_301 | last_volume_bar | form_25nse
  exit_source_accession TEXT, dereg_date TEXT,             -- Form 15; NEVER the exit date
  no_exit_record INTEGER NOT NULL DEFAULT 0                -- the Enron case: quarantine, don't keep investable
);

-- The price identity. firstTradeDate is the only field that catches ticker reuse.
CREATE TABLE IF NOT EXISTS pit_listing (
  listing_id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_id INTEGER REFERENCES pit_entity(entity_id),      -- NULL for non-equities
  symbol TEXT NOT NULL, exchange TEXT, instrument_type TEXT,
  first_trade_date TEXT, valid_from TEXT NOT NULL, valid_to TEXT,
  confidence TEXT NOT NULL,                                -- proved | inferred | unverified
  source TEXT NOT NULL,
  UNIQUE (symbol, first_trade_date)
);

-- Live-UI convenience ONLY. The replay never reads this.
CREATE TABLE IF NOT EXISTS pit_entity_asset (
  entity_id INTEGER NOT NULL REFERENCES pit_entity(entity_id),
  asset_id  INTEGER NOT NULL REFERENCES assets(asset_id),
  valid_from TEXT, valid_to TEXT, confidence TEXT NOT NULL,
  UNIQUE (entity_id, asset_id, valid_from)
);

-- ============ RAW PIT SOURCES (append-only, trigger-enforced) ============

-- AS BUILT. `pit_store.SCHEMA` is the authority; this is a copy for readers.
CREATE TABLE IF NOT EXISTS pit_fact (
  fact_id               INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_id             INTEGER NOT NULL REFERENCES pit_entity(entity_id),
  taxonomy              TEXT NOT NULL,   -- us-gaap | dei | ifrs-full
  tag                   TEXT NOT NULL,
  unit                  TEXT NOT NULL,
  period_start          TEXT,            -- NULL for instantaneous facts
  period_end            TEXT NOT NULL,
  qtrs                  INTEGER NOT NULL,
  segments              TEXT NOT NULL DEFAULT '',
  coreg                 TEXT NOT NULL DEFAULT '',
  val                   REAL NOT NULL,
  accn                  TEXT NOT NULL,
  form                  TEXT NOT NULL,
  filed                 TEXT NOT NULL,
  -- The acceptance timestamp is kept in THREE forms so a reader can always
  -- reconstruct why a filing became available on a given session (§2.1.2):
  --   accepted_raw      exactly as the source published it, never parsed
  --   accepted_eastern  the derived Eastern wall clock (naive)
  --   available_date    the session the latency policy assigned
  accepted_raw          TEXT,
  accepted_eastern      TEXT,
  available_date        TEXT NOT NULL,
  latency_policy_version TEXT NOT NULL,  -- 'information_latency_policy_v1'
  source                TEXT NOT NULL,   -- dera:2021q2 | companyfacts
  UNIQUE (entity_id, taxonomy, tag, unit, period_end, qtrs, segments, coreg, accn),
  CHECK (qtrs IN (0, 1, 2, 3, 4)),
  CHECK (available_date >= filed)        -- the clamp in §2.1.1, at the column
);
-- `frame` and `prevrpt` are deliberately NOT stored: both encode the future.
-- Note the entity key: the draft keyed on `cik` as well, which allowed two rows
-- for one issuer. `entity_id` is the single identity, per §5.

CREATE TRIGGER IF NOT EXISTS trg_pit_fact_no_update BEFORE UPDATE ON pit_fact
BEGIN SELECT RAISE(ABORT, 'pit_fact is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_pit_fact_no_delete BEFORE DELETE ON pit_fact
BEGIN SELECT RAISE(ABORT, 'pit_fact is append-only'); END;
-- The same pair guards pit_feature and pit_score: trg_pit_feature_no_update,
-- trg_pit_score_no_update. Immutability is a mechanism, not a convention.

CREATE TABLE IF NOT EXISTS pit_price_bar (
  listing_id INTEGER NOT NULL REFERENCES pit_listing(listing_id),
  bar_date TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL, adjclose REAL, volume REAL,
  source_symbol TEXT NOT NULL, ingest_id INTEGER NOT NULL,  -- which pull; adjclose true only as of it
  UNIQUE (listing_id, bar_date, ingest_id)
);

CREATE TABLE IF NOT EXISTS pit_corporate_action (
  listing_id INTEGER NOT NULL REFERENCES pit_listing(listing_id),
  event_date TEXT NOT NULL, event_type TEXT NOT NULL,
  -- split | dividend | spinoff | bankruptcy | merger_cash | merger_stock | ticker_change
  ratio_num REAL, ratio_den REAL, cash_amount REAL,
  applies_to_price INTEGER NOT NULL DEFAULT 1,              -- a spin-off does NOT apply to share count
  applies_to_shares INTEGER NOT NULL DEFAULT 1,
  acquirer_entity_id INTEGER REFERENCES pit_entity(entity_id), exchange_ratio REAL,
  terminal_value_per_share REAL, source TEXT NOT NULL, source_accession TEXT,
  UNIQUE (listing_id, event_date, event_type)
);

CREATE TABLE IF NOT EXISTS pit_macro_obs (
  series_id TEXT NOT NULL, obs_date TEXT NOT NULL, vintage_date TEXT NOT NULL,
  value REAL, available_date TEXT NOT NULL,
  revision_class TEXT NOT NULL,                             -- NOT_REVISED | REVISED
  source TEXT NOT NULL,
  UNIQUE (series_id, obs_date, vintage_date),
  CHECK (vintage_date >= obs_date)
);

CREATE TABLE IF NOT EXISTS pit_macro_manifest (
  series_id TEXT PRIMARY KEY, frequency TEXT, revision_class TEXT NOT NULL,
  vintage_coverage_start TEXT, series_first_obs TEXT,
  publication_lag_days INTEGER, units_note TEXT
);

CREATE TABLE IF NOT EXISTS pit_curve_obs (
  curve_id TEXT NOT NULL, obs_date TEXT NOT NULL, tenor TEXT NOT NULL,
  value REAL, available_date TEXT NOT NULL,
  UNIQUE (curve_id, obs_date, tenor)
);

CREATE TABLE IF NOT EXISTS pit_calendar (
  session_date TEXT PRIMARY KEY, session_index INTEGER NOT NULL, market TEXT NOT NULL
);

-- Regimes carry the date they became knowable, so hindsight labels fail the CHECK downstream.
CREATE TABLE IF NOT EXISTS pit_regime (
  regime_id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL, start_date TEXT, end_date TEXT,
  method TEXT NOT NULL, defined_on_date TEXT NOT NULL,
  fold_set_id TEXT, fold_index INTEGER,                     -- unsupervised: fit on train only
  source TEXT NOT NULL
);

-- ============ FEATURE STORE ============

CREATE TABLE IF NOT EXISTS pit_feature_definition (
  feature_key TEXT NOT NULL, model_version TEXT NOT NULL,
  description TEXT NOT NULL, tag_ladder_json TEXT, transform TEXT,
  winsorisation TEXT, norm_method TEXT, defined_on TEXT NOT NULL,
  UNIQUE (feature_key, model_version)
);

CREATE TABLE IF NOT EXISTS pit_peer_set (
  peer_set_id INTEGER PRIMARY KEY AUTOINCREMENT,
  as_of_date TEXT NOT NULL, rung TEXT NOT NULL, key TEXT NOT NULL,
  n_members INTEGER NOT NULL, n_price_resolvable INTEGER NOT NULL,
  crosswalk_hash TEXT NOT NULL, model_version TEXT NOT NULL,
  UNIQUE (as_of_date, rung, key, model_version)
);

CREATE TABLE IF NOT EXISTS pit_peer_member (
  peer_set_id INTEGER NOT NULL REFERENCES pit_peer_set(peer_set_id),
  entity_id INTEGER NOT NULL REFERENCES pit_entity(entity_id),
  UNIQUE (peer_set_id, entity_id)
);

CREATE TABLE IF NOT EXISTS pit_feature (
  feature_id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_id INTEGER NOT NULL REFERENCES pit_entity(entity_id),
  listing_id INTEGER REFERENCES pit_listing(listing_id),    -- NULL is normal: most peers have no price
  as_of_date TEXT NOT NULL, feature_key TEXT NOT NULL,
  sources_json TEXT NOT NULL,        -- [{taxonomy, tag, accn, filed, available_date}, ...]
  source_tag TEXT, source_tag_changed INTEGER NOT NULL DEFAULT 0,
  feature_available_date TEXT NOT NULL,     -- max(available_date) over contributing facts
  fact_max_age_days INTEGER,
  raw_value REAL, normalized_value REAL, norm_method TEXT,
  peer_set_id INTEGER REFERENCES pit_peer_set(peer_set_id),
  availability TEXT NOT NULL,               -- complete | partial | unavailable
  unavailable_reason TEXT,                  -- never_tagged | tagged_zero | company_had_none
                                            -- | not_yet_filed | ladder_exhausted | stale_beyond_max_age
  model_version TEXT NOT NULL, replay_run_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (entity_id, as_of_date, feature_key, model_version, replay_run_id),
  CHECK (feature_available_date <= as_of_date)        -- guardrail 5, at the column
);
CREATE INDEX IF NOT EXISTS idx_pit_feature_asof ON pit_feature (as_of_date, model_version);

-- ============ REPLAY OUTPUT ============

CREATE TABLE IF NOT EXISTS pit_score (
  entity_id INTEGER NOT NULL REFERENCES pit_entity(entity_id),
  listing_id INTEGER REFERENCES pit_listing(listing_id),
  as_of_date TEXT NOT NULL, model_version TEXT NOT NULL, replay_run_id INTEGER NOT NULL,
  company_score REAL, sector_overlay REAL, political_overlay REAL,
  final_score REAL, classification TEXT,
  benchmark_level TEXT, valuation_benchmark_level TEXT,
  n_industry_peers INTEGER, n_valid_peers INTEGER, n_ebitda_cohort INTEGER,
  weights_used_json TEXT, factor_scores_json TEXT, raw_inputs_json TEXT, notes_json TEXT,
  coverage REAL, confidence TEXT, created_at TEXT NOT NULL,
  UNIQUE (entity_id, as_of_date, model_version, replay_run_id)
);

CREATE TABLE IF NOT EXISTS pit_sector_score (
  as_of_date TEXT NOT NULL, rung TEXT NOT NULL, key TEXT NOT NULL,
  raw_score REAL, overlay REAL,
  growth_score REAL, roe_score REAL, roa_score REAL, debt_score REAL,
  n_eligible INTEGER, confidence TEXT,
  crosswalk_hash TEXT NOT NULL, model_version TEXT NOT NULL, replay_run_id INTEGER NOT NULL,
  UNIQUE (as_of_date, rung, key, model_version, replay_run_id)
);

CREATE TABLE IF NOT EXISTS pit_label (
  listing_id INTEGER NOT NULL REFERENCES pit_listing(listing_id),
  entity_id INTEGER REFERENCES pit_entity(entity_id),
  as_of_date TEXT NOT NULL, horizon TEXT NOT NULL,
  anchor_date TEXT, anchor_price REAL, anchor_roll_days INTEGER,
  end_date TEXT, end_price REAL, terminal_roll_days INTEGER,
  forward_return REAL, benchmark_forward_return REAL, excess_return REAL,
  net_forward_return REAL, cost_assumption_set_id INTEGER,
  censored INTEGER NOT NULL DEFAULT 0, terminal_reason TEXT,
  price_source TEXT NOT NULL, price_ingest_id INTEGER, computed_at TEXT NOT NULL,
  UNIQUE (listing_id, as_of_date, horizon)
);

CREATE TABLE IF NOT EXISTS pit_cost_assumption (
  assumption_set_id INTEGER PRIMARY KEY AUTOINCREMENT,
  effective_from TEXT, asset_class TEXT,
  commission_bps REAL, spread_bps REAL, borrow_bps REAL, financing_spread_bps REAL,
  adv_participation_json TEXT, roll_cost_json TEXT, option_premium_source TEXT,
  is_assumption INTEGER NOT NULL DEFAULT 1, source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pit_replay_run (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL, finished_at TEXT,
  as_of_from TEXT, as_of_to TEXT, as_of_grid TEXT,          -- daily | weekly | month_end
  model_version TEXT NOT NULL, universe_def_json TEXT, source_versions_json TEXT,
  crosswalk_hash TEXT, code_revision TEXT, seed INTEGER,
  status TEXT NOT NULL DEFAULT 'running',                   -- running|complete|superseded|invalidated
  superseded_by INTEGER, invalidated_reason TEXT,
  n_entities INTEGER, n_dates INTEGER, summary_json TEXT
);

-- ============ RESEARCH ============

CREATE TABLE IF NOT EXISTS ml_fold (
  fold_set_id TEXT NOT NULL, fold_index INTEGER NOT NULL, horizon TEXT NOT NULL,
  mode TEXT NOT NULL,                                       -- walk_forward | purged_kfold
  train_start TEXT, train_end TEXT, test_start TEXT, test_end TEXT,
  purge_days INTEGER NOT NULL, embargo_days INTEGER NOT NULL, embargo_rule TEXT NOT NULL,
  n_train INTEGER, n_test INTEGER, n_purged INTEGER, n_embargoed INTEGER, n_effective INTEGER,
  UNIQUE (fold_set_id, fold_index)
);

CREATE TABLE IF NOT EXISTS ml_fold_metric (
  fold_set_id TEXT NOT NULL, fold_index INTEGER NOT NULL, model_id INTEGER NOT NULL,
  metric TEXT NOT NULL, value REAL, n_effective INTEGER,
  UNIQUE (fold_set_id, fold_index, model_id, metric)
);

CREATE TABLE IF NOT EXISTS pit_prediction (
  model_id INTEGER NOT NULL, fold_set_id TEXT, fold_index INTEGER,
  entity_id INTEGER NOT NULL, as_of_date TEXT NOT NULL, horizon TEXT NOT NULL,
  point_estimate REAL, interval_lo REAL, interval_hi REAL,
  interval_method TEXT, interval_nominal_coverage REAL, created_at TEXT NOT NULL,
  UNIQUE (model_id, entity_id, as_of_date, horizon)
);

CREATE TABLE IF NOT EXISTS pit_proposal (
  proposal_id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
  title TEXT NOT NULL, claim TEXT NOT NULL, rationale TEXT NOT NULL,
  replay_run_id INTEGER, fold_set_id TEXT, model_id INTEGER,
  dataset_spec_json TEXT, metrics_json TEXT, effect_size REAL, n_effective INTEGER,
  folds_improved INTEGER, folds_total INTEGER, known_weaknesses TEXT,
  status TEXT NOT NULL DEFAULT 'OPEN',                      -- OPEN|ACCEPTED|REJECTED|SUPERSEDED
  decided_by TEXT, decided_at TEXT, decision_note TEXT
);
```

**The macro vintage selector** — missing from the first draft, and the reason guardrail 4 was only
claimed rather than met:

```sql
SELECT value FROM pit_macro_obs
 WHERE series_id = :s AND obs_date = :d
   AND vintage_date <= :as_of AND available_date <= :as_of
 ORDER BY vintage_date DESC LIMIT 1;
```

**The row shape you asked for.** Preserved as a view, with the horizon pivoted so it does not fan out
six-to-one (the first draft's `USING (asset_id, as_of_date)` returned six rows per feature):

```sql
CREATE VIEW IF NOT EXISTS v_pit_row AS
SELECT f.entity_id, f.listing_id, f.as_of_date, f.feature_available_date,
       f.sources_json AS source, f.feature_key, f.raw_value, f.normalized_value,
       f.model_version, s.final_score AS score,
       MAX(CASE WHEN l.horizon='1M'  THEN l.forward_return END) AS fwd_ret_1m,
       MAX(CASE WHEN l.horizon='3M'  THEN l.forward_return END) AS fwd_ret_3m,
       MAX(CASE WHEN l.horizon='6M'  THEN l.forward_return END) AS fwd_ret_6m,
       MAX(CASE WHEN l.horizon='12M' THEN l.forward_return END) AS fwd_ret_12m
  FROM pit_feature f
  LEFT JOIN pit_score s ON s.entity_id=f.entity_id AND s.as_of_date=f.as_of_date
                       AND s.model_version=f.model_version AND s.replay_run_id=f.replay_run_id
  LEFT JOIN pit_label l ON l.listing_id=f.listing_id AND l.as_of_date=f.as_of_date
 GROUP BY f.feature_id;
```

---

## 8. Labels, purging, and the sample-size reality

**Labels.** `adjclose`; trading-day horizons on one canonical calendar: `1D=1, 5D=5, 1M=21, 3M=63,
6M=126, 12M=252`. Unscheduled closures are real (Sandy 2012-10-29/30) and yearly session counts run
250–253.

**The calendar is BUILT.** `pit_ingest.py calendar` loaded `pit_calendar` from SPY daily bars:
**8,467 sessions, 1993-01-29 → 2026-09-18, with 165 month-end sessions from 2013.** (The draft's
"8,233 sessions 1994–2026" was a pre-build estimate; the loaded figure supersedes it.) SPY is the
source rather than a generated weekday list minus a holiday file precisely because a generated
calendar gets unscheduled closures wrong, and those are the days a naive calendar silently invents.
`range=max` is never used — it coarsens to monthly, which would have produced a "calendar" of
month-ends that every horizon lookup would then quietly have agreed with.

**Volume hygiene on BOTH ends** (the first draft only guarded the anchor). FRCB's 2023-05-01 and
2023-05-02 bars print the carried-forward $3.51 at **volume 0** after the FDIC seizure; the next real
trade was $0.3336. A stale **terminal** bar understates the loss by 90%; a stale **anchor** overstates
it. Require `volume > 0` at both ends and record `anchor_roll_days` / `terminal_roll_days`.

**Censoring, not dropping.** A window past the last bar is `censored=1` with `forward_return=NULL`.

**Termination by corporate action, not by absent bars.** Bankruptcy with no recovery is
`forward_return = -1.0` exactly; a bankruptcy with an OTC stub follows the stub (FRCB is −99.95% at
252 sessions, not −100%). **Dropping these rows is precisely how a backtest lies.**

**The purge/embargo cost — corrected.** The first draft quoted the monthly grid. `pit_feature` is keyed
by a daily `as_of_date`, so the **daily grid governs**:

| Horizon | Monthly grid | **Daily grid (governs)** |
|---|---|---|
| 3M | 1.8% | **2.6%** |
| 12M | 10.4% | **10.7%** |

Scheme: `purge = h`, `embargo = max(h/4, 90 calendar days)`. The 90-day floor is **new** and covers
fundamental first-appearance lag (median 34 days, p90 331 days quarterly). Even so, the measured
autocorrelation of the 12M label is still **0.163 at 189 trading days** — `h/4` is a floor, not a
comfortable margin. `embargo_rule` is stored per fold so provenance is auditable.

**The honest sample size.** Over 2010–2025 a 500-name universe yields roughly **80–300 genuinely
independent 12M observations**. **1M and 3M horizons must carry the early research**; 12M challengers
cannot be evidenced yet, and claiming otherwise would be manufacturing confidence.

**Missingness contract.** `ml_lab.build_dataset` currently drops a row if **any** feature is non-finite
(`ml_lab.py:311-318`). With the debt ladder at 56% coverage, listwise deletion would silently discard
nearly half the universe and bias it toward simple capital structures. The contract must be decided
explicitly, not inherited.

---

## 9. What is honestly not feasible

| Item | Verdict |
|---|---|
| **ICE BofA OAS before 2023-09-19** | **NOT FEASIBLE** — now a rolling 3-year window; `&cosd=` is silently ignored; ALFRED 404s. *(Moody's BAA10Y/AAA10Y substitute from 1986 — §1 correction 2)* |
| **Commodity term structure / carry / roll / basis** | **NOT FEASIBLE** — Yahoo `=F` is unadjusted spliced front-month; expired contracts 404 |
| **Options / implied-vol history BEFORE 2026-09-20** | **NOT FEASIBLE, PERMANENTLY** — expired contracts 404; only the current chain is served. **Shaffer Hedge cannot be backtested on real option prices for any date before the archive started.** The forward fix is now running: see §9.1 |
| **Historical GPI** | **NOT RECONSTRUCTABLE.** Event *dates* are partly recoverable (Federal Register API, OFAC, GDELT), but severity and exposure components are analyst judgements — re-scoring a 2022 event in 2026 is hindsight. `pit_score.political_overlay` will be NULL where live rows carry a number, and that asymmetry must be stated wherever the two are compared |
| **Delisted-equity prices** | **NO FREE SOURCE.** 21 of 26 delisted tickers 404 on Yahoo; the local crash dataset is itself a survivor set. This caps how completely the −100% outcomes can be populated, and it is the most consequential gap in the whole map |
| **Ticker-change and ADR-change events** | **NO FREE DATED SOURCE** |
| **Section 12(i) bank registrants** | **A NAMED HOLE** — First Republic and Signature never filed a 10-K or 10-Q; `companyfacts` 404s for First Republic. The largest bank failures of the period are structurally invisible in EDGAR, and they are exactly what a survivorship study wants |

### 9.1 The option archive — **BUILT AND RUNNING. Start date: 2026-09-20.**

The options gap had one cheap mitigation that only worked if started immediately: **archive the live
option chain daily**. It was started. Decision #5 in §11 is closed, approved and implemented in
`pit_options.py`.

**The archive start date is 2026-09-20.** First capture that day: **6 underlyings — SPY, AAPL, MSFT,
NVDA, AMZN, JPM — 8 expiries each, 7,500 contract observations**, written as one gzipped JSONL file
per snapshot under `pit_archive/options/YYYY/MM/DD/` with a sha256 in `pit_store.pit_option_snapshot`.

**Every option price before 2026-09-20 is UNAVAILABLE and always will be.** That is not a gap waiting
to be filled — it is a permanent property of the free option market data, and
`pit_options.coverage_statement()` prints it as a sentence so a future backtest states it rather than
quietly substituting a model price.

Three rules the archive enforces, in order of how much damage breaking them does:

1. **The raw chain is archived before any Shaffer calculation touches it.** Each record carries the
   venue's own JSON object verbatim under `raw`, beside a normalised view. Nothing scores, ranks,
   filters for "reasonable" quotes or repairs a crossed market. **A quote that looked wrong on the day
   is evidence about the day.**
2. **Greeks carry their provenance.** Yahoo's v7 option endpoint publishes **no Greeks at all**, so
   `greeks_source` is `'none'` on every snapshot written from it. A Greek computed later from a model
   is DERIVED, belongs in `greeks_derived`, and may never be written into a source field — a derived
   delta stored as if the venue had published it would make a backtest look like it had data it never
   had.
3. **Raw chains are files; SQLite holds only a manifest.** The write order is file → checksum →
   manifest row, never the reverse. A failed capture records `status = 'error'` with the message,
   because **a silent gap and a documented outage look identical in a coverage report a year later,
   and only one of them is honest.** The first day's manifest demonstrates this: the 23:26:49Z run
   recorded six `error` rows reading *"Yahoo rejected the crumb request. Option chains are
   unavailable."*, and the 23:28:27Z retry recorded the six `ok` rows. Both are still in the
   manifest. The failure was not overwritten by the success.

The remaining open question is **cadence**, not existence: the capture is intended for 15:45 ET on
weekdays, before the 16:15 option close, and a day not captured is permanently lost.

---

## 10. Tests — the map's own verified cases become the test suite

The repo has no pytest; tests are hand-run procedural `test_*.py` scripts with a `check(name, cond)`
helper, a `main()`, PASS/FAIL per line and a nonzero exit on failure. Each case below is a fixture
this document proved.

**Written and passing:**

| # | Case | Script |
|---|---|---|
| 1 | **Ticker reuse.** A `BBBY` pull is rejected by the `firstTradeDate` gate for a 2015 as-of | `test_pit_identity.py` |
| 2 | **Survivorship gate.** Lehman, SVB, Sears, BBBY, Enron, old GM, new GM, Berkshire, First Republic — all representable; Enron quarantined; Berkshire not delisted | `test_pit_identity.py` |
| 3 | **The latency boundary, both directions.** 15:29 / 15:30 / 15:30:01 / 15:31 / 16:05, weekend and holiday acceptance, late-evening acceptance, missing and unparseable timestamps, `Z` ignored, a real offset converted, and `available_date >= filed` on every sample | `test_pit_policy.py` |
| 4 | **The ASC 606 date gate.** The two 606 tags are invisible at 2016 and lead the ladder at 2019; the gate opens on 2018-01-01 exactly | `test_pit_policy.py` |
| 5 | **The staleness boundary.** FY2019 usable through 2021-03-31, stale 2021-04-01; SVB's FY2011 net income stale at a 2019 as-of | `test_pit_policy.py` |
| 6 | **DERA filters.** A dimensional row never reaches `pit_fact`; `qtrs` outside 0–4 rejected; month-end period normalisation; append-only restatement path | `test_pit_dera.py` |
| 7 | **The two leakage fences.** The `--date` relabel trap and the unpurged splitter both fail loudly if the fence is removed | `test_pit_hazards.py` |
| 8 | **Option archive integrity.** The raw venue object round-trips byte-identically; the manifest sha256 stops matching the moment a file is edited | `test_pit_options.py` |
| 9 | **Lineage drift.** Recorded weights are compared against the **imported** production constants, so editing a weight fails the test | `test_pit_lineage.py` |

**Still to write — each blocked on a component that does not exist yet:**

| # | Case | Blocked on |
|---|---|---|
| 10 | **Golden restatement.** KHC FY2016 net income at 2019-06-06 = $3,632M; at 2019-06-07 = $3,596M | The `companyfacts` complement / a loaded KHC history |
| 11 | **Future-data canary.** Insert a fact with `available_date = t+1`; assert it never reaches a feature row at *t*. **This is the test that guards the selector, which the CHECK cannot** | The replay (§0.1) |
| 12 | **Split reconstruction.** AAPL 2015-06-30 must resolve to 125.43 as-traded, never 31.3575 | The price ingest |
| 13 | **Spin-off classification.** `T` 2022-04-11 "1324:1000" must classify as a spin-off (price-applies, shares-does-not), not a split | The corporate-action ingest |
| 14 | **Zero-volume.** FRCB 2023-05-01/02 must roll rather than anchor or terminate | The label builder |
| 15 | **ALFRED clamp.** A future `vintage_date` must be rejected by the header-echo assert | The macro ingest |
| 16 | **Frozen regression.** The stdlib Shaffer v1 must reproduce AUC 0.8233 at w=0 | The replay |
| 17 | **The 53-name casualty list** must resolve for its era | The price ingest (9 of the 53 are already gated in #2) |

---

## 11. Open decisions

**Closed since the draft:** #3 (start date — **2013-01-01**, with the DERA warm-up window running
from 2009q2 so the growth factor has its three annual revenue points) and #5 (**the option archive was
approved and started; it began 2026-09-20**, §9.1). The rest remain open.

1. **Model version for the historical twin.** A PIT replay cannot reproduce today's live score (§4).
   *Recommend:* build `equity_shaffer_v1_pit` as a distinct version, run it forward in parallel with
   live, and revisit switching production only once the gap is measured.
2. **Phase 1 scored universe.** S&P 500 dated membership gives a defensible dated *ticker* set but the
   CIK link is unproved for 37% of the 2008 list. *Recommend:* start from names where ticker↔CIK is
   provable via `dei:TradingSymbol` on the filed cover page (FY2011+), accept a smaller Phase 1, and
   widen as the identity map improves. The **peer** universe is survivorship-free either way.
3. ~~**Start date.**~~ **CLOSED: 2013-01-01.** The first draft said 2012-01-01 on the strength of a
   coverage table built from the frames API it elsewhere bans — a self-contradiction. Rebuilt from PIT
   filer counts and with a warm-up for the three annual revenue points the growth factor needs, the
   answer is **2013-01-01** (`pit_dera.SCORING_START`). The ingest window itself starts at **2009q2**
   so the warm-up is the whole available history rather than the bare minimum of 2010q1. If
   `DTWEXBGS` is wanted as a macro feature, its vintage archive starts **2019-02-04** and would bind
   instead — so it is dropped from the early feature set rather than moving the start.
4. **Credit.** *Recommend:* Moody's `BAA10Y`/`AAA10Y` from 1986, labelled an option-unadjusted
   seasoned-corporate spread, never an OAS. **Still open — no macro ingest is built.**
5. ~~**Start the option-chain archive now?**~~ **CLOSED: yes, and it started 2026-09-20.** See §9.1.
6. **Missingness contract** (§8) — listwise deletion, or explicit availability flags as features?
   **Still open, and now more urgent**: the DERA shares-outstanding hole (§2.1.4) adds a second
   structurally-absent input beside the 56% debt ladder.
7. **NEW — the latency buffer is a judgement, not a measurement** (§2.1.1). 15:30 was chosen, not
   timed. Whether to measure a realistic retrieve-parse-score-trade lag and issue an
   `information_latency_policy_v2` is an open question; until it is answered, v1 stands and every row
   it produced is identifiable by `pit_fact.latency_policy_version`.

## 12. Build order — progress

| Step | | Status |
|---|---|---|
| 1 | Fence the `--date` trap (`snapshot_provenance`) and fence `ml_lab.walk_forward_splits` | **DONE** |
| 2 | Schema — §7, with triggers and CHECKs | **DONE** — 30 tables, 4 triggers |
| 3 | Identity layer → `pit_entity*`, `pit_listing`. *The survivorship gate must pass here, before any fundamentals are loaded* | **DONE — gate passed** (§5.1) |
| 3b | Calendar → `pit_calendar`. *Moved AHEAD of the fundamentals: `available_date` needs a session resolver and `pit_fact` is immutable, so a wrong date cannot be repaired in place* | **DONE** — 8,467 sessions |
| 4 | DERA bulk ingest (2009q2→2026q2, consolidated rows only) → `pit_fact` | **RUNNING** — 69 quarters, ~32 min |
| 4b | Per-issuer `companyconcept` complement → share counts | **IN FLIGHT — mandatory** (§2.1.4) |
| 5 | Prices + corporate actions → `pit_listing`-keyed, with the `firstTradeDate` gate | Not started |
| 6 | Macro vintages + curve → `pit_macro_obs`, `pit_macro_manifest`, `pit_curve_obs` | Not started |
| 7 | Peer sets → `pit_peer_set` | Selector built; snapshotting not started |
| 8 | **Replay** → `pit_feature`, `pit_score`, `pit_sector_score` | **Not started. Not one line.** |
| 9 | Labels → `pit_label` | Not started |
| 10 | Purged walk-forward splitter + fold registry → `ml_fold`, `ml_fold_metric` | Not started |
| 11 | Challengers → `pit_prediction`, reported fold-by-fold, RESEARCH status only | Not started |
| 12 | Proposals → `pit_proposal`, human promotion only | Not started |
| — | Option archive → `pit_option_snapshot` (**out of band — it could not wait**) | **RUNNING from 2026-09-20** |

**The budget question that gated step 4 is answered.** It was: *"the DERA ingest is ~5.6 GB across 70
quarters with `num.txt` alone at 2.8M rows per quarter — a tag-filter policy, an expected `pit_fact`
row count, disk and wall-clock estimates, and a bulk-insert path are required."* Measured answer in
§2.1.5: **69 quarters, 5.26 GiB down, a 38-tag filter computed from the concept ladders,
12,563,014 retained rows, 4.36 GiB on disk, ~32 minutes at 32,191 rows/s**, via `executemany` in
20,000-row batches into a separate historical `.db` file — `storage.py`'s per-row helper convention
was indeed not used.
