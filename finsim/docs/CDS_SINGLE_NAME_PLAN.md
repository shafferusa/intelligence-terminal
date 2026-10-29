# Single-name CDS on the prime-broker OTC desk - build plan for release 1.0.7

Designer, 2026-10-28 18:30 EDT (clock from `date`). Design only: no code, test or doc other than this file was edited.
Revised by the designer 2026-10-28 19:59 EDT (clock from `date`) after the independent critic's 14 findings CDS-C-01..14. Each finding was verified
against the data and the code, then fixed here or answered with evidence; one line per finding is in the last section, "Critic answers".
Still design only: this file is the only file edited.

**The owner's request (2026-10-28 17:05 ET, verbatim):** "CDS needs added to prime brokers, i see CDX, but I want to CDS on everything you can"

**Also binding:** his Institutional Book V3 directive (`docs\OWNER_INSTITUTIONAL_V3_2026-10-28.md`, 17:57 ET) asks for "Oracle single-name five-year
CDS: $25M" buy protection. It also says: "Validate ... legal reference entities, reference obligations, coupon, upfront consideration, accrued
premium, recovery assumptions and credit-event settlement. Report CS01, upfront cash, running premium, mark-to-market and counterparty margin for
every trade. Do not fabricate an executable CDS quote if reference data is missing." This plan delivers the product that trade needs. Booking it
belongs to the V3 workflow after 1.0.7 is deployed, not to this build, and only at his own negotiated mid: the model is far from where ORCL
trades (0, 3.4, 9.5 step 7).

**Build window.** This build ships as release 1.0.7.
- Which build goes first is the owner's choice (Q13): both requests came the same evening. Until he answers, the build follows the FX /
  cross-currency plan (`docs\FX_CCS_PLAN.md`, draft of 18:15 EDT, its D1 and R10): 1.0.6.1 first. That plan says: "The single-name CDS plan
  (1.0.7) rebases on 1.0.6.1. The two pools never run at the same time." That is right for a 7.6 GB machine running four builders, their
  servers and browsers.
- So this build starts from the gated, committed 1.0.6.1 tree, or from 1.0.6 if 1.0.6.1 is dropped or the owner puts CDS first. No 1.0.7
  edit touches the tree before that gate has committed it (FINSIM1_PLAN 15.1 rule 3). The only earlier work allowed is B3 and the core of
  B2 in a scratch folder outside the repository (section 10).
- Section 9.1 says how the shared files are handled.

**Inputs read**
- The research hand-back (2026-10-28, two parts):
  - data: 291 real requests with honest user agents, SEC kept at 4 requests a second or less;
  - code: FinSim1 read-only, at the 1.0.6 tree of about 17:30 ET.
- The research's own files under `<scratchpad>\cds-research\`.
- These code files, read in full or in the sections named:
  - `products\swaps_cds.py`, the whole file;
  - `products\swaps.py`: dispatch, IM / funding, h_book, VM core, close_contract, vm_keys, contract_view, display fields, RFQ, timeline, the
    Derivatives OTC view, routes;
  - `prime.py`: FIELD_SPECS, OTC_SEEDS, OTC_LIVE;
  - `db.py`: the CHECKs, OTC_KINDS, the widening, next_id;
  - `risk.py`, `accounting.py` (OTC rows), `api_views.py` (exposure footer), `blotters.py` (OTC rows), `instruments.py` (otc_reference_ids),
    `lifecycle_jobs.py` (ACCRUAL_PRODUCT), `lifecycle_eod.py` (recorded_set, ProductJob);
  - `quotes\http.py`, `providers.py`, `parsers.py`, `rates.py`, `worker.py` (refreshers);
  - `swaps_trs.close_on` / `_vm_close`, `server.py`, `api.py`;
  - `static\pages\markets_otc.js`, `components\cdsticket.js`, `swapticket.js`, `pages\derivatives.js`, `risk.js`, `positions.js`,
    `portfolio.js`, `cash.js`, `core.js`, `index.html`;
  - `tests\test_terminal_cds.py`, `terminal_helpers.py`, `test_terminal_static.py`, `terminal_fixture_server.py`.
- `docs\FINSIM1_PLAN.md` sections 3 and 15 (15.1, 15.9 and 15.15).
- `docs\BUILD_LOG.md`, through 18:14 ET; for the revision through 18:32 ET (the DTCC terms decision), with the 18:35 ET note at the top of
  `docs\OWNER_INSTITUTIONAL_V3_2026-10-28.md`, `docs\INSTITUTIONAL_V3_PLAN.md` (19:14 EDT: 1.3, D7 and line 4.4) and `docs\FX_CCS_PLAN.md`
  sections 2 (D1) and 11.
- For the owner report only: public news on Oracle's CDS level (Appendix E). The app never fetches news.
- The CDX hook script `<scratchpad>\cdx-hooks.py`.
- The CreditGrades Technical Document (RiskMetrics Group, ed. C. Finger, May 2002). It was fetched from the MSCI support site as a PDF and its
  text extracted with a stdlib script.
- ISDA's 2015 FAQ on amending the single-name CDS roll frequency (web search result, isda.org), for the semi-annual roll.

**Design-time checks I ran** (scratch only, `<scratchpad>\cds-design\`; offline; read-only imports of the repo with `-B`, no bytecode written):
- `calib_eval.py`: the first draft's comparison with traded CDS levels, from DTCC files the research downloaded before the DTCC terms
  decision. Its inputs were flawed and its source may not be used again (3.4): it is a record of the first draft, not evidence.
- `cg_vectors.py`: the CreditGrades test vectors with every intermediate (Appendix A).
- `isda_example.py` and `seller_example.py`: the worked deal on the existing `swaps_cds` pure functions (Appendix C).
- `coverage_count.py`: the coverage count from the research's EDGAR frames (section 4.1).

**Revision checks** (`<scratchpad>\cds-design\rev\`; run with `py -3.12 -I` on the SEC, Yahoo and FRED files the critic fetched at
18:52-18:56 ET with honest user agents, plus five SEC frames I fetched at 19:30 ET with the GOV user agent, under 3 requests a second; no
DTCC file was opened):
- `orcl_model.py`, `four_names.py`: the shipped model on real 10-28 inputs for ORCL, VZ, KBH and KSS, with both debt rules (3.4, Appendix B).
- `cf_tags.py`, `frames_check.py`, `coverage_exch.py`: the debt tags, the double counts, the tag gaps and the counts with the exchange
  filter (3.2, 4.1).
- `distress_check.py`, `simpson_vectors.py`: the overflow, the cancellation and the quadrature vectors (3.1, Appendix A).
- `sec_flagtags.py`: the insurer flag tags (3.5).

`<scratchpad>` = `C:\Users\logan\AppData\Local\Temp\claude\C--Users-logan-intelligence-terminal\5e0edd51-72c4-438a-89eb-77c644650257\scratchpad`.

---

## Contents

0. Summary
1. Facts this plan stands on
2. The product
3. The spread model
4. Coverage, listing and search, refresh, caching, stale rules
5. Initial margin, risk, ledger, UI
6. Architecture and contracts between the packages
7. Rules for every package
8. Packages B1-B4
9. Integration, verification, gate and deploy
10. Hours
11. Owner questions (the build proceeds on the defaults)
12. Deferred, with reasons
- Appendix A: CreditGrades test vectors
- Appendix B: design-time checks: what they show and what they cannot
- Appendix C: the worked deal (money test numbers)
- Appendix D: hook-edit catalogue
- Appendix E: sources
- Critic answers (one line per finding of the independent critic, CDS-C-01..14)

---

## 0. Summary

**What the owner gets.** A Single-name CDS sub-tab on every prime broker's OTC tab, next to "Credit (CDX)". It covers about 1,650-1,800 US-listed
companies, and he can search for one or browse all of them.

**The contract and its lifecycle**
- Buy or sell protection on one company's senior unsecured debt.
- 1-10 year standard maturities, 5Y by default.
- The standard 100 or 500 bp coupon, with an upfront and the accrued rebate.
- Coupons quarterly; daily variation margin under the CSA.
- Termination at any time.
- A credit event the owner triggers himself, at a recovery he enters (40 % by default).

**How it reuses the CDX machinery.** It runs on the swaps_cds machinery: the ISDA-style pricing, IMM dates, premium leg, coupons, VM,
termination and close-out are imported, not rewritten. It runs as a new contract kind `CDS` in new modules, so the owner's live CDX-000001 /
-000002 never touch the new code.

**The spread (labelled MODEL everywhere).** No free single-name CDS quote exists, so every spread is a model spread built in two steps:
1. Equity-implied CreditGrades, the RiskMetrics 2002 model. It uses:
   - the share price;
   - the 1000-day realised volatility (the estimator the CreditGrades document found best);
   - the debt per share that the company reports to the SEC in XBRL.
2. A floor: the ICE BofA AAA US corporate OAS from FRED (3.3).

**How good it is: not validated, and the owner is told so plainly.**
- It is a model, not a market level. The first draft's comparison with traded levels used files fetched before the DTCC terms decision,
  so it cannot be reused (3.4). It was weak anyway: most investment-grade names sat at the AAA floor, and a constant 42 bp did almost as
  well as the model. Checked on today's production inputs, names whose CDS trades are missed by about 2 times in both directions.
- **The owner's own target is the clearest miss.** On the real 10-28 inputs (Yahoo close 144.54, 1000-day volatility 0.4577, SEC debt of
  125.3B-129.5B over 3,023,736,000 shares) the model gives ORCL 112.54-115.74 bp, BBB-like. Public news put Oracle's 5-year CDS at 295-315
  bp on 2026-10-19 (CNBC, as reported by Wealth Professional on 10-20), about 2.5-2.8 times the model. On the model, the V3 trade's USD 25M
  would pay an upfront of about USD 0.21M; at 300-315 bp it is about USD 2.0-2.2M.
- So **no new deal runs on the bare model by default.** The first deal on a name in a PB account needs either the owner's **negotiated
  mid** (stored as a basis to the model, so marks keep moving with the model's inputs) or his explicit confirmation "deal at the MODEL - no
  market level known" (3.5). Without either, a preview shows its numbers but is not bookable (`DEALING_LEVEL_UNCONFIRMED`).
- The dealer deals at the mid plus or minus a half-spread by spread bucket, times the PB schedule's OTC spread multiplier.

**Initial margin.** A fraction of the notional by the spread bucket of the mid when the dealer quotes, per broker group. It is assumed, editable per account,
and 4-30 %.

**Risk.** CS01 and jump-to-default per contract and per name, a concentration warning, spread scenarios, and equity scenarios through the
model (V3 section 16 asks for "Oracle rallies 50%").

**Ledger.** It reuses the CDX GL accounts. No new GL is needed.

**Packages: four file-disjoint packages for four parallel builders.** Each owns only new files. Every edit of an existing file is an anchored,
idempotent hook script that the orchestrator applies at integration. This is the CDX precedent, `cdx-hooks.py`. It keeps the edits to the
files shared with the FX / cross-currency build (1.0.6.1) small and easy to re-anchor.

| Package | Builds | Hook edits in |
|---|---|---|
| B1 Engine | `products\swaps_cds_names.py`: the contract, deal (with the dealing-level guard and firm quote terms), booking, valuation and VM hook, premium leg, CDS_COUPON job, termination, credit event, desk / preview / terms / basis / basis-effect routes, risk block (spread and equity scenarios) | db, prime, server, swaps, lifecycle_jobs, swaps_cds (one sentence) |
| B2 Reference entities | `credit_entities.py`: SEC EDGAR universe (exchange allowlist) and fundamentals (XBRL frames in bulk, debt recipes R1-R6 with the parts rule, per-name freshness on demand), point-in-time history, financial flags with their source, search | risk, accounting, api_views, blotters, instruments |
| B3 Spread model | `products\cds_model.py`: CreditGrades (closed form, and quadrature for distressed names), realised volatility and persisted split events from Yahoo daily closes, the FRED floor and ladder, buckets, live and VM-date inputs, the equity-shock helper, the on-demand fetch queue | none |
| B4 UI | `static\components\cdsnames.js`: the desk (search / browse with flag sources, ticket, preview with the dealing-level blocker, assumptions, the negotiated-mid and deal-at-the-MODEL dialogs with their effect lists, credit-event dialog, risk panel) | api.py, index.html, the fixture server, markets_otc, swapticket, derivatives, positions, portfolio, risk, cash, core |

**Hours.**
- Builder pool: 5.25-5.75 h wall clock (about 19.5 builder-hours). That is a little over the requested 3-5 h: the critic's fixes add about
  0.75 h to B1, the longest package.
- Prep, integration, verification, fixes, gate and deploy: 5.25-7.75 h more.
- Total: 10.5-13.5 h to a deployed 1.0.7 (section 10). With the default order (1.0.6.1 first), 1.0.7 most likely deploys on Friday 10-30;
  the order is the owner's choice (Q13).

---

## 1. Facts this plan stands on

**Data (research, verified 2026-10-28)**

SEC EDGAR, with the `LoganTerminal/1.0 (loganshaffer87@gmail.com)` user agent, at 10 requests a second or less:
- `company_tickers.json` lists 10,429 tickers on 8,024 CIKs.
- The XBRL **frames** API returns one tag for every filer and one calendar quarter in one request. Row fields are `cik`, `val`, `end` and `accn`
  (no form, no filed date).
- `companyfacts` per CIK carries form and filed dates. It is 2.5 MB on average and drops dimensional facts.
- `submissions` per CIK has the SIC code and the recent filings list.
- Gaps the research measured:
  - 6 of 36 sample names lacked their latest 10-Q in companyfacts (F, AAL, C, PLD, NEE, DUK);
  - multi-class issuers have missing or stale share counts (F, CHTR, BRK, SPG, CVNA, RIVN);
  - Ford and Berkshire use custom debt tags;
  - 20-F filers use IFRS tags, annually.

FRED (`fredgraph.csv`) has the ICE BofA OAS by rating, daily, posted the next business day, with a rolling 3-year history:
- 2026-10-27: AAA 0.42 %, AA 0.63, A 0.73, BBB 1.09, BB 1.87, B 3.22, CCC 13.21; IG 0.88; HY 3.18.
- The terminal already fetches all of these every 6 h (`quotes\rates.py` `OAS_SERIES`, `FRED_SERIES`) and keeps them dated (`RateStore.as_of_le`).

Yahoo chart daily bars (a browser user agent is allowed for Yahoo only):
- 19 of 20 test symbols returned 221-251 bars a year.
- The existing provider method `YahooChart.daily()` caps at 3 months. The internal `_chart(symbol, query)` takes any range.

DTCC public dissemination (SEC Reg SBSR / CFTC Part 43): **not used.**
- The owner said "Yes, if the terms allow" (2026-10-28, about 18:30 ET). The orchestrator then checked DTCC's Terms of Use (dtcc.com/terms,
  2026-03-11): they grant only a personal, revocable license and prohibit automated extraction and compiling the content into a database
  (BUILD_LOG 18:32 EDT; the 18:35 ET note at the top of `OWNER_INSTITUTIONAL_V3_2026-10-28.md`). So neither the app nor any builder uses
  DTCC data.
- The research had already downloaded 7 DTCC files with a script before that decision, and the first draft's comparison (3.4) used them.
  That comparison stays below only as a record of the first draft. No builder opens or reuses the files, and the app never stores,
  refreshes or reproduces them. Traded prints are not planned (Q10, 12.1).

No machine-readable free source of per-company credit ratings exists:
- S&P, Moody's and Fitch 17g-7 pages are JavaScript applications;
- FINRA's terms of use forbid scraping its bond pages.
- So this plan never shows a rating, only a "rating-like bucket" of the model spread.

**Model (the CreditGrades Technical Document, May 2002)**
- Section 2.2: "the global debt recovery ... L-bar and lambda are estimated to be 0.5 and 0.3, respectively" (from about 300 defaulted US
  non-financial firms, 1987-1997).
- Section 3: "Overall, the 1000-day window appears to work best" and "a simple measure of volatility based on the last 1000 days of equity" prices
  is a good approximation.
- Appendix B: debt per share = financial debt / equivalent shares. Financial debt is short- plus long-term borrowings, plus 50 % of other
  liabilities, plus 0 % of accounts payable.
- The universe note: "CreditGrades has been calibrated for use with industrial firms ... The universe does not include bank and finance firms.
  These entities are very highly leveraged, which results in very high CreditGrades scores. Actual spreads for these sectors are much tighter."

**Code (1.0.6 tree, about 17:30-18:20 ET; line numbers move, so builders re-anchor at the gated base commit: 1.0.6.1, else 1.0.6)**

CDX is a delegate kind:
- `swaps._module_for` (swaps.py:592) routes `CDS_KINDS = ("CDX",)` to `services.swaps_cds`.
- `swaps.h_book` / `preview` / `h_terminate` / `mark` / `dv01_c` / `leg_specs` / `next_events` / `contract_view` delegate per kind.
- SWAP_VM calls the kind's `mark(..., valuation_date=d)`. It accepts `(None, {vm_outcome: CLOSE_PENDING | CAUGHT_UP, note, quiet, quiet_until})`.

`swaps_cds.py` public pure functions (its `__all__`):
- `on_the_run`, `accrual_start`, `coupon_periods`;
- `hazard`, `survival`, `df_function`, `rpv01`, `price`;
- `upfront_c`, `value_c`, `cs01_from`, `rebate_c`, `coupon_amount_c`.
- They are index-agnostic. The worked deal in Appendix C was computed with them.

`swaps_cds` booking, coupon and credit-event postings are generic once a deal dict exists:
- IM Dr 1330 / Cr 1010;
- upfront cost basis Dr 1335 (paid) or Cr 2345 (received);
- the accrued rebate Cr 2340 for a buyer or Dr 1225 for a seller;
- the net initial payment Dr 1200 or Cr 2100, with a settlement row;
- premium nights Dr 4453 / Cr 2340 (buyer) or Dr 1225 / Cr 4453 (seller);
- coupons funded with CSA netting;
- protection to 4450.

Kind-bound pieces that a new kind must replace or extend:
- `swaps_cds.h_coupon` refuses any other kind (`row["kind"] != KIND`) and its `coupon_keys` filters on KIND, so a new kind needs its own coupon
  job.
- The CDX credit event uses equal weights and refuses to exhaust the notional (`NOTIONAL_EXHAUSTED`), so a single name needs its own handler,
  which closes the contract.

What the platform hooks already give for free:
- `otc_contracts.kind` has a CHECK. Adding a kind to `db.OTC_KINDS` and the DDL makes `Database._widen_otc_kinds` rebuild the owner's table
  once, at the first open, keeping every row (tested by `test_terminal_core`). `status` has no widening, so a credit event closes a contract
  as `TERMINATED`.
- `db.next_id(conn, "cds")` gives `CDS-000001` with no db.py edit.
- `lifecycle_jobs.ACCRUAL_PRODUCT` filters the premium accrual by kind, so the new kind must be added there or its premium never accrues.
- `instruments.otc_reference_ids` makes the quote worker pin a contract's reference equity at HELD priority and makes MARKS_RECORD record its
  official close. It filters by kind and reads `terms.underlying_id`.
- `swaps._input_stamp` reads the inputs' `underlying` (`close_date` or `quote_time`), `curve_as_of` and `proxy.as_of`. A CDS valuation that
  names its inputs that way gets the intraday versus last-VM labels for free.
- `quotes.worker.register_refresher(name, interval_s, fn, run_after_et, label)` runs a module's fetches on the worker threads, never on a
  request thread and never inside the writer. A refresher must not touch the network when `services.providers` is the offline FakeProvider.
  Refreshers write through light commands; the commands table keeps every payload, so write only changed rows.
- `quotes.http.ua_for` already gives the GOV user agent for `*.gov`; `RateLimiter` and `ProviderHealth.record(name, ...)` accept any provider
  name.
- `server._bind_optional` imports, registers and binds every OPTIONAL_MODULES entry. `Router._register_products` adds each bound module's
  `register_routes`, and the first match wins. `api.unsupported_rows` collects each bound module's `unsupported()`.

**The owner's live state (BUILD_LOG)**
- F1: Goldman TRS, CDX IG 3M / HY 4M buy protection (CDX-000001 / -000002), and an IRS.
- I1 holds USD 1B with `I1:PB_GS` open.
- Part A behaviour is final (FINSIM1 15.9 item 8). This build must leave every CDX mark, VM, accrual and coupon identical (acceptance in 9.3).

---

## 2. The product

### 2.1 Contract terms (single-name CDS, North American standard as simplified here)

| Term | Value | Basis |
|---|---|---|
| Kind | `CDS` (contract ids `CDS-000001` ...); an `otc_contracts` row with a SWAP instrument row; PB accounts only; eligible books only (`books.require_pb_otc`: the PB / OTC threshold and the ECP floor) | the CDX pattern |
| Reference entity | One SEC registrant (CIK) with a US listing; the contract stores the CIK, the legal name (SEC conformed name), the ticker and the reference equity's instrument id at booking | 4.1 |
| Seniority / obligation | Senior unsecured. The contract references the entity's senior unsecured level. No reference-obligation ISIN is stored: the ISDA standard reference obligation (SRO) list is published by ISDA / S&P Global (Markit RED), and its free availability was not established for this build. Said in the preview and on the Unsupported list | assumed, labelled |
| Credit events | XR14: bankruptcy and failure to pay, no restructuring (ISDA 2014 definitions, the North American corporate standard). Simulated only, owner-entered | labelled |
| Side | BUY_PROTECTION (pay the coupon; short credit) or SELL_PROTECTION (receive it; long credit). `terms.receiver = (side == SELL_PROTECTION)`, which gives the position its sign in accounting | as CDX |
| Notional | USD 100,000.00 to 1,000,000,000.00, in cents | as CDX |
| Tenor and maturity | 1, 2, 3, 4, 5, 7 or 10 years (default 5). Semi-annual roll, see the rule below this table | ISDA's 2015 amendment of the single-name roll frequency (FAQ, isda.org): since December 2015 single names roll on 20 March / 20 September to 20 June / 20 December maturities ("March 20, 2016 - Single Name CDS contracts will 'roll' to June 20, 2021") |
| Coupon | 100 or 500 bp running, the owner's choice. Default by the bucket of the MODEL spread before any negotiated basis: 100 bp unless that bucket is BB-like or worse, then 500 bp. A negotiated mid never changes the default: an investment-grade name that trades wide keeps the standard 100 bp contract (ICE lists Oracle at the 100 bp coupon only). The coupon is fixed when the dealer quotes (Booking path) | SNAC fixed coupons (ICE lists 246 names at 100, 199 at 500); the convention follows the reference entity's standing, not today's spread |
| Recovery for pricing | 40 % (senior unsecured market convention) | as CDX IG |
| Dates and accrual | Exactly as CDX (see below this table) | `swaps_cds.accrual_start` / `coupon_periods` |
| Upfront | U = (S - C) x RPV01(S) x N, paid by the buyer when positive; price = 100 - 100 x U / N. The ISDA-style flat-hazard approximation of `swaps_cds`, labelled MODEL | as CDX |
| Initial payment | Upfront and rebate net, settled **T+3 business days** (the bilateral standard; the PB is the counterparty of record) as a settlement row (`ref_type` otc) | Q6 |
| Variation margin | SWAP_VM at 17:05 ET on the model value of that date's inputs, CTM or STM per the PB schedule's CSA; FRED posts a business day's OAS the next morning, so a CDS VM of D posts the next morning, as CDX | swaps VM core |
| Termination | Owner, at any time: the premium accrued through today first, then the close-out at the mid minus or plus the half-spread, against the owner | as CDX |
| Credit event | Owner-triggered (section 2.4); the contract closes | new |
| Booking path | RFQ only, as every OTC kind since 1.0.6: Request quote, then the simulated dealer quotes on the next lifecycle tick (about 30 s); the quote is good for 120 s; accepting books at the quoted spread, coupon, maturity and tier (a firm kind: all four are fixed when the dealer quotes, so a coupon left to its default cannot change between the quote and the booking) | swaps C14 |
| Dealing level | A new deal on a name needs, for that PB account, the owner's negotiated mid or his confirmation to deal at the MODEL (3.5). Without it the preview shows every number but is not bookable, and a quote request or booking answers 409 `DEALING_LEVEL_UNCONFIRMED` in words | new (the V3 rule "do not fabricate an executable CDS quote") |
| No as-of booking | `CDS` is not added to `swaps.ASOF_KINDS`; an as-of CDS answers 409 `ASOF_INPUT_MISSING` (its trade date's model inputs are not recorded) | |

**Maturity rule.** `roll = swaps_cds.on_the_run(cal, trade_date)["roll_date"]` (20 March / 20 September, rolled following). Then:
`maturity = date(roll.year + n, 12 if roll.month == 9 else 6, 20)`.

| Trade date | 1Y | 3Y | 5Y | 7Y | 10Y |
|---|---|---|---|---|---|
| 2026-10-29 | 2027-12-20 | 2029-12-20 | 2031-12-20 | 2033-12-20 | 2036-12-20 |
| 2027-03-22 or later | | | 2032-06-20 | | |

**Dates and accrual (exactly as CDX)**
- Coupons are paid on 20 March / June / September / December, rolled following on the NYSE calendar (ASSUMED equal to the New York calendar).
- ACT/360.
- Accrual starts on the latest adjusted coupon date on or before the trade date.
- Full first coupon, with an accrued rebate of N x C x (T + 1 - accrual start) / 360 paid by the seller.
- The last period includes the maturity date.

### 2.2 Lifecycle and jobs

| Event | Job / command | When | Mechanism |
|---|---|---|---|
| Quote request | `otc.rfq.request` (kind CDS) | owner | swaps C14; `FIRM_KINDS` gains CDS (hook H1-11); refused 409 `DEALING_LEVEL_UNCONFIRMED` until the name has a dealing level in that account (3.5) |
| Booking | `otc.book` (kind CDS) | the accepted quote | `swaps.h_book` -> `swaps_cds_names.book`; `mark_new_contract` -> PENDING_CONFIRM; OTC_CONFIRM at 17:05 |
| Premium nights | ACCRUE_DAILY `swappay` / `swaprcv` | 00:30 ET each night | `swaps.accrue_night` -> the module's `leg_specs` (FIXED at C, ACT/360); `ACCRUAL_PRODUCT` gains CDS (hook H1-18) |
| Coupon | **CDS_COUPON** (new ProductJob, before SWAP_VM) | 00:45 ET on each coupon date | a copy of `swaps_cds.h_coupon` / `coupon_keys` for KIND `CDS`, idem `cdscpn:{cid}:{date}`; the last coupon matures the contract |
| Variation margin | SWAP_VM | 17:05 ET, posts once that date's inputs exist | the module's `mark(valuation_date=d)`; the CLOSE_PENDING pacing of `vm_keys` covers CDS (hook H1-9) |
| Inputs of a VM date | the B3 refreshers | 16:45 ET and on demand | realised vol of the session and the split events, from Yahoo daily closes; both persisted (3.2) |
| Termination | `otc.terminate` | owner | the module's `terminate` |
| Credit event | `cds.credit_event` | owner only (actor `ui`; refused for lifecycle / matcher / worker) | 2.4 |
| Maturity | the last CDS_COUPON | maturity rolled | `swaps.close_contract(MATURED)` |

### 2.3 What each booking posts (worked numbers in Appendix C)

1. **IM.** The funding check covers the IM plus a payable initial payment together (`swaps.funding_preview`, as `swaps_cds._funding`); a
   shortfall is 409 `INSUFFICIENT_FUNDS` and writes nothing. Then `swaps.fund_commitment` funds the IM and `OTC_IM` posts Dr 1330 / Cr 1010,
   idem `im:{cid}:open`. The initial payment is funded when its settlement row settles.
2. **Initial payment entry `CDS_UPFRONT`.** Idem `otc:{cid}:upfront`, `ref_type` otc.
   - The upfront: Dr 1335 if this holder pays it, Cr 2345 if it receives it (the contract's cost basis, released at the end).
   - The rebate: Cr 2340 (buyer) or Dr 1225 (seller).
   - The net: Dr 1200 or Cr 2100.
   - A settlement row: leg `UPFRONT`, due T+3 business days, `ref_type` otc, `ref_id` the contract.
3. **The contract row.** `kind` CDS, `margin_mode` from the schedule, `im_c`, `mark_c` = the mark at mid, `inputs_json` = the deal's
   valuation inputs. `terms_json` holds exactly these keys; the blotter, risk, display and timeline code read them by these names:
   - who: `kind` "CDS", `cik`, `ticker`, `entity_name`, `underlying_id` (the reference equity's instrument id), `seniority` "SENIOR_UNSECURED",
     `doc_clause` "XR14";
   - what: `tenor_years`, `side`, `receiver`, `notional_c`, `coupon_bp`, `recovery` "0.40", `trade_date`, `step_in_date`, `accrual_start`,
     `maturity_date`, `roll_date`, `fixed_basis` "ACT/360", `fixed_periods`;
   - the deal: `model` {spread_bp, raw_bp, floor_bp, floor_as_of, bucket, quality, method, inputs, label}, `level_kind` ("NEGOTIATED" |
     "MODEL"), `basis_bp`, `mid_spread_bp`, `tier`, `coupon_rule` ("owner" | "default from the MODEL bucket"), `quoted` {rfq_id,
     dealt_spread_bp, coupon_bp, maturity_date, tier},
     `half_spread_bp`, `otc_spread_mult`, `deal_spread_bp`, `upfront_c`, `upfront_buyer_c`, `upfront_pct`, `price`, `upfront_gl`;
   - the cash: `rebate_days`, `rebate_c`, `initial_payment_c`, `settle_date`, `settle_id`, `im_pct`;
   - day-one figures: `mark0_c`, `cs01_0_c`, `jtd_0_c`, `rpv01_0`, `margin_mode`, `dealt_ts`, `model_version` "CG2002-AAA-FLOOR-1".
4. **The SWAP instrument row.** `instrument_id` = `symbol` = cid; `underlying_id` = the reference equity, as TRS does.
   Spec: `{contract_id, otc_kind: "CDS", broker, cik, ticker, entity_name, tenor_years, side, receiver, maturity_date, coupon_bp}`.
5. **`accrual_state`.** The premium leg seeded at the accrual start with the rebate as its cumulative. The other leg kind is marked accrued
   through the last night (exactly as `swaps_cds._book`).
6. **Event.** `OTC_BOOKED`, kind CDS.

### 2.4 The single-name credit event

`cds.credit_event {contract_id, event_type: BANKRUPTCY | FAILURE_TO_PAY, recovery_pct (0-100, at most 4 decimals, default shown 40), event_date}`,
route `POST /api/otc/cds/contracts/{id}/credit-event`.

It is entered by the owner and never fires by itself. Its label is the CDX one: "simulated credit event - recovery entered by the owner; no ISDA
Determinations Committee ruling or auction in the simulator".

Refusals:
- Not OPEN: 409 `NOT_OPEN`.
- Not a CDS contract: 400 `KIND`.
- An event date in the future, before the trade date, or outside the contract's coupon periods: 400 `TERMS`.
- An event date in a period whose coupon is already paid: 409 `EVENT_PERIOD_SETTLED` (as CDX).

Steps, in ONE command:
1. **Catch up.** Accrue the premium through the event date (`_catch_up` as CDX).
2. **Late nights.** Nights after the event date that were already posted are reversed. Their exact amount is **summed from the posted
   accrual lines** of this contract (idem `accr:{swappay|swaprcv}:{cid}:{night}`, night > event date), never re-derived by formula, so GL
   2340 / 1225 of the contract reaches exactly 0.
3. **Amounts.**
   - accrued = the contract's 2340 (buyer) or 1225 (seller) balance minus the late nights;
   - protection = N x (1 - recovery), in cents;
   - net = protection - accrued (buyer) or accrued - protection (seller).
4. **Entry `CDS_CREDIT_EVENT`.** Idem `otc:{cid}:credit`, `ref_type` otc.
   - Buyer: Dr 2340 (accrued + late), Cr 4453 (late), Cr 4450 (protection), and Dr 1200 (net) or Cr 2100.
   - Seller: the mirror.
   - A settlement row: leg `CREDIT EVENT`, due T+1 business day of the event date (as CDX).
5. **Close.** `swaps.close_contract(ctx, services, row, "TERMINATED", 0, swaps._reference_mark_c(conn, cid), f"otc:{cid}:credit-close",
   "CDS_CREDIT_CLOSE", memo, False, True)`. It unwinds the VM, releases the whole cost basis (1335 / 2345) into 4450, releases the IM, sets
   TERMINATED, deletes `accrual_state` and expires the SWAP row.
   - `settle_accrued=False`, because the accrued was settled in step 4.
   - `overdraft=True`: a VM received that must be returned settles like a maturity obligation, with CSA netting. An event the owner entered is
     never refused for funds.
6. **Record.** A row in the module's table `cds_credit_events` and the event `CDS_CREDIT_EVENT`. Its payload carries the keys that
   `blotters._credit_event_row` reads from a CDX event (`reference_entity, event_date, recovery_pct, defaulted_c (= N), protection_c, accrued_c,
   net_c, settle_date, settle_id, notional_before_c (= N), notional_after_c (= 0), label`), plus `event_type`, `event_words` and
   `contract_closed: true`.
   The blotter and timeline hooks render it. No `OTC_TERMINATED` event is written (one Completed-Trades row: "credit event").

---

## 3. The spread model (labelled MODEL with its inputs)

### 3.1 CreditGrades, as the module computes it

Per the technical document, sections 2.1-2.2. Evaluated in float64 with `math.erfc`; Phi(x) = 0.5 x erfc(-x / sqrt 2). The research script used
exactly these formulas.

**Inputs**

| Symbol | Meaning | Value or source |
|---|---|---|
| S | share price | live delayed mark (intraday) or the recorded official close of the valuation session (VM) |
| sigma_S | equity volatility | 1000-day realised (3.2) |
| D | debt per share | debt_c / 100 / shares, shares adjusted for splits after their date (3.2) |
| L-bar | mean global recovery (the barrier level as a fraction of D) | 0.5 |
| lambda | standard deviation of ln L (barrier uncertainty) | 0.3 |
| R | the CDS recovery | 0.40 |
| r | the continuous risk-free rate | -ln DF(T) / T, from the swaps' Treasury-bootstrapped curve (`swaps.dfs_for`, log-linear `swaps_cds.df_function`) |
| T | years to maturity | (maturity - valuation date).days / 365 |

**The model**
1. LD = L-bar x D
2. sigma = sigma_S x S / (S + LD) (the asset volatility)
3. d = (S + LD) / LD x e^(lambda^2)
4. A_t = sqrt(sigma^2 t + lambda^2)
5. P(t) = Phi(-A_t/2 + ln d / A_t) - d x Phi(-A_t/2 - ln d / A_t) (the survival probability)
6. xi = lambda^2 / sigma^2; z = sqrt(1/4 + 2 r / sigma^2)
7. G(u) = d^(z+1/2) Phi(-ln d / (sigma sqrt u) - z sigma sqrt u) + d^(-z+1/2) Phi(-ln d / (sigma sqrt u) + z sigma sqrt u)
8. H = e^(r xi) x (G(T + xi) - G(xi))
9. c* = r (1 - R) x (1 - P(0) + H) / (P(0) - P(T) e^(-rT) - H) (the CDS par spread, continuous premium)
10. raw_bp = 10^4 x c*, quantized to 0.01 bp with ROUND_HALF_EVEN from `Decimal(repr(value))`

**The quadrature path (distressed names, and any failure of the closed form).** Python's `math.exp` and float `**` raise `OverflowError`
instead of returning infinity, and near the barrier the closed form cancels. At S 0.01, sigma_S 2.0, D 40 it raises (e^(r xi) with xi about
9.0e4); at S 0.05, sigma_S 1.5, D 40 it gives 4,989.37 bp against 4,999.42 bp by quadrature (T 5.148, r 4.76 %). So:
- When P(0) < 0.95, or when steps 6-9 raise `OverflowError`, `ZeroDivisionError` or `ValueError`, or give a non-finite value or a
  denominator <= 0, step 9 is replaced by the integral form of the same par spread:
  c* = (1 - R) x (1 - P(T) e^(-rT) - r I) / I, with I = the integral from 0 to T of e^(-rs) P(s) ds,
  by the composite Simpson rule with 2,000 equal steps (deterministic). The closed form's denominator is exactly r I, so both agree where
  both work: to 12 significant digits on V1, V3 and V5 (Appendix A).
- `method` says which was used ("closed form" or "quadrature, Simpson 2,000 steps"); the quadrature reports I instead of xi, z, G and H.

**Guards** (each a documented outcome, never a crash)
- S <= 0, D <= 0 or sigma_S <= 0: no model (codes in 3.6).
- ln d / A_T >= 8: raw = 0.00 exactly. The distance to the barrier is over 8 standard deviations, and this avoids the overflow of d^(z+1/2).
- The quadrature fails too (an exception, a non-finite value, or I <= 0): `MODEL_DOMAIN`. `cg_spread` catches `OverflowError`,
  `ZeroDivisionError` and `ValueError` itself and never lets one out. No model value; a live mark keeps its last value labelled stale; a
  preview is refused.
- P(0) < 0.95 or raw_bp > 2,000: **distressed**. The spread is still computed (by quadrature when P(0) < 0.95) and labelled; a new deal
  needs a negotiated mid (3.5).

All intermediates (LD, sigma, d, A_T, P(0), P(T), then xi, z, G(xi), G(T + xi) and H for the closed form or I for the quadrature, c*) are
returned for the inputs panel.

**Test vectors with every intermediate:** Appendix A (V1-V7, to 1e-9 relative).

### 3.2 The inputs: sources, dates and recipes

**Share price S**
- Live: `services.marks.mark(instrument_id)`, quality live, delayed or close, quote time within its session's rules.
- VM date d: the recorded CLOSE of R, the last trading day of the equity's session group on or before d:
  - via `swaps_trs.close_on` / `_vm_close` (CLOSE_PENDING, or CAUGHT_UP when a later session's price exists);
  - the contract's reference equity is pinned and its closes recorded through `otc_reference_ids` (hook H2-1).

**Equity volatility sigma_S**
- Daily closes c_0..c_n from Yahoo through the session R (split-adjusted closes).
- Live fetch: `services.providers.yahoo._chart(symbol, "range=5y&interval=1d&events=div%2Csplits")`, then `parsers.parse_daily` and
  `parsers.parse_events`. Read-only use of the provider's internal call: its limiter (at most 2 requests a second), breaker and health apply.
  The public `daily()` caps at 3 months, which is not enough.
- Offline (FakeProvider): `providers.yahoo.daily(symbol, days=1001)` and `providers.yahoo.events(symbol)`. The rule: use `_chart` only when
  `services.providers.offline` is not True AND `providers.yahoo` has `_chart`; otherwise `daily` / `events`. A test runtime's
  `types.SimpleNamespace(yahoo=fp)` has no `offline` attribute and no `_chart`, so it takes the offline path.
- Take the last 1,001 closes, i.e. 1,000 log returns r_i = ln(c_i / c_(i-1)).
- sigma_S = sample standard deviation (n - 1) x sqrt(252), quantized to 0.0001.
- At least 250 returns are needed, otherwise `SHORT_HISTORY` "fewer than 250 daily closes (listed since <date>)".
- Between 250 and 1,000 returns the window used is said in the label.
- Stored per (ticker, R) in `cds_vol`, so a VM of a past date uses that date's volatility.
- Vector: alternating +1 % / -1 % closes give a daily sd 0.0105412769 and sigma 0.1673 (Appendix A).

**Debt** (B2, from SEC XBRL, us-gaap facts with no dimension)
- **Which balance sheet.** `end` = the latest `end` among the facts of the debt-recipe tags below (R1-R6), shown with its age. Every
  component comes from facts with that same `end`; older tags are ignored. This is why JPM's 2014 `LongTermDebt` is never used. A filing's
  other balance-sheet facts (`Assets`, `LiabilitiesAndStockholdersEquity`) never set `end`. Oracle shows why: its 2026-08-31 10-Q has no
  R1-R5 tag, only `LongTermNotesAndLoans` 117,712,000,000 and `NotesPayableCurrent` 7,625,000,000, so an `end` set by its other facts would
  make it `NO_DEBT`. With R6 it is offered at 125,337,000,000 (2026-08-31); its 10-K of 2026-05-31 had R1 129,541,000,000.
- **Two rows with the same `end`** (an amended filing, or a later filing repeating the balance sheet as its comparative column): the one
  with the latest `filed` when known (companyfacts), else the latest `known_from` (frames), then the higher `accn`.
- **Parts and wholes (no double count).** Filers often report a total and one of its parts at the same `end`. A part counts only when it
  is larger than its whole (then it cannot be inside it); otherwise the whole is used alone. The pairs: (`ShortTermBorrowings`,
  `CommercialPaper`) and (`NotesPayable`, `SeniorNotes` / `ConvertibleNotesPayable`). Measured on all CY2026Q2I filers: 17 report
  `ShortTermBorrowings` and `CommercialPaper` at the same `end`, 14 of them with CP <= STB (PEP STB 10,602,000,000 with CP 6,100,000,000;
  ICE both 1,218,000,000); 18 report `NotesPayable` with `ConvertibleNotesPayable`, 10 with the convertible <= the total. Lines of credit
  are not notes and always add (`LinesOfCreditCurrent` exceeds `NotesPayable` in 6 of the 10 filers that report both).
- **Order.** R1-R4 apply when their first tag is present and above 0; otherwise R5a, R5b and R6 are tried in that order and the first with
  debt above 0 wins. Counts: national-exchange CIKs of CY2026Q2I with debt above 0 by that recipe (my run of exactly this rule on the
  research frames and the critic's R6 frames, `<scratchpad>\cds-design\rev\coverage_exch.py`).

| # | Recipe ("STB + CP" = `ShortTermBorrowings` + `CommercialPaper` under the parts rule) | Count |
|---|---|---|
| R1 | `DebtLongtermAndShorttermCombinedAmount` | 201 |
| R2 | `LongTermDebt` + STB + CP | 1,259 |
| R3 | `LongTermDebtNoncurrent` + (`DebtCurrent` if present, else `LongTermDebtCurrent` + STB + CP) | 503 |
| R4a | `LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities` + STB + CP | 89 |
| R4b | `LongTermDebtAndCapitalLeaseObligations` + (`LongTermDebtAndCapitalLeaseObligationsCurrent` if present, else `LongTermDebtCurrent`, else `DebtCurrent`) + STB + CP | 135 |
| R5a | `SecuredDebt` + `UnsecuredDebt` (REITs and others) | 70 |
| R5b | `NotesPayable` (with `SeniorNotes` / `ConvertibleNotesPayable` under the parts rule; both summed when `NotesPayable` is absent) + `LinesOfCreditCurrent` + `LongTermLineOfCredit` + `OtherLongTermDebtNoncurrent` | 213 |
| R6 | The larger of `NotesAndLoansPayable`, `LoansPayable` (totals that include the current part) and NC + CUR. NC = the largest of `LongTermNotesAndLoans`, `LongTermNotesPayable`, `SeniorLongTermNotes`, `LongTermLoansPayable`, `OtherLongTermDebt` and `SecuredLongTermDebt` + `UnsecuredLongTermDebt` (filers use them as alternative names for one total: Oracle reports `LongTermNotesAndLoans` = `LongTermNotesPayable` = 122,342,000,000 at 2026-05-31). CUR = `NotesPayableCurrent`, else `DebtCurrent`, else `LongTermDebtCurrent` + STB + CP | 281 |

- Why R4b and R6 changed: Verizon reports `LongTermDebtAndCapitalLeaseObligations` 143,448,000,000 with its current part as
  `LongTermDebtCurrent` 21,783,000,000 (2026-06-30); the first draft dropped the current part (13 % low; 165,231,000,000 now). About 40
  national-exchange filers with at least USD 100M of borrowings report only R6 tags (KBH `NotesAndLoansPayable` 2,109,145,000 at
  2026-08-31, CME, CDNS, VRSN, ACGL, ILMN); the first draft refused them as `NO_DEBT`. In all, R6 brings in 64 offered names of at least
  USD 100M on CY2026Q2I (1,599 -> 1,663, 4.1); the other recipes' counts are unchanged.
- A sum above `Liabilities` of the same `end` is rejected: `DEBT_GT_LIABILITIES`, 9 names.
- Financial firms (3.5): only the borrowings recipes above. `Deposits`, repos and policy liabilities are never debt.
- Deviation from the technical document's Appendix B, disclosed: the document adds 50 % of other liabilities (pensions, deferred taxes,
  provisions) and 0 % of accounts payable. The first draft left it out on the strength of the 3.4 comparison, which can no longer carry
  that weight. The variant moves levels a lot (Appendix B, model bp debt-only -> variant: VZ 59.89 -> 78.72, KBH 124.15 -> 149.73, ORCL
  112.54 -> 149.08, KSS 547.33 -> 812.29). 1.0.7 ships debt-only; B3 reports both on a fixed list and the owner chooses (Q1). Nothing is
  tuned in the build.
- The UI shows the parts, for example "LongTermDebt 82,300,000,000 + CommercialPaper 1,997,000,000 (10-Q, balance sheet 2026-06-27)", and
  names a part the parts rule left out ("CommercialPaper 6,100,000,000 taken as part of ShortTermBorrowings 10,602,000,000").

**Shares**
- Chain: `dei:EntityCommonStockSharesOutstanding` (the cover page, the latest) -> `us-gaap:CommonStockSharesOutstanding` (the same `end`) ->
  `us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding` (the latest quarter, labelled "diluted weighted average"). Otherwise `NO_SHARES`
  "share count not reported (multi-class issuer?)".
- Splits after the share count's date (Yahoo events) multiply the count by numerator / denominator. This stops a 10:1 split from making D ten
  times too large.
- The split events are persisted (`cds_splits`, written in the same `cds.vol_record` as the volatility rows, 6.2), never only in memory.
  A VM of date d uses the splits dated after the share count's date and on or before R. A vol row of R exists only after a fetch that saw
  R's events (`events_through`); without it the VM answers CLOSE_PENDING and queues the fetch, so a VM after a restart never runs on
  unadjusted shares. Live, the list is the one fetched in the current session (the name's first preview, or the 09:45 ET pass of
  `cds_inputs` over the open contracts' equities), so a split is known on its ex-date.

**Point in time**
- Each fundamentals row carries `known_from`: the NY date the refresher first saw that (cik, end, accn).
- `fundamentals_on(conn, cik, d)` returns the row with the latest `end` among rows known on or before d (two rows of one `end`: the
  tie-break of the debt rules). A VM of date d uses what the app knew on d.
- Frames carry no filed date; companyfacts rows also store `filed` and `form`.

**Rate r and the curve:** `swaps.dfs_for(services, n, on=R)` for a VM date (the curve of that date, C12; a missing one gives CAUGHT_UP or
CLOSE_PENDING as the IRS does); the latest curve when live.

**Floor and ladder**
- `rates.as_of_le(series, d)` for the seven `rates.OAS_SERIES` buckets.
- Live: the latest observation, at most 3 business days old (`FLOOR_STALE` beyond).
- VM: the observation OF R exactly. Not yet published: CLOSE_PENDING (FRED posts the next morning). Skipped while a later one exists:
  CAUGHT_UP. This is the CDX proxy rule.

### 3.3 Calibration: the AAA floor ("rating-like bucket" ladder)

**The rule:** `model spread = floor + raw`.
- floor = the ICE BofA AAA US Corporate OAS (FRED `BAMLC0A1CAAA`, percent x 100, in bp) of the valuation date.
- raw = the CreditGrades spread of 3.1.

**Rating-like bucket of a spread:** the FRED bucket (AAA, AA, A, BBB, BB, B, CCC, same date) whose OAS is nearest in |ln(spread / OAS)|,
computed with Decimal ln to 12 digits. A tie goes to the wider bucket.
- Shown as "BBB-like (model spread bucket - not a credit rating)".
- The bucket of the **mid** (model plus a negotiated basis) sets the IM tier and the dealer half-spread: a name the owner marks wide is
  riskier and costs more to margin and to deal. The default coupon follows the bucket of the MODEL spread before the basis (2.1), because
  the coupon is the name's standard contract, not a risk measure.

**Why this calibration** (stated in the UI's model note and in CONVENTIONS `CDS_MODEL`)
- Structural models leave out the part of every corporate spread that is not expected default loss (liquidity and risk premium); this is the
  "credit spread puzzle". The AAA OAS is the market price of that part for the safest corporates. Adding it floors every listed company at
  the AAA level, so no name deals near 0 bp; raw CreditGrades gives MSFT 0.00 bp.
- Scaling raw spreads per rating bucket was considered and rejected. Without a free per-company rating, bucket membership can only come from
  the model itself, so a per-bucket scale factor is circular: it comes out at about 1.
- Equivalently, the floor rule places each name on the FRED ladder where its default component (raw) equals the bucket's spread over AAA:
  raw = OAS_bucket - OAS_AAA gives exactly OAS_bucket.

### 3.4 Evidence and its limits (design time)

**The first draft's comparison: a record, not evidence.**
- It compared the model with 5Y CDS levels from 7 DTCC files the research downloaded by script before the DTCC terms decision (1). Those
  files are not reused: no builder opens them, and the app never stores, refreshes or reproduces them. Per-name traded levels from them
  are not repeated in this plan.
- Its inputs were weaker than the first draft said:
  - price, `total_debt` and shares came from the unverified `finsim\finsim\data\universe.json` snapshot of 2026-09-18, not from the 3.2
    recipe, and some are badly wrong: PFE `total_debt` 4.0B (D 0.70 a share; Pfizer carries about USD 60B), F 291M, and ORCL 0, so the
    owner's own target was silently dropped by `calib_eval.py`;
  - 11 of the 55 names (AIG, AMD, BA, BAC, GS, INTC, LMT, MET, MSFT, NEE, VZ) used the Cboe 1-year at-the-money implied volatility, not
    the 1000-day realised volatility of 3.2.
- What it showed, in aggregate:

| Calibration | Median abs. error (bp) | Mean abs. error (bp) | Median abs. log error | Mean log bias |
|---|---|---|---|---|
| Raw CreditGrades | 39.8 | 47.1 | 2.893 (about 18x) | -3.755 |
| AAA floor + raw (shipped) | 16.8 | 25.2 | 0.337 | +0.083 |
| Constant 42 bp (the floor alone) | 22.0 | 29.2 | 0.499 | -0.178 |
| A floor + raw | 39.4 | 40.2 | 0.562 | +0.550 |
| Bucket OAS of (AAA + raw), a step function | 16.5 | 25.3 | 0.336 | +0.065 |
| AAA x (sigma / 0.35)^0.5 + raw | 16.1 | 24.9 | 0.339 | +0.017 |
| Raising L-bar to 0.6-0.9 | worse in every variant | | | |

- Read it as weak. 32 of the 55 names had a raw spread under 5 bp, so they sat at or near the 42 bp floor; the floor alone came within
  5.2 bp of the model's median error; only 27 of 55 were within +/-35 %. For investment-grade names the structural part adds little: the
  "credit spread puzzle" of 3.3.
- Its design choices rest on their own reasons, not on these numbers: raw CreditGrades is far too tight, so a floor is needed; a step
  function would make spurious VM at bucket boundaries; a volatility-scaled floor adds a fitted parameter.

**On today's production inputs** (permitted sources only; Appendix B) the model misses names whose CDS trades by about 2 times in both
directions (the critic's spot checks: some too tight, some too wide):
- **ORCL: 112.54 bp** (debt 125,337,000,000 by R6 at 2026-08-31) or 115.74 bp (the 10-K's R1 129,541,000,000 at 2026-05-31), BBB-like.
  Public news put Oracle's 5-year CDS at 295-315 bp on 2026-10-19 (CNBC, as reported by Wealth Professional on 2026-10-20; Appendix E):
  about 2.5-2.8 times the model. **The model is not validated for ORCL**, the one name the owner has asked to trade.
- Known blind spots (first draft, qualitatively): too tight for the large technology names whose CDS trade on heavy 2026 bond issuance
  (ORCL among them); too wide for some defence and railroad names; too tight for insurers.

**What the build does about it**
- Every spread stays labelled MODEL with its inputs; the model note says it is not validated against traded levels.
- The dealing-level guard (3.5): no new deal on the bare model without the owner's explicit confirmation, per PB account and name.
- B3 reports the shipped model on fresh production inputs for a fixed list of 20 names, beside the CreditGrades Appendix B debt variant
  (B3 task 7). It reports and never tunes; the owner decides under Q1.

### 3.5 The dealing level (negotiated mid or MODEL confirmation), the dealer half-spread, and the special cases

**The dealing level: your negotiated mid, or your confirmation of the MODEL** (one `cds_basis` row per PB account and reference entity)
- **The guard.** A new deal on a name (preview, quote request, booking) needs a dealing level for that account and name. Without one the
  preview still shows every number but is not bookable, with the blocker `DEALING_LEVEL_UNCONFIRMED`; `otc.rfq.request` and `otc.book`
  answer 409 with the same words, for example: "No market level is known for Oracle Corp (ORCL): its spread is a MODEL (112.54 bp,
  BBB-like) and has not been validated against traded levels. Set your negotiated mid, or confirm dealing at the MODEL, before asking for a
  quote." Marks, VM, termination and credit events never need it.
- `cds.basis {account_id, cik, negotiated_mid_bp}` stores `level_kind` "NEGOTIATED" and `basis_bp = negotiated - model mid now`, with the
  model mid, its inputs and the time.
- `cds.basis {account_id, cik, deal_at_model: true}` stores the owner's confirmation: `level_kind` "MODEL", basis 0. Its dialog says "deal
  at the MODEL - no market level known" and shows the model's inputs and its known limits (3.4) before he confirms.
- From then on, mid = model + basis for every preview, booking, mark, VM and termination of that name in that account. The marks keep moving
  with the share price, volatility, debt and floor.
- `{..., clear: true}` removes the row; the guard applies again.
- A model mid that cannot be computed now gives 409 `INPUTS_PENDING`: preview the name first.
- A distressed name needs a negotiated mid; a MODEL confirmation is not enough (`DISTRESSED_NEEDS_NEGOTIATED_MID`, Q4).
- **The change is shown before it is saved.** A new or cleared level re-marks every open contract on that name in that account at once (one
  name has one mark per account). Before Save, the dialog lists each open contract with its value now and its value at the new mid (full
  revaluation, not CS01 x the change), the total, and for new deals the bucket, tier, IM % and half-spread before and after; the default
  coupon does not move (2.1). For USD 25M of ORCL protection, CS01 is about USD 10K a bp, so moving the mid by 200 bp moves the mark by
  about USD 2M. Route `GET /api/otc/cds/basis/effect` (6.5); the command recomputes the same at its own instant.
- Each change is one `SETTINGS_CHANGED` event with before, after and that effect (`{contracts: [{contract_id, value_before_c,
  value_after_c, effect_c}], effect_total_c}`). Past postings are never restated. A VM computed later uses the level current then: the CDX
  semantics, stated.
- Shown everywhere the mid is shown: "mid 84.00 bp = model 65.47 bp + your negotiated basis +18.53 bp (set 2026-10-29 10:14 ET)", or "mid
  65.47 bp = MODEL (you confirmed dealing at the model on 2026-10-29 10:14 ET)".

**Dealer half-spread** = the tier's `half_spread_bp` x the PB schedule's `otc_spread_mult`, against the owner:
- a buyer deals at mid + hs, a seller at mid - hs;
- a termination is at mid - hs (buyer) or mid + hs (seller).

**Special cases** (each one an exact rule with words on screen)

| Case | How it is detected | Rule |
|---|---|---|
| Bank, insurer or finance company | SIC 6000-6499: banks, credit institutions, broker-dealers, insurers (from `submissions`, fetched on demand). Before that, a frames flag of the same `end`, labelled "from filing tags; SIC not checked yet": `Deposits` > 0 (banks); `LiabilityForFuturePolicyBenefits`, `LiabilityForClaimsAndClaimsAdjustmentExpense`, `LiabilityForUnpaidClaimsAndClaimsAdjustmentExpenseNet`, `PolicyholderFunds` or `SeparateAccountsLiability` > 0 (insurers: 129 listed filers in CY2026Q2I against 34 with the first tag alone; TRV, ALL, PGR, ACGL, WRB, PRU, MKL and AFG were missed before) | **Offered, LIMITED.** Debt = borrowings only (R1-R6; never deposits, repos or policy liabilities). Label: "financial firm: CreditGrades was calibrated for industrial firms (its 2002 document excludes banks and finance firms); deposits, repos and policy liabilities are left out of debt - treat this spread with care". On the first draft's comparison (3.4) banks came out close and insurers too tight (Q3) |
| REIT | SIC 6798, or `RealEstateInvestmentPropertyNet` >= 50 % of `Assets` | Offered (normal leverage). Debt recipe R5a / R5b allowed; labelled "REIT" |
| Foreign private issuer | The latest periodic form is 20-F / 40-F (submissions), or the instrument master kind is ADR | `FOREIGN_FILER`: "not offered: foreign private issuer (ADR ratios and IFRS reports are not modelled in this release)" (Q7) |
| IFRS-only filer | No us-gaap debt facts but ifrs-full facts | `FOREIGN_FILER`, same words |
| Debt below USD 100M | the debt recipe | `BELOW_MIN_DEBT`: "not offered: reported debt USD 30,000,000 is below the USD 100,000,000 minimum (assumed)" (Q8) |
| No debt tag / zero debt | no recipe applies, or the sum is 0 | `NO_DEBT`: "not offered: no debt reported in the latest 10-Q / 10-K XBRL (or it uses the company's own tags)". Known: Ford, Berkshire Hathaway |
| Shares missing | the share chain is exhausted | `NO_SHARES` |
| Balance sheet older than 15 months (465 days) | `end` | `STALE_FUNDAMENTALS`: "balance sheet 2025-06-30 is 16 months old" |
| Balance sheet 6-15 months old | `end` | Offered, with an amber age note |
| Under 250 daily closes | Yahoo | `SHORT_HISTORY` |
| No share price, or a stale one | marks | `NO_PRICE` / `PRICE_STALE` (a mark older than its session's last close) |
| Distressed | P(0) < 0.95 or raw > 2,000 bp | Marks and VM use the model value (by quadrature when P(0) < 0.95, 3.1), labelled "distressed - outside the model's calibrated range". **A new deal needs a negotiated mid** (`DISTRESSED_NEEDS_NEGOTIATED_MID`) (Q4) |
| No dealing level | no `cds_basis` row for the account and name | Preview shown with every number, not bookable: `DEALING_LEVEL_UNCONFIRMED` (above); quote request and booking 409 |
| FRED floor missing or stale | rates | `FLOOR_UNAVAILABLE` / `FLOOR_STALE`: no CDS price; marks keep their last value labelled stale |

**The UI never shows a defaulted number.** Every refusal is words first, with the code only in the hover (the 1.0.4 rule BW-04).

### 3.6 Model result codes (B3 contract, used by B1 and B4)

| Code | Meaning | HTTP when it is a preview |
|---|---|---|
| `INPUTS_PENDING` | a fetch is queued; `retry_ms` | 202 on the preview route; 409 inside a command |
| `ENTITY_UNKNOWN` | no such CIK or ticker | 404 |
| `NOT_COVERED` | the entity's status reason (`BELOW_MIN_DEBT`, `NO_DEBT`, `NO_SHARES`, `STALE_FUNDAMENTALS`, `FOREIGN_FILER`, `DEBT_GT_LIABILITIES`) | 200 refused |
| `NO_PRICE`, `PRICE_STALE`, `NO_VOL`, `SHORT_HISTORY`, `FLOOR_UNAVAILABLE`, `FLOOR_STALE`, `NO_CURVE`, `MODEL_DOMAIN` | as named | 200 refused |
| `DISTRESSED_NEEDS_NEGOTIATED_MID` | raised by B1 when a distressed name has no negotiated mid | 200 refused |
| `DEALING_LEVEL_UNCONFIRMED` | raised by B1 when the account has no dealing level for the name (3.5) | 200 with every number and `bookable` false; 409 from `otc.rfq.request` and `otc.book` |
| (VM only) `vm_outcome` `CLOSE_PENDING` / `CAUGHT_UP` with `close_date`, `quiet`, `quiet_until`, `note` | an input of that date is not out yet / never will be | - |

---

## 4. Coverage, listing and search, refresh, caching, stale rules

### 4.1 Which names get a CDS, and how many

**The universe.** SEC `company_tickers_exchange.json`: CIK, name, ticker, exchange.
- Keep the allowlist {NYSE, Nasdaq, CBOE, TXSE}, compared case-insensitively. The file of 2026-10-28 (about 18:52 ET) holds exactly these
  values: Nasdaq 4,357 rows, NYSE 3,269, OTC 2,577, null 171, CBOE 44, TXSE 12 (the Texas Stock Exchange is new; there is no "NYSE
  American" string). OTC and null are dropped. Any other string is dropped and logged once per refresh with its row count, so a new
  exchange name is seen, never silently kept or lost.
- One primary ticker per CIK:
  1. the ticker that is in the instrument master;
  2. else the first common-stock-looking ticker in the SEC file order (no `-W`, `-U`, `-P` suffix, no preferred / warrant / unit pattern);
  3. the others go in `tickers_json`.
- The research's crude title match picked ETN and preferred tickers: JPMorgan as AMJB, Comcast as CCZ, International Paper as INPAP. This rule
  prevents that.

**Offered** = listed, us-gaap 10-K / 10-Q filer, debt >= USD 100M at a balance sheet 15 months old or newer, and shares present.

**Expected count, measured on CY2026Q2I frames (national exchanges only: the allowlist above)**

| Step | First-draft recipe | Recipe of 3.2 (R6, parts rule, R4b fallback) |
|---|---|---|
| CIKs with a debt-recipe tag | 2,673 | 2,857 |
| Debt > 0, not rejected, with shares | 2,161 | 2,388 |
| Debt >= USD 100M | 1,599 | 1,663 |
| Debt >= USD 1B | 939 | 954 |

- The first draft's 2,948 / 1,629 counted OTC names: its script read `company_tickers.json`, which has no exchange field.
- About 1,650-1,800 are expected once B2 adds the second and earlier quarters (annual-only filers) and the companyfacts freshness path.
- On demand, a few per cent more say unavailable: no price, under 250 closes, foreign filers found by `submissions`, multi-class share counts.
- Without the USD 100M minimum (Q8) the count is about 2,400 (2,388 on CY2026Q2I alone).
- For scale: ICE Clear Credit lists 337 North American single names with a cleared CDS market. Everywhere else the contract is a bilateral
  contract on a MODEL spread, and the UI says so.

### 4.2 How names are listed and searched on the OTC tab

A sub-tab **Single-name CDS** on every PB account's OTC tab, after "Credit (CDX)" (B4).

**Header line**
- "MODEL - equity-implied CreditGrades spread plus the ICE BofA AAA OAS floor; not a dealer quote".
- The FRED ladder with its date: AAA 42 / AA 63 / A 73 / BBB 109 / BB 187 / B 322 / CCC 1,321 bp as of 2026-10-27.
- The universe count and refresh time: "1,7xx reference entities from SEC EDGAR, refreshed 06:30 ET".
- The PB's tier terms (IM % and half-spread per tier) with an Assumptions button.

**Search and browse**
- A search box (ticker or company name, typeahead after 1 character, 250 ms debounce).
- A filter: all | non-financial | financial | REIT | my contracts. Until a name's SIC is checked (on its first preview), its flag comes from
  its filing tags and says so: "financial (from filing tags; SIC not checked yet)", and a name with no flag counts as "non-financial so
  far". The filter's caption says "known so far".
- A browse-all table, paged 50 rows, sortable by ticker, name or debt.
- Columns:
  - ticker;
  - company (the SEC name);
  - debt (USD, abbreviated, exact in the hover);
  - balance sheet date (amber when 6-15 months old);
  - flags (financial / REIT, with their source: filing tags until the SIC is checked, then SIC);
  - "5Y model" (spread and bucket when computed in this session, else "on request");
  - status (offered, or the reason in words).
- Route `GET /api/otc/cds/entities` (B2).

**Selecting a row** opens the ticket. The first preview of a name fetches its inputs (Yahoo 5-year daily closes, the SEC submissions check,
companyfacts if a newer 10-Q exists) on the worker. The route answers 202 with `retry_ms`; the ticket says "fetching <name>'s SEC filings and
1,000 daily closes (first time today only)" and retries by itself (the V104R1-06 pattern).

### 4.3 Refresh cadence, caching and rate limits

| What | When | Size | Writes |
|---|---|---|---|
| `company_tickers_exchange.json` | weekly, Monday 06:30 ET, and at the first run | 1 request | changed entity rows only |
| XBRL frames: 37 debt / liability / flag tags (the first draft's 23: `DebtLongtermAndShorttermCombinedAmount`, `LongTermDebt`, `LongTermDebtNoncurrent`, `LongTermDebtCurrent`, `DebtCurrent`, `ShortTermBorrowings`, `CommercialPaper`, the three `LongTermDebtAndCapitalLeaseObligations*`, `NotesPayable`, `SecuredDebt`, `UnsecuredDebt`, `SeniorNotes`, `ConvertibleNotesPayable`, `OtherLongTermDebtNoncurrent`, `LinesOfCreditCurrent`, `LongTermLineOfCredit`, `Liabilities`, `Assets`, `Deposits`, `LiabilityForFuturePolicyBenefits`, `RealEstateInvestmentPropertyNet`; the 10 R6 tags `NotesAndLoansPayable`, `LongTermNotesAndLoans`, `LongTermNotesPayable`, `NotesPayableCurrent`, `LongTermLoansPayable`, `OtherLongTermDebt`, `SeniorLongTermNotes`, `SecuredLongTermDebt`, `UnsecuredLongTermDebt`, `LoansPayable`; the 4 insurer flag tags `LiabilityForClaimsAndClaimsAdjustmentExpense`, `LiabilityForUnpaidClaimsAndClaimsAdjustmentExpenseNet`, `PolicyholderFunds`, `SeparateAccountsLiability`; instant frames `CY{yyyy}Q{q}I`) and 3 share tags (`dei:EntityCommonStockSharesOutstanding` and `us-gaap:CommonStockSharesOutstanding`, instant; `us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding`, the quarterly duration frame `CY{yyyy}Q{q}`) | daily at 06:30 ET (refresher `cds_fundamentals`, `run_after_et="06:30"`; its first run is at the worker's first tick): the current and the previous calendar quarter; the first run (no universe yet) and Monday runs: the last 5 quarters, for a 15-month window | about 80 requests a day (40 tags x 2 quarters); about 201 on the first run and on Mondays (40 tags x 5 quarters + the tickers file), about 22 MB; 0.002-0.74 MB each (`Assets` is the largest) | new or changed (cik, end, accn) rows only, in chunks of at most 500 rows per light command, request id = content hash |
| Per name: `submissions` (SIC, latest periodic form) and, when a newer 10-Q / 10-K than the frames row exists, `companyfacts` | on a name's preview when its entity check (`checked_ts`) is missing or older than 7 days | 1-2 requests (companyfacts 1-10 MB; over the 16 MB `http` cap it falls back to frames, labelled) | one entity row and its fundamentals rows |
| Yahoo 5-year daily closes plus splits, and the realised vol rows | on demand (the first preview of a name in a session); 16:45 ET daily for every reference equity with an OPEN CDS contract (refresher `cds_eod`), back-filling any session missing since the contract's trade date; once per session after 09:45 ET for the same equities, for their split events only (`cds_inputs`) | 1 request a name | new `cds_vol` and `cds_splits` rows only |
| FRED ladder and floor | the existing FRED slot (every 6 h) | none new | none new |
| Live share price | the quote worker: the contract's equity pinned HELD (hook H2-1); while the ticket shows a name, `worker.subscribe([iid], 90, "WATCH")` | none new | none new |

**Rate limits**
- SEC: the module's own `quotes.http.RateLimiter(4)` (well under 10 a second), user agent from `ua_for` (GOV), health under provider name
  `sec` (`services.providers.health.record`; `allow("sec")` checked first).
- Yahoo: the provider's own limiter.
- **The CDS modules never call Cboe in 1.0.7.** The research's pitfall: `CboeChain` treats the 403 for an unoptionable symbol as a refused user
  agent.

**Caching**
- Fundamentals and volatility are persisted (`credit_fundamentals`, `cds_vol`).
- The live model result, the fetch queue and the "5Y model" column are in memory (process lifetime, never persisted). The commands table
  keeps every payload, so a cache must not live there.

**Offline (`--offline`, the tests).** The refreshers return at once without a network call (`services.providers.offline`). Tests inject a
getter (B2) or the FakeProvider (B3).

**First run of 1.0.7 on the owner's database.** The startup run of `cds_fundamentals` builds the universe: about 201 requests (40 tags x 5
quarters + the tickers file) and about 22 MB. At `RateLimiter(4)` the pacing alone is at least 50 s; with transfer and parsing it takes about
2-3 minutes on the worker. Until it ends, the desk says "loading the reference-entity list from SEC EDGAR (first run, about 2-3 minutes)".

### 4.4 Stale rules (one table, used by B2, B3 and B1)

| Input | Fresh | Labelled | Refuses dealing |
|---|---|---|---|
| Balance sheet `end` | 0-182 days | 183-465 days ("n months old", amber) | > 465 days |
| Share count date | 0-465 days | | > 465 days |
| Realised vol row | the last complete session; up to 3 business days old live | 1-3 business days | > 3 business days (`NO_VOL`, a refetch queued) |
| Share price | delayed or live in session; the last close out of session | as the quote's freshness | stale beyond its session rule (`PRICE_STALE`) |
| AAA floor and ladder | up to 3 business days | | > 3 business days |
| VM inputs of date d | the inputs OF d (close of R, vol of R and the split events its fetch saw, OAS of R, curve of R, fundamentals known on R) | | CLOSE_PENDING until published; CAUGHT_UP when they never will be |

---

## 5. Initial margin, risk, ledger, UI

### 5.1 IM schedule per PB (assumed, editable; by spread bucket)

**Tiers** (from the rating-like bucket of the **mid** when the dealer quotes; the quote fixes the tier, 2.1):

| Tier | Buckets |
|---|---|
| A_OR_BETTER | AAA, AA, A |
| BBB | BBB |
| BB | BB |
| B | B |
| CCC | CCC |

The tier does not set the default coupon: that follows the bucket of the MODEL spread before any negotiated basis (100 bp unless BB-like
or worse, 2.1), so an investment-grade name the owner marks wide keeps the 100 bp contract but margins and deals in the wider tier.

**Seeds by broker group** (the OTC_SEEDS grouping: GS, JPM, CITI, BOFA keep the lowest terms, as for the swaps)

| Tier | IM, GS / JPM / CITI / BOFA | IM, MS / BARC / UBS | IM, DB / BNP | Dealer half-spread (bp, all brokers; x `otc_spread_mult`) |
|---|---|---|---|---|
| A_OR_BETTER | 4.0 % | 5.0 % | 6.0 % | 2.0 |
| BBB | 5.0 % | 6.0 % | 7.5 % | 3.0 |
| BB | 8.0 % | 9.0 % | 10.0 % | 7.5 |
| B | 12.0 % | 13.5 % | 15.0 % | 15.0 |
| CCC | 25.0 % | 27.5 % | 30.0 % | 40.0 |

- **Rule:** IM = round to the cent (N x im_pct of the tier the quote fixed). Posted Dr 1330 / Cr 1010 and fixed for the contract's life (as CDX);
  released at termination, maturity or a credit event.
- **Reference points, for comparison only:**
  - the owner's CDX IM is 2 % (IG) and 6 % (HY), a diversified index;
  - the BCBS-IOSCO standardised schedule for uncleared credit derivatives is 2 % (0-2 years), 5 % (2-5 years) and 10 % (over 5 years) of
    notional (BIS d499 Appendix A, via the FINOS CDM summary) (Q5).
- **Editable** per PB account and tier with `cds.terms {account_id, tier, im_pct?, half_spread_bp?}` (light; one `SETTINGS_CHANGED` with
  before and after):
  - ranges: im_pct 0-0.99; half-spread 0-500 bp;
  - stored in the module's `cds_terms` table, seeded from the account's broker;
  - shown and edited in the CDS desk's Assumptions panel, like the CDX terms. Not in Settings > Prime brokers (Q12).

### 5.2 Risk

**CS01.** Full revaluation at mid + 1 bp (hazard re-solved), signed for the holder: a buyer gains when spreads widen. This is
`swaps_cds.cs01_from`.

**Jump-to-default (JTD), per contract:** JTD = sign x (1 - R) x N - MV.
- sign is +1 for a buyer and -1 for a seller; R is 40 %.
- MV is the contract's model value to the holder now (the position's `mv_c`).
- The accrued premium is already a liability or asset in NAV and settles at par, so it does not change NAV at default.
- A buyer at MV -78,913.97 on N 5,000,000.00 gains 3,078,913.97. The seller of the same contract loses 3,078,913.97.

**Per reference entity (all accounts in scope):** contracts, gross notional, net credit exposure, net JTD, JTD as % of NAV, and CS01.

**Concentration warning:** `CDS_JTD_CONCENTRATION` (WARN) when one entity's net JTD **loss** exceeds 10 % of NAV (Q9). Message: "<name> jump to
default would lose USD x (y % of NAV, above 10 %)".

**Scenarios** (full revaluation):
- single-name spreads -25, +25, +50 and +100 bp (the mid floored at 1 bp);
- "default of <the entity with the largest JTD loss>" = its JTD;
- **equity scenarios through the model.** V3 section 16 asks for "Oracle rallies 50%" and "Nasdaq rallies 30% and credit spreads tighten",
  and a share-price shock is exactly what moves this model's spread. The model is re-run with S x (1 + x), holding volatility, debt,
  floor, curve and the owner's basis, and each contract is revalued at its new mid (`cds_model.spread_at`, 6.4):
  - every reference equity -30 % and +30 % together;
  - the name with the largest JTD alone at -30 %, +30 % and +50 %;
  - per name, the same three shocks alone (`by_name` columns), so a fund-level stress can take any name's figure.
- A name whose model cannot be re-run shows "unavailable" in these rows, never 0.

**Where it shows**
- Risk: a "Single-name CDS" block next to "Credit derivatives (CDX)" (`RiskView.single_name_cds`, hook H2-9).
- Bucket "Credit derivatives (single-name CDS notional)": outside the headline gross like CDX, with `DEFINITIONS["cds"]` and `["jtd"]`.
- Portfolio footer "single-name CDS credit long / short".
- Derivatives page: the CS01 column, and the JTD in the drawer.

### 5.3 Ledger (no new GL; `ledger.py` unchanged)

| Posting | Lines | Journal kind / idem |
|---|---|---|
| IM | Dr 1330 / Cr 1010 | `OTC_IM` / `im:{cid}:open` |
| Upfront and rebate | Dr 1335 or Cr 2345 (cost basis); Cr 2340 (buyer) or Dr 1225 (seller) rebate; Dr 1200 or Cr 2100 net | `CDS_UPFRONT` / `otc:{cid}:upfront` |
| Premium night | buyer Dr 4453 / Cr 2340; seller Dr 1225 / Cr 4453 | `ACCRUAL` / `accr:{swappay\|swaprcv}:{cid}:{night}` (swaps) |
| Coupon | buyer Dr 2340 / Cr 1010 (`fund_csa`, overdraft rule); seller Dr 1010 / Cr 1225 | `SWAP_PAY` / `cdscpn:{cid}:{date}`; an `otc_resets` PAY row with `detail.kind` "CDS coupon" |
| VM | 1320 / 2330 (CTM) or 4450 (STM) | `VM` / `swapvm:{cid}:{date}` (swaps) |
| Termination | VM unwind, value, cost basis released to 4450, accrued settled, IM released | `OTC_TERMINATE` / `otc:{cid}:terminate` (swaps.close_contract) |
| Credit event | 2.4 step 4 | `CDS_CREDIT_EVENT` / `otc:{cid}:credit` |
| Credit-event close | 2.4 step 5 | `CDS_CREDIT_CLOSE` / `otc:{cid}:credit-close`, then `im:{cid}:release` |
| Maturity | swaps.close_contract(MATURED) | `OTC_MATURE` / `otc:{cid}:mature` |

- Journal kinds starting `CDS_` with `ref_type` otc are classified "otc" by `api_views` already (`r == "otc"`).
- Link words gain `cdscpn` = "CDS coupon" (hooks H2-11 and H4-10).

### 5.4 UI (B4; every number is the server's)

**OTC tab**
- `SUBTABS` gains `{key: 'cds', label: 'Single-name CDS'}`, which mounts `T.components.cdsnames`.
- The contracts table below the sub-tabs (the swap ticket's, shared by every kind) shows CDS rows:
  - kind "ORCL 5Y CDS";
  - side "buy protection (short credit)";
  - now "spread now <mid> bp (MODEL) vs dealt <dealt> bp . coupon 100 bp . upfront paid <amount>" (the shape of the CDX row's text).

**The ticket**
- Entity line: name, ticker, CIK, flags, debt with its parts and source, shares, debt / share, balance-sheet date.
- Fields: tenor (1, 2, 3, 4, 5, 7, 10Y with its maturity date), side, notional, coupon (100 / 500; after the first preview the default is set
  from the MODEL bucket before any basis, 2.1; always shown when a negotiated mid is set).
- Buttons: Preview, Request quote, Assumptions, Negotiated mid ..., Deal at the MODEL ..., Reset terms. While the name has no dealing level
  in this account, Request quote is disabled and the preview's blocker says why in words, with the two level buttons beside it.
- Keys `ticket.preview` / `ticket.submit` as in `cdsticket.js`.

**The preview** (headline list like the CDX ticket)
- deal; counterparty and relationship; reference entity and obligation ("senior unsecured, XR14, no reference-obligation ISIN");
- the model: raw CreditGrades + AAA floor = model mid, its bucket, every input with its date and source, and the intermediates behind a "show
  the model" toggle;
- the dealing level (your negotiated mid, your MODEL confirmation, or "not set - not bookable"); negotiated basis; mid; dealt spread (= mid
  +/- half-spread x multiplier);
- upfront in USD and % and the price; accrued rebate; initial payment and its T+3 date; coupon and schedule;
- value at mid; cost of dealing; CS01; rates DV01; JTD; credit exposure; IM; funding; VM mode; the assumed list;
- a plain sentence: "Buying protection on USD 25,000,000.00 of Oracle Corp (ORCL) senior unsecured debt for 5 years: you pay 100 bp a year
  quarterly and are paid (1 - recovery) x the notional if Oracle defaults ...".
- Refusals answer 200 with their blocker in words (V104R2-BW-05).

**Assumptions panel.** Five tiers x (IM %, half-spread bp) with seeds, then Save. Every quote and preview after the save uses them.

**Negotiated mid dialog.** The model mid now, with inputs and time; the owner's mid in bp. Before Save it shows, from
`GET /api/otc/cds/basis/effect`: every open contract on the name in this account with its value now, its value at the new mid and the
change (full revaluation), the total, and for new deals the bucket, tier, IM % and half-spread before and after, and the default coupon
(which the basis never changes). Save / Clear; the SETTINGS_CHANGED event records the same effect.

**Deal at the MODEL dialog.** The model mid, its inputs and their dates, and the words "No market level is known for <name>. Its spread
is a MODEL and has not been validated against traded levels; design-time checks missed traded names by about 2 times in both
directions." The same effect list (no change for a name without a basis). Confirm / Cancel.

**Derivatives page (1.0.6)**
- On a CDS row: CS01 in the sensitivity column; dealt / now spread; a "Simulate credit event ..." button.
- The single-name dialog: event type (bankruptcy / failure to pay), recovery % (40 prefilled), event date (today max), and the rules in
  words: "the contract closes: protection = notional x (1 - recovery), less the accrued premium, settles the next business day; the IM is
  released and the upfront cost is realised".
- The drawer: the event record, reference entity, model inputs and JTD.
- The `CDS_CREDIT_EVENT` step shows in the timeline.

**Other pages**
- Portfolio and Positions rows: side words; the footer line "single-name CDS credit long / short".
- Risk: the Single-name CDS panel (rows, by-name concentration with the equity-shock columns, spread and equity scenarios, warnings).
- Cash: link words "CDS coupon"; the `CDS-` contract link opens the OTC tab.
- Completed Trades: the booking row "ORCL 5Y CDS ... BUY PROTECTION ... spread bp", and the credit event row (blotter hook).

**Rules.** No "undefined", "NaN" or "[object Object]". 1440 px with the dock open shows the actions; 1100 px scrolls and never clips. ASCII
only; no CDN. Any page touching the module checks `T.hasModule('swaps_cds_names')` first; otherwise it shows "unavailable" and makes no call.

---

## 6. Architecture and contracts between the packages

### 6.1 Modules (all new; `services.<attr>` via `server.OPTIONAL_MODULES`, in this order after `swaps_cds`)

| attr | file | package |
|---|---|---|
| `credit_entities` | `finsim\finsim\terminal\credit_entities.py` | B2 |
| `cds_model` | `finsim\finsim\terminal\products\cds_model.py` | B3 |
| `swaps_cds_names` | `finsim\finsim\terminal\products\swaps_cds_names.py` | B1 |
| (UI) | `finsim\finsim\terminal\static\components\cdsnames.js`, `T.components.cdsnames` | B4 |

Import rules:
- A module may import another one's PUBLIC functions only through `services.<attr>`, at call time, never at import time. That keeps every
  module importable alone; a module that is missing answers 501 / unavailable.
- `swaps_cds_names` may import `swaps`, `swaps_cds` (only names in its `__all__`), `swaps_trs` (`close_on`, `last_session_day`, `_vm_close`),
  `prime`, `books`, `ledger`, `db`, `units`, `clock`, `commands`, `marks`, `tables`. These are read-only uses; none of them is edited, except
  through the hook scripts.

### 6.2 Tables

Created at bind with `Database.ensure_schema_ext(owner, statements)`: additive, `IF NOT EXISTS`, never a CHECK widening.

```sql
-- B2 credit_entities
CREATE TABLE IF NOT EXISTS credit_entities (cik INTEGER PRIMARY KEY, ticker TEXT NOT NULL, name TEXT NOT NULL, exchange TEXT,
  tickers_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL, status_reason TEXT, flag TEXT NOT NULL DEFAULT '', flag_source TEXT,
  sic TEXT, sic_desc TEXT, latest_form TEXT, latest_filed TEXT, checked_ts INTEGER, updated_ts INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_credit_entities_ticker ON credit_entities(ticker);
CREATE TABLE IF NOT EXISTS credit_fundamentals (cik INTEGER NOT NULL, end_date TEXT NOT NULL, accn TEXT NOT NULL, known_from TEXT NOT NULL,
  filed TEXT, form TEXT, debt_c INTEGER, debt_recipe TEXT, debt_parts_json TEXT NOT NULL DEFAULT '{}', liabilities_c INTEGER, shares INTEGER,
  shares_end TEXT, shares_tag TEXT, source TEXT NOT NULL, fetched_ts INTEGER NOT NULL, PRIMARY KEY (cik, end_date, accn));
-- B3 cds_model
CREATE TABLE IF NOT EXISTS cds_vol (ticker TEXT NOT NULL, vol_date TEXT NOT NULL, sigma TEXT NOT NULL, n_returns INTEGER NOT NULL,
  first_date TEXT NOT NULL, last_close TEXT NOT NULL, events_through TEXT NOT NULL, source TEXT NOT NULL, fetched_ts INTEGER NOT NULL,
  PRIMARY KEY (ticker, vol_date));
CREATE TABLE IF NOT EXISTS cds_splits (ticker TEXT NOT NULL, split_date TEXT NOT NULL, numerator TEXT NOT NULL, denominator TEXT NOT NULL,
  source TEXT NOT NULL, fetched_ts INTEGER NOT NULL, PRIMARY KEY (ticker, split_date));
-- B1 swaps_cds_names
CREATE TABLE IF NOT EXISTS cds_terms (account_id TEXT PRIMARY KEY, terms_json TEXT NOT NULL, updated_ts INTEGER NOT NULL, command_id INTEGER);
CREATE TABLE IF NOT EXISTS cds_basis (account_id TEXT NOT NULL, cik INTEGER NOT NULL, level_kind TEXT NOT NULL, basis_bp TEXT NOT NULL,
  negotiated_mid_bp TEXT, model_mid_bp TEXT NOT NULL, model_json TEXT NOT NULL, entered_ts INTEGER NOT NULL, command_id INTEGER,
  PRIMARY KEY (account_id, cik));  -- level_kind NEGOTIATED (basis = negotiated - model) | MODEL (the owner's confirmation, basis 0)
CREATE TABLE IF NOT EXISTS cds_credit_events (contract_id TEXT PRIMARY KEY, cik INTEGER, entity_name TEXT NOT NULL, event_type TEXT NOT NULL,
  event_date TEXT NOT NULL, recovery_pct TEXT NOT NULL, notional_c INTEGER NOT NULL, protection_c INTEGER NOT NULL, accrued_c INTEGER NOT NULL,
  late_c INTEGER NOT NULL, net_c INTEGER NOT NULL, settle_date TEXT NOT NULL, settle_id TEXT, entry_id INTEGER, close_entry_id INTEGER,
  command_id INTEGER, ts INTEGER NOT NULL);
```

Refresher writes go through light commands registered by the owning module:
- `credit.entities_upsert` and `credit.fundamentals_upsert` (B2);
- `cds.vol_record` (B3: the volatility rows and the split events of one fetch, together).
- Each sends only new or changed rows, at most 500 per command, with request id `<prefix>:<sha256(payload)[:12]>` (the treasuries pattern), so a
  replay is a no-op.

### 6.3 Contract C-ENT (B2 -> B3, B1, B4): `services.credit_entities`

```python
MIN_DEBT_C = 100_000_000_00        # USD 100M (assumed, Q8)
STALE_DAYS, AGE_WARN_DAYS = 465, 183
def entity(conn, key) -> Optional[dict]          # key: int CIK | str ticker (any of tickers_json); None when unknown
    # {cik, ticker, name, exchange, tickers, status, status_reason, flag, flag_source, sic, sic_desc, latest_form, latest_filed, checked_ts,
    #  instrument_id (master id or None), fundamentals: fundamentals_now(conn, cik)}
def search(conn, q: str, limit=50, offset=0, flt="all", held: Optional[set]=None) -> dict
    # {total, offset, limit, rows: [{cik, ticker, name, exchange, flag, flag_source, status, status_reason, debt_c, debt_end, age_days, shares}]}
def fundamentals_on(conn, cik: int, d: date) -> Optional[dict]   # point in time: the latest end_date among rows with known_from <= d
def fundamentals_now(conn, cik: int) -> Optional[dict]
    # {cik, end_date, accn, known_from, filed, form, debt_c, debt_recipe, debt_parts {tag: cents}, debt_note (a part the parts rule left out),
    #  liabilities_c, shares, shares_end, shares_tag, source, age_days}
def refresh_entity(services, cik: int) -> dict   # network (worker thread only): submissions (+ companyfacts when a newer periodic filing exists)
def instrument_for(services, ent: dict) -> Tuple[Optional[str], str]   # ('ORCL', 'ok') | (None, 'resolving') - worker.resolve(ticker) queued
def universe_status(conn) -> dict                # {offered, listed, by_status {...}, refreshed_ts, source}
```

### 6.4 Contract C-MODEL (B3 -> B1, B4): `services.cds_model`

```python
LABEL = "MODEL"
MODEL_TEXT = ("equity-implied CreditGrades (RiskMetrics 2002) on the share price, 1000-day realised volatility and SEC-reported debt per share"
              " (L-bar 0.5, lambda 0.3, recovery 40 %), plus the ICE BofA AAA OAS floor (FRED) - a MODEL spread, not a dealer quote")
FLOOR_SERIES = "BAMLC0A1CAAA"; LADDER = ("AAA", "AA", "A", "BBB", "BB", "B", "CCC")
def cg_spread(S: float, sigma_s: float, dps: float, T: float, r: float, R=0.40, Lbar=0.5, lam=0.3) -> dict   # pure; all intermediates + c
def realised_vol(closes: Sequence[Decimal], window=1000, minimum=250) -> Tuple[Decimal, int]               # pure
def bucket_of(spread_bp: Decimal, ladder_bp: Dict[str, Decimal]) -> str                                   # pure
def spread_at(model: dict, s_mult: Decimal) -> Optional[Decimal]
    # pure: floor + raw re-run from model["inputs"] with S x s_mult (volatility, debt, floor, curve held); None when it fails (5.2)
def ladder(services, on: Optional[date]=None, today: Optional[date]=None) -> dict
    # {ok, code, reason, as_of, rows {bucket: {series_id, value_pct, spread_bp, as_of, age_bd}}, floor {...}}
def model_spread(services, cik: int, maturity: date, valuation_date: Optional[date]=None, conn=None, now: Optional[int]=None,
                 marks=None) -> dict      # marks: the caller's snapshot (a contracts view's) for S, else services.marks
def request_inputs(services, cik: int) -> dict    # {status: queued|ready|error, retry_ms, message}; never blocks; no I/O
def last_models(ciks) -> Dict[int, dict]         # the in-memory 5Y results of this session for the desk list
```

`model_spread` ok:

```python
{"ok": True, "label": "MODEL", "model": MODEL_TEXT, "cik": ..., "ticker": ..., "name": ..., "valuation_date": iso, "maturity_date": iso,
 "T": "5.1452", "raw_bp": Decimal, "floor_bp": Decimal, "spread_bp": Decimal,             # spread = floor + raw, 0.01 bp
 "bucket": "BBB", "bucket_label": "BBB-like (model spread bucket - not a credit rating)", "quality": "computed" | "distressed",
 "dealable": bool, "dealable_reason": str | None,
 "floor": {series_id, value_pct, spread_bp, as_of, age_bd, source}, "ladder": {...},
 "inputs": {S, S_source, S_quality, sigma, sigma_window, sigma_through, sigma_source, debt_c, shares, shares_adj_note, dps, debt_end,
            debt_known_from, debt_form, debt_recipe, debt_parts, flag, flag_note, r, curve_as_of, R, Lbar, lambda},
 "method": "closed form" | "quadrature",                                                          # 3.1
 "intermediate": {LD, sigma_A, d, A_T, P0, PT, xi, z, G_xi, G_T_xi, H (closed form) | I (quadrature), c},
 "underlying": {"instrument_id": "ORCL", "quote_time": epoch} | {"instrument_id": "ORCL", "close_date": iso},  # for swaps._input_stamp
 "proxy": {"as_of": floor date, "series_id": FLOOR_SERIES},                                          # for swaps._input_stamp
 "curve_as_of": iso}
```

`model_spread` not ok:

```python
{"ok": False, "code": ..., "reason": words, "retry_ms"?: 1500, "vm_outcome"?: "CLOSE_PENDING" | "CAUGHT_UP", "close_date"?: iso,
 "quiet"?: bool, "quiet_until"?: epoch}
```

Behaviour:
- With `valuation_date=d`, every input is that of R (3.2) and nothing is fetched. A missing volatility row of R calls `request_inputs` (an
  in-memory queue, no I/O) and answers CLOSE_PENDING. The split events come from `cds_splits`; a vol row of R exists only after a fetch
  that saw R's events, so a VM never runs on a split list it does not have.
- Live: a missing input queues a fetch and answers `INPUTS_PENDING` with `retry_ms`.
- Never any network I/O on the calling thread.

### 6.5 Contract C-ENGINE (B1 -> B4, B2): `services.swaps_cds_names` and its routes

**Delegate hooks** (called by swaps.py through `_module_for("CDS")`, hook H1-8), with the CDX signatures:
- `preview(services, account_id, kind, terms, conn=None, now=None)`
- `book(ctx, payload)`
- `terminate(ctx, payload)`
- `mark(services, contract, at=None, valuation_date=None, snapshot=None)`
- `dv01_c(services, contract)`
- `leg_specs(services, row)`
- `next_events(services, contract, conn=None, limit=4)`
- `contract_detail(services, contract, conn=None)`

**New hooks the swaps.py / risk.py / blotter hooks call**
- `display_fields(row, terms, inputs) -> {units, unit_label, entry, current, notional_c, extra}`. entry = "250.10 bp"; current = the mid now
  ("248.10 bp") or None; extra = `{cik, ticker, entity_name, tenor_years, side, coupon_bp, upfront_c, upfront, entry_label "dealt spread",
  current_label "spread now (MODEL mid)", floor_as_of, bucket}`.
- `rfq_describe(terms, pv) -> (label, side words, None, notional_c)`, for example ("ORCL 5Y CDS (Oracle Corp)", "BUY PROTECTION", None, n).
- `credit_events_of(services, ids, conn=None) -> {cid: [event]}`.
- `h_credit_event(ctx, payload)`.
- `risk_block(services, rows, base_c) -> Optional[dict]`, shape in 6.6.

**Routes** (`register_routes`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/otc/cds/desk?account_id=` | {account_id, broker, relationship, eligibility, blocked, otc_spread_mult, margin_mode, ladder, floor, tiers {A_OR_BETTER: {buckets, im_pct, half_spread_bp, half_spread_eff_bp, coupon_default_bp, seed {...}, source, updated_ts}, BBB ...}, term_specs, tenors [1,2,3,4,5,7,10], default_tenor 5, coupons [100, 500], recovery "0.40", roll {roll_date, next_roll, maturities {"1": iso ... "10": iso}}, settle_rule, model MODEL_TEXT, label "MODEL", support "LIMITED", universe (C-ENT `universe_status`), bases [{cik, ticker, name, level_kind, basis_bp, negotiated_mid_bp, model_mid_bp, entered_ts}], contracts [ids], conventions {CDS_MODEL, CDS_DEAL, CDS_COUPON, CDS_CREDIT}, unsupported [...]} |
| POST | `/api/otc/cds/preview` {account_id, terms: {cik \| ticker, tenor_years, side, notional, coupon_bp?}} | the preview dict (5.4 fields, keys named as in `swaps_cds._preview_dict` where they mean the same, plus `entity`, `model`, `level`, `basis`, `tier`, `coupon_bp`, `coupon_rule`, `maturity_date`, `jtd_c`, `jtd_rule`, `entity_label`); a refusal answers 200 `{refused: true, bookable: false, code, message, details, blockers}`; a missing dealing level answers 200 with every number, `bookable: false` and the blocker `DEALING_LEVEL_UNCONFIRMED`; `INPUTS_PENDING` answers 202 `{status: "fetching", retry_ms, message}` |
| POST | `/api/otc/cds/terms` | `cds.terms` (light) |
| POST | `/api/otc/cds/basis` {account_id, cik, negotiated_mid_bp \| deal_at_model: true \| clear: true} | `cds.basis` (light): the dealing level (3.5); the effect on open contracts recorded in SETTINGS_CHANGED |
| GET | `/api/otc/cds/basis/effect?account_id=&cik=&negotiated_mid_bp=\|deal_at_model=1\|clear=1` | computed, nothing written: {model_mid_bp, before and after {level_kind, basis_bp, mid_bp, bucket, tier, im_pct, half_spread_bp}, coupon_default_bp, contracts [{contract_id, side, notional_c, value_before_c, value_after_c, effect_c}], effect_total_c, as_of} |
| POST | `/api/otc/cds/contracts/{id}/credit-event` | `cds.credit_event` |

B2 and B3 register their own read routes:

| Method | Path | Owner |
|---|---|---|
| GET | `/api/otc/cds/entities?q=&filter=&limit=&offset=&account_id=` | B2; `held` from the account's CDS contracts |
| GET | `/api/otc/cds/model?cik=\|ticker=&tenor_years=5` | B3; 200 the model dict, 202 while fetching |

**Commands:** `cds.terms` (light), `cds.basis` (light: a negotiated mid, a MODEL confirmation, or clear), `cds.credit_event`, `cds.coupon`
(lifecycle).

**Job:** ProductJob `CDS_COUPON` ("cds.coupon", "swaps_cds_names", "coupon_keys", ("contract_id", "date"), "each CDS coupon date 00:45 ET"),
added before SWAP_VM in `bind`.

### 6.6 Contract C-RISK (B1 -> B2): `risk_block(services, rows, base_c)`

Input: risk's PosRisk rows whose `kind == "SWAP"` and `extra["kind"] == "CDS"`. Returns None when there are none.

```python
{"bucket": "Credit derivatives (single-name CDS notional)",
 "rows": [{contract_id, account_id, label, cik, ticker, entity_name, side, notional_c, exposure_c, tenor_years, maturity_date, mid_spread_bp,
           model_spread_bp, basis_bp, bucket, cs01_c, jtd_c, value_c, quality, note}],
 "by_name": [{cik, ticker, entity_name, contracts, gross_notional_c, net_exposure_c, jtd_c, jtd_pct_nav, cs01_c, level_kind,
              eq_dn30_pnl_c, eq_up30_pnl_c, eq_up50_pnl_c}],          # equity shocks of this name alone (None: unavailable)
 "cs01_c": int, "jtd_gain_c": int, "jtd_loss_c": int, "net_jtd_c": int, "short_credit_c": int, "long_credit_c": int, "gross_pct_nav": Decimal|None,
 "missing": [cid], "scenarios": [{"name": "single-name spreads +25 bp", "shock_bp": 25, "pnl_c": int}, ...,
                                 {"name": "single-name equities -30 % (spreads through the model)", "equity_shock": "-0.30", "pnl_c": int | None},
                                 {"name": "ORCL equity +50 % alone (largest jump-to-default)", "equity_shock": "0.50", "cik": int, "pnl_c": int | None}, ...,
                                 {"name": "default of ORCL (largest jump-to-default loss)", "pnl_c": int}],
 "warnings": [{"code": "CDS_JTD_CONCENTRATION", "message": str, "account_id": None, "instrument_id": None, "value": "0.12", "threshold": "0.10"}],
 "note": "CS01 = P&L for +1 bp of each name's spread; jump to default = (1 - 40 %) x notional less the contract's value now, signed; spreads are MODEL"}
```

### 6.7 Contract C-UI (B4 helpers the hook edits call): `T.components.cdsnames`

`T.components.cdsnames = {mount, ...}`:
- `mount(el, {account_id, blocked, note, onBooked}) -> {setBlocked, refresh, unmount}`
- `kindShort(c)`, `sideText(side, row)`, `nowText(c)`, `dealtNow(c)`, `sensitivity(c)` (CS01 words)
- `swapSide(r)` (positions)
- `creditDialog(st, c, onDone)`, `creditSection(c)` (the drawer)
- `riskPanel(container, helpers) -> {update(block, names)}`
- `footer(x, def)` (portfolio)

---

## 7. Rules for every package

1. **New files only, plus one hook script.**
   - Edit only your owns list. Every change to an EXISTING file goes into your hook script,
     `C:\Users\logan\finsim-orchestrator\cds107\hooks\hooks_<b1|b2|b3|b4>.py`. That folder is durable, outside the repo and not under %TEMP%.
   - The format is `cdx-hooks.py`'s (Appendix D):
     - a list of `{file, anchor, new, why}`;
     - an anchor occurs exactly once;
     - "already applied" when `new` is present;
     - CRLF / LF preserved as found;
     - read and write with Python bytes / UTF-8 (never PowerShell text cmdlets: the 10-21 lesson);
     - `--root <finsim folder>` and `--check`;
     - exit 1 on a missing or ambiguous anchor.
   - Each existing file belongs to exactly ONE hook script (Appendix D). If you need an edit in a file another package's script owns, put the
     exact anchor and text in `requests_for_others`; the orchestrator moves it.
2. **Start from the gated base commit (1.0.6.1, else 1.0.6; 1.0.6 also when the owner puts CDS first, Q13).** Re-read every file you hook or import; the line numbers here are of the 17:30-18:20 ET tree. Write
   your hook script and your public signatures FIRST: tier 1, within about 45 minutes, stubs answering "unavailable" where the work is not done
   yet. Report "Bn hooks landed: ..." in your result.
3. **The rehearsal copy.** Your tests that need the hooks run on a scratch copy, never on the repo tree:
   - make the copy with `C:\Users\logan\finsim-orchestrator\cds107\rehearse.py <name>` (prepared by the orchestrator, section 9.1). It copies
     `data\nyse-holidays.json`, `finsim\finsim` (without `__pycache__`), `finsim\tests` and `finsim\pyproject.toml` into
     `C:\Users\logan\finsim-orchestrator\cds107\checks\<name>-<HHMMSS>\` (the layout `config.repo_root` expects: `<copy>\data\` beside
     `<copy>\finsim\finsim\`);
   - it applies every hook script present, with `--check` and then apply;
   - with `--suite` it runs `py -3.12 tests\run_terminal_suite.py` there.
   - Delete your copies when done. The disk was at 97 % at 18:20 ET today and was full at noon.
   - Tests that need the hooks must **skip** on the plain tree, with the reason (the `HOOKS` pattern of `test_terminal_cds.py`). Both suites
     stay green on the repo tree and on a full rehearsal copy.
4. **Tests.**
   - py-3.12 and py-3.14.
   - Money tests are hand-worked, with the arithmetic in comments. Nothing is pasted from the code's output; the numbers in Appendix C are
     there to check against.
   - No network in tests: inject getters or the FakeProvider.
5. **Honesty.**
   - Every spread is labelled MODEL with its inputs, dates and sources; every assumed term is labelled assumed.
   - No new deal runs on the bare model unless the owner set a dealing level for that account and name (3.5).
   - DTCC data is never used (1): do not open, copy or reuse the research's DTCC files.
   - Missing data is "unavailable" with the reason, never a default.
   - Words first on screen; codes only in hovers; no endpoints, package ids or doc references in trader text.
6. **UI.** Vanilla JS, ASCII, no CDN. Reuse the classes of `cdsticket.js` / `swapticket.js` and P1's. `T.hasModule` gates every call. 1440 /
   1100 px rules, 0 console errors.
7. **Verify like Build 2.**
   - Scratch homes only. `py -3.12 -m finsim serve --port 0 --home <folder> [--offline]`.
   - Playwright py-3.14 at 1440x900 and 1100.
   - One server and one browser per agent at a time, each stopped with POST `/api/shutdown` and its pid confirmed gone.
   - Real data read-only and gently: SEC at 4 requests a second or less with the GOV user agent; Yahoo through the provider.
8. **Never:**
   - deploy; write `%USERPROFILE%\.finsim`; use ports 8765 / 8865;
   - run git; change `finsim.terminal.VERSION`;
   - edit FinSim2 or `site\`;
   - call Cboe from a CDS module;
   - change any CDX behaviour (only the one Unsupported sentence of H1-19);
   - stop a process you did not start.
9. **Finish.**
   - Do not write BUILD_LOG.
   - Return the structured result: what you did; test counts on the repo tree and on the rehearsal copy; hook script path and its `--check`
     output; requests for others; ONE log line (clock from `date`); doc text for the orchestrator (CHANGELOG lines and a TERMINAL_DESIGN
     paragraph).

---

## 8. Packages

### B1 - Engine: `products\swaps_cds_names.py` (kind `CDS`)

**Owns (new):**
- `finsim\finsim\terminal\products\swaps_cds_names.py`
- `finsim\tests\test_terminal_cds_names.py`
- `C:\Users\logan\finsim-orchestrator\cds107\hooks\hooks_b1.py` (edits H1-1..H1-19 in `db.py`, `prime.py`, `server.py`, `products\swaps.py`,
  `lifecycle_jobs.py`, `products\swaps_cds.py`)

**Depends on:** C-MODEL. Code against it; in tests, stub `services.cds_model` with a `types.SimpleNamespace` whose `model_spread` returns the
dicts of 6.4. C-ENT likewise.

**Tasks**

1. **Hooks H1-1..H1-19** (Appendix D) and the module skeleton: constants, tables, `register`, `bind`, `register_routes` answering
   "unavailable", `unsupported()`. The module registers four CONVENTIONS with `tables.add_conventions` (one sentence each, trader words):
   - `CDS_MODEL`: "Single-name CDS spreads are a MODEL: the equity-implied CreditGrades spread (share price, 1000-day realised volatility,
     SEC-reported debt per share) plus the ICE BofA AAA corporate OAS from FRED; not a dealer quote, and not validated against traded
     levels. A new deal on a name needs your negotiated mid or your confirmation to deal at the MODEL; a negotiated mid moves with the model
     from then on."
   - `CDS_DEAL`: "Single-name CDS deal at the mid plus or minus the dealer's half-spread for the spread bucket, against you; the upfront =
     (spread - coupon) x the risky annuity, with the accrued rebate, settles three business days later; full first coupon."
   - `CDS_COUPON`: "A protection buyer pays the fixed coupon (100 or 500 bp) quarterly on the 20th of March, June, September and December (next
     business day), ACT/360; daily variation margin on the model value under the CSA."
   - `CDS_CREDIT`: "Credit events are simulated only when you enter one (bankruptcy or failure to pay, at the recovery you enter): the
     protection, less the accrued premium, settles the next business day and the contract closes."
2. **Terms.** `parse_terms`: cik (int) or ticker, resolved through C-ENT; tenor_years in {1,2,3,4,5,7,10}; side (aliases BUY / SELL);
   notional (CDX rules); coupon_bp in {100, 500} or omitted (default: 100 unless the MODEL bucket before any basis is BB-like or worse,
   2.1). A 400 `TERMS` names its field.
3. **The deal** (`_deal`, shared by preview and booking; no network).
   1. The entity must be COVERED (409 `NOT_COVERED` with the reason).
   2. Maturity by 2.1. The periods are `swaps_cds.coupon_periods(cal, today, maturity)`.
   3. `model = services.cds_model.model_spread(services, cik, maturity, conn=conn, now=ts)`. Not ok raises ConflictError with its code
      and words. `INPUTS_PENDING` carries `details.retry_ms`, so both B1's preview route and the generic `/api/otc/preview` (swaps.py: a
      RejectError with `retry_ms` answers 202) return 202.
   4. level = the `cds_basis` row (`level_kind` NEGOTIATED or MODEL); mid = model.spread_bp + basis (0.0001 bp). No row: the blocker
      `DEALING_LEVEL_UNCONFIRMED` (3.5). The preview keeps every number and answers `bookable` false; `otc.rfq.request` refuses through
      `swaps.preview_blocker` (409, the same words); `book` re-checks and raises ConflictError 409 (a level cleared between the quote and
      the accept). Termination never checks it.
   5. A distressed model without a NEGOTIATED level is refused (`DISTRESSED_NEEDS_NEGOTIATED_MID`); a MODEL confirmation is not enough.
   6. tier = the tier of `services.cds_model.bucket_of(mid, {b: r["spread_bp"] for b, r in model["ladder"]["rows"].items()})` (the same
      day's FRED ladder the model used); coupon = the terms' `coupon_bp`, else 100 unless `model["bucket"]` (the MODEL bucket, before the
      basis) is BB, B or CCC, then 500; hs = tier half-spread x `prime.read_schedule(conn, acct).otc_spread_mult`; deal = mid +/- hs. An
      accepted RFQ books with `ctx.rfq_firm`'s `dealt_spread_bp`, `coupon_bp`, `maturity_date` and `tier` (C14; all four fixed when the
      dealer quoted, hook H1-13), so a coupon left to its default cannot flip between 100 and 500 when the mid crosses the BBB / BB
      boundary before the accept (about 400 bp x RPV01, some 17 % of the notional, of upfront). The mid of the booking instant gives mark0
      and the cost of dealing; the IM uses the quoted tier.
   7. The curve: `swaps.dfs_for` as `swaps_cds._curve` does.
   8. mark0, deal value, CS01 and DV01 with `swaps_cds.value_c` / `cs01_from` at recovery 0.40 and the chosen coupon.
   9. The rebate with `accrual_start` / `rebate_c`. The initial payment as in `swaps_cds._deal`, settling **T+3** business days.
   10. IM = round(N x tier im_pct).
   11. JTD = sign x round(N x 0.60) - mark0.
4. **`preview`** (5.4 fields, `kind_label` "single-name CDS"), with funding (`swaps.funding_preview`: IM plus a payable initial payment),
   eligibility and relationship (as `swaps_cds.preview`).
5. **`book`.** Postings of 2.3, the instrument row, `accrual_state`, the event. Idempotent through the RFQ / command request id.
6. **Valuation.**
   - `_valuation(services, row, now, valuation_date=None, shift_spread_bp=None, conn=None)`:
     - model of the contract's maturity at the valuation date (3.2);
     - plus the account basis;
     - inputs named `underlying` / `proxy` / `curve_as_of` (6.4);
     - `par_rate` / `dealt_rate` / `annuity` strings for the swaps VM memo, as `swaps_cds` does;
     - VM outcomes passed through with `quiet` / `quiet_until` = 12:00 ET of the next business day of R (the CDX rule).
   - `mark`: a live failure gives the stored mark as a stale MarkView (as `swaps_cds._stale`). When `snapshot` is given (a contracts
     view's; `swaps._view_snapshot` already holds `terms.underlying_id`), pass it to `model_spread(marks=snapshot)` so every row of a view
     prices S from one instant.
   - Also `cs01_c` and `dv01_c`.
7. **Premium leg and coupon.**
   - `leg_specs`: FIXED at C, ACT/360, the periods.
   - `_catch_up` as CDX.
   - `h_coupon` / `coupon_keys` for KIND `CDS`: idem `cdscpn:`, memo "CDS coupon ...", `otc_resets` PAY detail kind "CDS coupon"; the last
     one matures.
   - The CDS_COUPON job added in `bind` before SWAP_VM.
8. **`terminate`** (as `swaps_cds.terminate`, with the model mid and the tier half-spread).
9. **Credit event**, exactly 2.4.
10. **Views.**
    - `next_events` and `contract_detail`: side_text, entity, tenor, cs01_c, jtd_c, a live block {mid_spread_bp, model_spread_bp, basis_bp,
      floor_bp, raw_bp, bucket, upfront_pct, price, rpv01, value_c, inputs_as_of}, terms_text, the credit event if any, label MODEL.
    - `display_fields`, `rfq_describe`, `credit_events_of`.
11. **`cds.terms` and `cds.basis`** (5.1, 3.5: a negotiated mid, a MODEL confirmation or clear; the effect on the open contracts of that
    account and name computed by full revaluation and recorded in SETTINGS_CHANGED) and `GET /api/otc/cds/basis/effect` (the same
    computation, nothing written). **`desk`** and the routes (6.5).
12. **`risk_block`** (6.6): JTD, by-name concentration, spread scenarios, the equity scenarios of 5.2 (each contract revalued at
    `services.cds_model.spread_at(model, 1 + x)` + its basis; None gives "unavailable"), the largest-loss default, the warnings at 10 % of
    NAV.
13. **`unsupported()` rows:**
    - "Single-name CDS dealer quotes" LIMITED ("no free single-name CDS quote: MODEL spread, not validated against traded levels; set a
      negotiated mid per name, or confirm dealing at the MODEL");
    - "Single-name CDS on banks, insurers and finance companies" LIMITED;
    - "Foreign private issuers, IFRS filers and names with under USD 100M of debt" NOT_OFFERED;
    - "Restructuring clauses, subordinated / LCDS, sovereign and municipal CDS, CDS options, tenors other than 1-5 / 7 / 10 years"
      NOT_OFFERED;
    - "Real credit events (ISDA Determinations Committee rulings, auctions)" SIMULATED;
    - "Reference obligations (the ISDA standard reference obligation list)" NOT_OFFERED;
    - "Traded single-name CDS prints" NOT_OFFERED ("no free source whose terms allow automated use: DTCC's terms prohibit it").

**Acceptance**
- Both suites green on the repo tree (hook tests skipped) and on a full rehearsal copy (hook tests run).
- `hooks_b1.py --check` is clean on a fresh copy of the base commit.
- On the rehearsal copy with the hand-worked fixtures, every money test below passes.
- A served rehearsal copy (offline, the model stubbed by a fixture module) shows:
  - `GET /api/otc/cds/desk`: 200 with the tiers seeded from GS;
  - `POST /api/otc/cds/preview`: 200;
  - an RFQ request without a dealing level answers 409 `DEALING_LEVEL_UNCONFIRMED` in words and writes nothing; after
    `cds.basis {deal_at_model: true}`, RFQ request -> quote -> accept books CDS-000001; integrity full ok.
- **CDX unchanged:** `test_terminal_cds.py`, `test_terminal_rfq.py`, `test_terminal_otc_asof.py` and `test_terminal_fund_reconcile.py` pass
  unedited on the rehearsal copy.

**Hand-worked money tests** (`test_terminal_cds_names.py`; numbers in Appendix C; the DFS fixture of `test_terminal_cds.py`; model stubbed at a
mid of 65.47 bp, bucket AA, tier A_OR_BETTER, GS; every test that books first sets `cds.basis {deal_at_model: true}` for the fixture name,
or the negotiated mid it names)

| # | Test |
|---|---|
| 1 | Maturities for 1/3/5/7/10Y on 2026-10-29, and 5Y on 2027-03-22 = 2032-06-20 |
| 2 | Tier and spreads: buyer 67.47, seller 63.47; with `otc_spread_mult` 2.0, buyer 69.47 |
| 3 | **Buyer booking, 5,000,000.00:** rebate 39 days 5,416.67; upfront received 74,282.20 (= to_c(5,000,000 x -32.53 bp x RPV01 4.5669965459)); IM 200,000.00 (Dr 1330 / Cr 1010); initial-payment entry Cr 2345 74,282.20, Cr 2340 5,416.67, Dr 1200 79,698.87; settlement 79,698.87 due 2026-11-03; mark0 -78,913.97; cost of dealing -4,631.77; NAV change -4,631.77 = +79,698.87 - 5,416.67 - 78,913.97 |
| 4 | **Seller booking, the same terms:** dealt 63.47; upfront paid 83,553.33 (Dr 1335); rebate paid 5,416.67 (Dr 1225); Cr 2100 88,970.00; mark0 +78,913.97; cost of dealing -4,639.36; IM 200,000.00; funding checks 288,970.00 together |
| 5 | Premium nights after the trade date: 138.89 / 138.88 / 138.89 / 138.89 (cumulative 5,555.56 / 5,694.44 / 5,833.33 / 5,972.22) |
| 6 | First coupon 2026-12-21: 12,638.89 (91 days); before the night of 12-20 is accrued: SKIPPED ACCRUALS_PENDING |
| 7 | **Credit event (buyer):** event 2026-11-02 entered 2026-11-04 with the night of 11-03 already posted; protection 3,000,000.00; accrued 5,972.22; late 138.89; entry Dr 2340 6,111.11 / Cr 4453 138.89 / Cr 4450 3,000,000.00 / Dr 1200 2,994,027.78; settlement due 2026-11-03; then the close releases the cost basis Dr 2345 / Cr 4450 74,282.20 and the IM 200,000.00; status TERMINATED; GL 2340, 2345, 1330 and 1320 / 2330 of the contract all 0; accrual_state gone; SWAP row EXPIRED |
| 8 | Credit event (seller): the mirror lines; net payable 2,994,027.78 |
| 9 | JTD: buyer +3,078,913.97, seller -3,078,913.97 |
| 10 | Negotiated basis: model 65.47 with negotiated 84.00 gives basis +18.53; the next model 66.10 gives mid 84.63; clear gives 66.10; each change one SETTINGS_CHANGED |
| 11 | Distressed: model quality distressed with no basis gives a refused preview (`DISTRESSED_NEEDS_NEGOTIATED_MID`); with a basis it is bookable and labelled |
| 12 | VM: date d with the stubbed model `CLOSE_PENDING` posts nothing (SKIPPED, quiet); once ok it posts one VM with business date d; the CAUGHT_UP outcome is quiet and terminal |
| 13 | Termination at mid - hs (buyer) after a catch-up of the owed nights; the cost basis released |
| 14 | RFQ: the quote words name the entity and spread; accept within 120 s books at the quoted 67.47 even when the model moved by +1 bp (firm); `QUOTE_MOVED` never fires for CDS |
| 15 | **Dealing-level guard:** with no `cds_basis` row the preview shows every number with `bookable` false and the blocker `DEALING_LEVEL_UNCONFIRMED` (words first); `otc.rfq.request` and `otc.book` answer 409 with the same words and write nothing; after `deal_at_model` the buyer books at the model mid 65.47 (dealt 67.47); after a negotiated 84.00 it books at mid 84.00; `clear` restores the guard; a level cleared between quote and accept makes the accept 409; each change is one SETTINGS_CHANGED |
| 16 | **Firm quote terms:** an RFQ with the coupon omitted quotes coupon 100 (model bucket AA), maturity 2031-12-20 and tier A_OR_BETTER; the stub then moves the model to 190.00 bp (BB-like) before the accept; the booking keeps coupon 100, the maturity, the quoted 67.47 and the tier's IM 4 % (200,000.00) |
| 17 | **Basis effect:** with the buyer contract of test 3 open, the effect route for a negotiated mid of 265.47 bp lists it with its value at 65.47 (-78,913.97) and at 265.47 (full revaluation, hand-derived with `swaps_cds.value_c`) and the change; the command's SETTINGS_CHANGED payload records the same contract, values and total; no posting is restated |
| 18 | **Coupon and tier with a wide negotiated mid:** model 65.47 (AA) with a negotiated 315.00 (B-like): the default coupon stays 100; the tier becomes B (IM 12 %, half-spread 15.0 bp); the effect route shows the tier, IM and half-spread before and after |
| 19 | **Equity scenarios:** the stub's `spread_at` returns hand values for S x 0.7 / 1.3 / 1.5; the risk block's scenario rows and the `by_name` columns equal the full revaluation at those mids plus the basis; a `spread_at` of None shows "unavailable", never 0 |

### B2 - Reference entities and fundamentals (`credit_entities.py`) plus the risk / portfolio / blotter wiring

**Owns (new):**
- `finsim\finsim\terminal\credit_entities.py`
- `finsim\tests\test_terminal_credit_entities.py`
- `finsim\tests\test_terminal_cds_wiring.py` (hook-dependent, skips without the hooks)
- `finsim\tests\fixtures\terminal\cds\edgar\` (small synthetic payloads in the real API shapes)
- `C:\Users\logan\finsim-orchestrator\cds107\hooks\hooks_b2.py` (edits H2-1..H2-12 in `instruments.py`, `accounting.py`, `risk.py`,
  `api_views.py`, `blotters.py`)

**Depends on:** C-RISK (`risk_block` shape) and C-ENGINE `display_fields` / `rfq_describe` names, for the wiring tests on the rehearsal copy.

**Tasks**

1. **The SEC client.**
   - `_sec_get(services, url) -> bytes`: `quotes.http.get_with_policy` (GOV user agent by host), a module `RateLimiter(4)`,
     `providers.health.allow("sec")` / `record("sec", ...)`, `FetchError` kept.
   - The network functions (`fetch_universe`, `fetch_frames`, `refresh_entity`) take an optional `getter` (tests pass one that serves the
     fixtures). The refreshers pass none and return at once when `services.providers` is missing or offline. Tests call the functions with a
     getter; nothing in a test touches the network.
   - URLs:
     - `https://www.sec.gov/files/company_tickers_exchange.json`;
     - `https://data.sec.gov/api/xbrl/frames/{us-gaap|dei}/{tag}/{USD|shares}/CY{yyyy}Q{q}I.json`;
     - `https://data.sec.gov/submissions/CIK{cik:010d}.json`;
     - `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json`.
   - Verify the exchange strings (the allowlist of 4.1; any other string logged) and the eight flag tags (`Assets`, `Deposits`,
     `LiabilityForFuturePolicyBenefits`, `LiabilityForClaimsAndClaimsAdjustmentExpense`,
     `LiabilityForUnpaidClaimsAndClaimsAdjustmentExpenseNet`, `PolicyholderFunds`, `SeparateAccountsLiability`,
     `RealEstateInvestmentPropertyNet`) with one live fetch each. Report the counts. A tag the API refuses falls back to SIC on demand.
2. **The universe** (4.1): the exchange allowlist {NYSE, Nasdaq, CBOE, TXSE}, case-insensitive, any other string dropped and logged once
   per refresh with its row count; primary ticker, `tickers_json`, entity status.
3. **Fundamentals from frames** (3.2):
   - per CIK: `end` = the latest end among the debt-recipe tags (R1-R6) across the fetched quarters; components of that `end` only;
     recipes R1-R6 with the parts rule and the R4b fallback (3.2); the Liabilities check; a part the parts rule left out named in
     `debt_parts_json`;
   - two rows of one `end`: the latest `filed`, else the latest `known_from`, then the higher `accn` (in `fundamentals_on` / `now` too);
   - the share chain;
   - flags from the same `end`, with `flag_source` "tags" (the SIC check of task 4 replaces it with "sic");
   - `known_from` = today (NY) for new (cik, end, accn).
4. **`refresh_entity` (on demand):**
   - from `submissions`: the SIC and its flag (6000-6499 = FINANCIAL, 6798 = REIT), and the latest 10-Q / 10-K / 20-F / 40-F (form,
     filingDate, reportDate);
   - a 20-F / 40-F gives `FOREIGN_FILER`;
   - a reportDate newer than the frames `end` triggers `companyfacts`: the same recipe on non-dimensional instants of forms 10-K, 10-Q and
     their /A versions, latest `end` then latest `filed`;
   - over the 16 MB cap: keep the frames row, labelled "the newest 10-Q is not in the SEC's XBRL API yet; using balance sheet <end>".
5. **The refreshers** `cds_fundamentals` (daily 06:30 ET, weekly full) and the on-demand path used by B3's queue (`refresh_entity`). Light
   commands `credit.entities_upsert` / `credit.fundamentals_upsert` with changed rows only (6.2).
6. **`entity`, `search`, `fundamentals_on` / `now`, `instrument_for`, `universe_status`, and `GET /api/otc/cds/entities`.** `instrument_for`
   order: the master id for the ticker, then the `.` / `-` swapped variant, else `services.worker.resolve(ticker)` and "resolving".
7. **Hooks H2-1..H2-12:**
   - reference pins;
   - asset class;
   - the risk bucket, labels, definitions, contract label, exposure, `_pos_risk` branch, the `single_name_cds` view field and block call;
   - the JTD warnings appended;
   - the Portfolio footer keys;
   - link words and acronyms;
   - the blotter describe, events and IN list.

**Acceptance**
- Both suites green (repo tree and rehearsal copy).
- Tests from fixtures:
  - the exchange allowlist (`CBOE` and `cboe` kept, `TXSE` kept, `OTC` and null dropped, an unknown string dropped and logged) and the
    primary ticker (the JPMorgan / AMJB case resolves to JPM when JPM is in the fixture master);
  - each recipe R1-R6 with a hand sum, for example AAPL-like `LongTermDebt` 82,300,000,000 + `CommercialPaper` 1,997,000,000 =
    84,297,000,000;
  - fixtures copying the real shapes: ORCL at 2026-08-31 (only `LongTermNotesAndLoans` 117,712,000,000 and `NotesPayableCurrent`
    7,625,000,000: R6 = 125,337,000,000, beside an older 2026-05-31 R1 row of 129,541,000,000 that is not used); KBH
    (`NotesAndLoansPayable` 2,109,145,000 alone: R6); VZ (`LongTermDebtAndCapitalLeaseObligations` 143,448,000,000 +
    `LongTermDebtCurrent` 21,783,000,000 = 165,231,000,000 by R4b); PEP (`ShortTermBorrowings` 10,602,000,000 with `CommercialPaper`
    6,100,000,000: 10,602,000,000); ICE (both 1,218,000,000: 1,218,000,000); a CP larger than its STB is added; a `NotesPayable` with a
    smaller `ConvertibleNotesPayable` counts once;
  - the same-end tie-break (an amendment with a later `filed` wins; equal `filed`: the later `known_from`, then the higher `accn`);
  - an insurer with only `LiabilityForClaimsAndClaimsAdjustmentExpense` is flagged financial with `flag_source` "tags"; the browse rows
    carry the source;
  - the stale tag ignored (a 2014 `LongTermDebt` beside a 2026 `end`);
  - debt > liabilities rejected; deposits never counted;
  - share chain and split adjustment (a 10:1 split after the share date multiplies by 10);
  - point in time: rows known on 10-20 and 10-29 give the 10-20 row for d = 10-25;
  - stale > 465 days; under USD 100M;
  - the SEC user agent equals `config.GOV_UA` and at most 4 requests a second (an injected clock);
  - offline makes no call.
- **A live check once (real SEC, gently): a full universe build on a scratch home.** Report:
  - request count, bytes, seconds;
  - offered count (expected 1,650-1,800);
  - by-status counts;
  - the sample of 36 research names with their recipe and debt (ORCL, AAPL, MSFT, JPM, NEE, T, VZ, F (`NO_DEBT` expected), GM, BA, CCL ...),
    asserting ORCL offered at 125-130B (R6 at 2026-08-31, or R1 at 05-31 while the frames lag) and VZ at about 165B (R4b with its current
    part);
  - any unknown exchange string seen (none expected) and the flag counts by source.
- Wiring tests on the rehearsal copy (B1's module present):
  - a CDS row in risk sits in the single-name bucket with the sign of a credit exposure;
  - `contract_label` "ORCL 5Y CDS buy protection";
  - the Portfolio footer CDS keys; `OTC CDS` asset class;
  - the blotter booking row "ORCL 5Y CDS ... BUY PROTECTION ... spread bp" and the credit-event row;
  - `otc_reference_ids` includes the CDS equity;
  - the CDX keys and rows unchanged (`test_terminal_portfolio_contract.py` unedited and green).

### B3 - Spread model (`products\cds_model.py`)

**Owns (new):**
- `finsim\finsim\terminal\products\cds_model.py`
- `finsim\tests\test_terminal_cds_model.py`
- `finsim\tests\fixtures\terminal\cds\yahoo\` (daily-bar fixtures)
- no hook script (B3 edits no existing file)

**Depends on:** C-ENT (stub it with fixture rows in tests).

**Tasks**

1. The pure functions `cg_spread` (the closed form, the quadrature path and the exception guard of 3.1), `realised_vol`, `bucket_of` and
   `spread_at` (5.2, 6.4).
2. **`ladder`.** The seven `rates.OAS_SERIES` buckets (latest or OF a date), the floor and their ages (business days by `services.cal`).
3. **The volatility fetch:**
   - live `_chart(..., "range=5y&interval=1d&events=div%2Csplits")` or offline `daily()` / `events()`;
   - compute and store `cds_vol` rows for every complete session R not yet stored, from the contract's trade date or the last 2 sessions
     for a desk preview;
   - a session is complete when now >= its close + 20 minutes;
   - the split events of each fetch are written to `cds_splits` in the same `cds.vol_record` as the vol rows, and each vol row records
     `events_through` (the session its fetch saw). No split state lives only in memory, so a VM after a restart has it (3.2).
4. **`model_spread`:**
   - live and VM paths (3.2, 4.4);
   - S from marks (live) or `swaps_trs._vm_close` / `close_on` with the equity's `session_group` (VM);
   - D from C-ENT `fundamentals_on` / `now` with the split adjustment;
   - r from the curve;
   - the guards and the distressed flag;
   - the inputs and intermediates dicts;
   - the in-memory 5Y cache for the desk list.
5. **`request_inputs` and the refreshers:**
   - The queue is a module-level ordered set guarded by a `threading.Lock` (request threads add, the worker drains). Each entry has a status
     (queued / in flight / ready / error with the reason and time); an error is kept 10 minutes, then the next request re-queues it.
   - `cds_inputs`, `interval_s=5`: drains up to 5 queued CIKs a run in order: `credit_entities.refresh_entity` when unchecked for 7 days,
     `instrument_for` (a resolve), the volatility fetch. It returns at once when the queue is empty (no network) and when offline. Once
     per session after 09:45 ET it also queues every open contract's equity, for its split events (a split is known on its ex-date).
   - `cds_eod`, `run_after_et="16:45"`: every reference equity of an OPEN CDS contract, back-fill included.
   - Both registered in `bind` with labels "Single-name CDS inputs (SEC EDGAR, Yahoo daily closes)".
6. **`GET /api/otc/cds/model`** (6.5). Light command `cds.vol_record`.
7. **The production-input report** (acceptance; no DTCC file and no traded level anywhere):
   - a fixed list of 20 names on production inputs, fetched gently (SEC frames from the universe build; Yahoo 20 requests; companyfacts
     20 requests for `Liabilities` and `AccountsPayableCurrent`): ORCL, VZ, T, KBH, KSS, AAPL, MSFT, AMZN, NVDA, PFE, BA, F (`NO_DEBT`
     expected), GM, CCL, AAL, JPM, MET, NEE, DUK, AMC;
   - per name, at the 5Y maturity: every input with its date, the shipped model (debt-only, 3.2) and beside it the CreditGrades Appendix B
     debt (debt + 50 % x (liabilities - debt - accounts payable)), each with its bucket. Liabilities = `Liabilities`, else
     `LiabilitiesAndStockholdersEquity` - `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` (else
     `StockholdersEquity`): Oracle and Verizon report no `Liabilities` total. Accounts payable = `AccountsPayableCurrent`, else
     `AccountsPayableAndAccruedLiabilitiesCurrent`, else 0, said in the row;
   - regression first: from the saved 10-28 inputs of Appendix B the module reproduces its rows to 0.01 bp (ORCL 112.54 / 149.08, VZ
     59.89 / 78.72, KBH 124.15 / 149.73, KSS 547.33 / 812.29);
   - no tuning. The owner chooses the debt rule under Q1.

**Acceptance**
- Both suites green.
- `test_terminal_cds_model.py`:
  - the vectors V1-V7 with every intermediate to 1e-9 relative and raw_bp to 0.01 bp (Appendix A): V5-V7 by the quadrature path; in V6
    the closed form raises and no exception leaves `cg_spread`; V7 takes the quadrature value, not the cancelled closed form;
  - the RV vector (0.1673);
  - calibration: V1 117.97 BBB, V2 42.00 AAA, V3 628.94 B, V4 82.25 A, V5-V7 distressed;
  - `spread_at` on V1 (floor 42.00): S x 0.7 -> 160.31, x 1.3 -> 93.85, x 1.5 -> 83.09;
  - the bucket tie rule (a test ladder A 100 / BBB 400: a spread of 200.00 bp is ln 2 from each and goes to BBB, the wider bucket);
  - every code of 3.6 from a missing input (fixture runtime with RateStore / CurveStore fixed);
  - VM: CLOSE_PENDING when the AAA OAS of R is not published; CAUGHT_UP when FRED skipped R and a later value exists; the vol row of R missing
    gives CLOSE_PENDING plus a queued request;
  - split adjustment, and the persisted split: a VM of a session after a split, computed by a new runtime on the same database (nothing in
    memory), uses the split from `cds_splits`; a ticker with no vol row of R answers CLOSE_PENDING and queues a fetch;
  - `request_inputs` never blocks;
  - offline refreshers make no call.
- The production-input report of task 7 in the result.

### B4 - UI (`static\components\cdsnames.js`) and the UI hooks

**Owns (new):**
- `finsim\finsim\terminal\static\components\cdsnames.js`
- `finsim\tests\test_terminal_cds_ui.py` (static checks)
- `C:\Users\logan\finsim-orchestrator\cds107\hooks\hooks_b4.py` (edits H4-1..H4-14 in `api.py`, `static\index.html`,
  `tests\terminal_fixture_server.py`, `static\pages\markets_otc.js`, `static\components\swapticket.js`, `static\pages\derivatives.js`,
  `static\pages\positions.js`, `static\pages\portfolio.js`, `static\pages\risk.js`, `static\pages\cash.js`, `static\core.js`)

**Depends on:** C-ENGINE routes, C-MODEL and C-ENT read routes. Code against the shapes of 6.4-6.6. Until the routes land, the component shows
"unavailable" through `T.hasModule`.

**Tasks**

1. **The component** (5.4, 6.7):
   - desk header, search and browse table, ticket, preview, schedule table;
   - the Assumptions panel (5 tiers);
   - the negotiated mid dialog and the Deal-at-the-MODEL dialog, each with the effect list of `GET /api/otc/cds/basis/effect` before
     Save (the open contracts' value changes and total, the tier / IM / half-spread change, the default coupon);
   - the dealing-level blocker in the preview with its two buttons; Request quote disabled until a level exists;
   - the flag source in the browse rows and the "known so far" caption of the financial filter;
   - Request quote through `T.components.swapticket.rfqRequest(account, 'CDS', terms)`;
   - the 202 retry loop (`retry_ms`, at most 20 tries, then "still fetching - try again");
   - the credit dialog and drawer section, the risk panel, the footer and row helpers;
   - `T.keys.uses('components/cdsnames.js')`; desk refresh every 60 s in place.
2. **Hooks H4-1..H4-14** (Appendix D); each JS hook is a one-to-three-line call to a helper, guarded by `T.components.cdsnames`.
3. **The walk** on a full rehearsal copy served ONLINE (real SEC / Yahoo / FRED; a scratch home; port 0): every element of 5.4 at 1440 with the
   dock open and at 1100. A name with every unavailable reason that occurs (`NO_DEBT` Ford, `FOREIGN_FILER`, a financial LIMITED, a stale
   one). The guard on a first preview; a MODEL confirmation and a negotiated mid with their effect lists; booking through RFQ when B1 has
   landed; else the preview states only.

**Acceptance**
- Both suites green, including `test_terminal_static.py` with the new optional file listed in `PLANNED_STATIC` / `index.html` / the fixture
  server.
- `test_terminal_cds_ui.py`: ASCII; registers `T.components.cdsnames`; balanced; `T.keys.uses`; no code-leading refusal (the BW-04 pattern);
  every route the file calls is one of 6.5; `T.hasModule('swaps_cds_names')` guards the calls.
- Walk: 0 console errors, 0 HTTP >= 400 (refusals answer 200), no "undefined" / "NaN" / "[object Object]", screenshots, and the CDX sub-tab
  unchanged.

---

## 9. Integration, verification, gate and deploy

### 9.1 Before launch (orchestrator, about 15 minutes)

1. Wait for the gate of the release this builds on: 1.0.6.1 (FX / CCS), or 1.0.6 if 1.0.6.1 is dropped or the owner puts CDS first (Q13). The commit must be verified from its
   archive (15.1 rule 3).
2. Create `C:\Users\logan\finsim-orchestrator\cds107\{hooks,checks}`.
3. Write `rehearse.py` as described in rule 7.3: copy, apply every `hooks_*.py`, optionally run the suite, `--clean` removes copies older than
   2 h.
4. Mirror the package hand-backs there as they arrive (the Storage Sense rule).

**Shared files with the FX / cross-currency build** (`docs\FX_CCS_PLAN.md`)
- It owns and edits `swaps.py`, `swaps_trs.py`, `prime.py` (P-C) and `otc_options.py`, `risk.py`, `otcticket.js`, `swapticket.js`,
  `pbschedule.js`, `risk.js`, `derivatives.js`, `markets_otc.js` (P-B).
- This build's hooks touch `swaps.py`, `prime.py`, `risk.py`, `swapticket.js`, `derivatives.js`, `markets_otc.js` and `risk.js`. The anchors in
  Appendix D are from the 1.0.6 tree.
- Each builder therefore re-reads those files at the 1.0.6.1 commit and re-anchors its hook script there (rule 2). An anchor 1.0.6.1 changed is
  re-anchored IN THE HOOK SCRIPT, never by editing FX code.
- 1.0.6.1 adds PB schedule fields (`xccy_basis_*`) but no OTC kind. If a later version adds a kind to `db.OTC_KINDS` or `prime.OTC_LIVE`,
  merge both lists in one edit.
- The CDS packages edit no existing file. The pool could overlap the FX pool on a machine with more memory, but on this 7.6 GB laptop the two
  pools run one after the other (the FX plan's D1). The one exception is optional and light: B3 and the core of B2 may start early in a
  scratch folder outside the repository (section 10).

### 9.2 Pool and integration

**Launch** B1-B4 together. Tier-1 hooks and signatures are due within about 45 minutes.

**Integration** (1-1.5 h)
1. `hooks_b1..b4.py --check` on the repo tree, then apply.
2. Both suites on py-3.12 and py-3.14 (`tests\run_terminal_suite.py`).
3. Every hook-dependent test now runs and none skips.
4. A served offline smoke: desk, preview with a fixture model, RFQ, book, VM, credit event on scratch homes.

### 9.3 Verification (2-2.5 h; a P8-style agent)

| # | Check |
|---|---|
| V1 | Suites green on both Pythons; test counts reported |
| V2 | **CDX non-regression A/B.** Two fresh copies of one read-only sqlite3 backup of the live database (running the base release, 1.0.6.1). A harness (like P8-lite's fund-reconcile test and the import checker) opens one copy with the base release's code and one with the 1.0.7 code, at one FakeClock instant with the same RateStore / CurveStore contents, and runs the same lifecycle ticks. CDX-000001 / -000002 marks, VM rows, accrual state and next coupons must be identical. `otc_contracts` widened with every row kept, integrity full ok, the owner's positions and NAV identical |
| V3 | **Real-data walk** on a copy of the live database (base release) served ONLINE (port 0, scratch home). The universe loads (report the count and the time: about 2-3 minutes on the first run). Search ORCL and preview 5Y buy protection 25,000,000.00 in `I1:PB_GS`: every number with its source and date, the MODEL label, the AAA floor, the debt parts (R6, balance sheet 2026-08-31, about 125.3B), the 1000-day vol window, the IM tier - and **the guard fires**: not bookable, `DEALING_LEVEL_UNCONFIRMED` in words, Request quote refused 409 with the same words. Set a negotiated mid of 300.00 bp (a test value on the copy, labelled as the owner's level): before Save the dialog shows no open contract, the tier change BBB -> B (IM 5 % -> 12 %, half-spread 3.0 -> 15.0 bp) and the coupon staying 100; after Save the preview deals at mid + the B half-spread with an upfront of about USD 2.1M paid. Request quote, accept, CDS-000001 in the contracts table, Portfolio row, Risk single-name block (CS01, JTD, ORCL concentration, the ORCL equity -30 / +30 / +50 % rows), Derivatives (CS01, credit-event button), Cash (initial payment T+3), Completed Trades (the booking row). Move the mid to 250.00 bp: the dialog lists CDS-000001 with its value change before Save, and SETTINGS_CHANGED records it. Then a second name through "Deal at the MODEL", a financial (LIMITED, flag source shown), Ford (`NO_DEBT`), KBH (offered by R6), a foreign filer, a sub-USD 100M name |
| V4 | **Lifecycle rehearsal** on the same copy with a FakeClock (a test-style harness, not the live clock). Each name's dealing level is set first. The nights accrue; the VM of the trade date posts the next morning after the AAA OAS of that date is put into the RateStore; termination of a second contract; a credit event on a third with the hand-worked lines; integrity and trial balance after each |
| V5 | 1440 / 1100 px; 0 console errors; 0 HTTP >= 400; keys |
| V6 | B3's production-input report (task 7: 20 names, debt-only beside the CreditGrades Appendix B variant, the regression rows; no traded levels) is attached to the result |

### 9.4 Fixes (at most 2 rounds, each re-verified by V1, V2 and the failing items)

### 9.5 Gate and deploy (orchestrator; the standing pre-approval of 2026-10-27 15:12 EDT covers FinSim releases)

1. VERSION 1.0.7, CHANGELOG (from the packages' doc text), BUILD_LOG lines.
2. Commit with `commit_terminal2.sh`.
3. The archive suite check; the release from the commit; the manifest.
4. **Before install:** the backup by `install_release2.ps1` (stop, back up, install). The first open rebuilds `otc_contracts` for the new kind
   (`_widen_otc_kinds`, rows kept): check `integrity full` right after start.
5. **After deploy:**
   - the first `cds_fundamentals` run completes (universe count; about 201 requests and 2-3 minutes, the desk's loading line meanwhile);
   - the Single-name CDS tab loads; an ORCL preview works;
   - CDX marks and NAV equal to before the deploy at the same quotes;
   - integrity ok in P / F / I.
6. Owner report, in plain words:
   - what the tab does;
   - **the model is not validated**: every spread carries the MODEL label; the first draft's comparison with traded levels cannot be
     reused (DTCC's terms) and was weak (most investment-grade names at the AAA floor; the floor alone almost as good); names whose CDS
     trades are missed by about 2 times in both directions;
   - **his own target, ORCL**: the model gives about 113-116 bp (BBB-like), while public news put Oracle's 5-year CDS at 295-315 bp on
     2026-10-19 (CNBC, as reported by Wealth Professional on 10-20): about 2.5-2.8 times the model. Dealing USD 25M on the model would
     understate the upfront by about USD 1.9M;
   - the dealing-level guard: how to set a negotiated mid or confirm dealing at the MODEL, and what a change does to open contracts;
   - the known blind spots (3.4);
   - the unanswered questions of section 11, first Q1 (the debt rule), Q13 (the order) and Q14 (the guard);
   - whether the research's DTCC observation of ORCL, made before the terms decision, may be quoted is the orchestrator's call; this
     plan does not quote it.
7. Booking the V3 ORCL USD 25M buy protection is the V3 workflow's call after this deploy, not this build's. It books only at **the
   owner's own negotiated mid**, entered by him or given by him in words and labelled as his level, never at the bare model: the V3
   workflow never sets a MODEL confirmation for ORCL, and without a level the guard refuses the deal (V3: "Do not fabricate an executable
   CDS quote if reference data is missing"). If he gives no level, the ORCL line stays unexecuted with that reason in words. `docs\INSTITUTIONAL_V3_PLAN.md` line 4.4 says "per the model at booking"; the V3
   workflow should follow this step instead.

---

## 10. Hours (wall clock)

| Step | Hours |
|---|---|
| 0. Prep (9.1) | 0.25 |
| 1. Pool: B1 5.25-5.75, B2 4-5, B3 4-5, B4 4.5-5.5 (about 19.5 builder-hours; tier-1 hooks at 45 min) | 5.25-5.75 |
| 2. Integration | 1-1.5 |
| 3. Verification V1-V6 | 2-2.5 |
| 4. Fixes (at most 2 rounds) | 1-2 |
| 5. Gate and deploy | 1-1.5 |
| **Total** | **10.5-13.5** |

- The pool is 5.25-5.75 h, a little over the requested 3-5 hours. The critic's fixes add about 0.75 h to B1, the longest package (the
  dealing-level guard, the firm coupon / maturity / tier, the basis-effect route, the equity scenarios), and about 0.5 h to each of the
  others (B2: R6, the parts rule, the flags; B3: the quadrature, the persisted splits, `spread_at`; B4: the two dialogs and the blocker).
- Steps 2-5 are the project's standing verify and deploy rules.
- **Start time and order (Q13).** Both timelines start from the FX plan's own assumption that the 1.0.6 gate ends overnight, with a pool
  from about 02:00 EDT on Thursday 10-29:
  - **1.0.6.1 first (the default, FX_CCS_PLAN D1).** Its gate falls about 16:00-22:00 EDT on 10-29, and the FX trades book on Friday 10-30
    between 08:00 and 12:00 ET. 1.0.7 starts at that gate and takes 10.5-13.5 h, so it deploys on **Friday 10-30**: before 08:00 ET if the
    FX gate is early, else after the FX booking window (a deploy restarts the terminal and must not fall inside it).
  - **1.0.7 first.** 1.0.7 deploys about 12:30-15:30 EDT on **Thursday 10-29**, the "likely Thursday afternoon" the owner was told.
    1.0.6.1 then needs 14-18.5 h to its gate (about 02:30-10:00 EDT on Friday). Its dry run and booking (1-1.75 h) fit Friday's 08:00-12:00
    ET window only if nothing slips; otherwise the FX trades book on **Monday 11-02**.
  - Either way, B3 and the core of B2 (new files and their unit tests only: no hook, no server, no browser, no write to the repository)
    may start in a scratch folder outside the repository right after the 1.0.6 gate, if the orchestrator's memory check allows two light
    agents beside the running pool. They are copied in at integration and re-checked against the base commit. This moves B2's and B3's
    live checks before the gate and halves the post-gate pool; it does not shorten the pool, because B1 is the longest package.
  - The orchestrator restates the date to the owner once he has answered Q13 and the gate times are known.

---

## 11. Owner questions (the build proceeds on the defaults)

| # | Question | Default |
|---|---|---|
| Q1 | Calibration and the debt rule | **CreditGrades + the AAA OAS floor on debt-only (3.2)**, labelled MODEL and **not validated**: the first draft's comparison was weak (most investment-grade names at the floor; the floor alone almost as good), and names whose CDS trades are missed by about 2 times either way, ORCL most of all (about 113-116 bp against 295-315 bp in public news, 3.4). Alternative: the CreditGrades document's own debt (adds 50 % of other liabilities, 0 % of accounts payable), which widens leveraged names (ORCL about 149 bp, VZ 79, KBH 150, KSS 812; Appendix B). B3's report shows both on 20 names; you choose; nothing is tuned in the build |
| Q2 | Volatility | **1000-day realised** (CreditGrades' own finding; reproducible for any past VM date). Cboe 1-year implied vol is deferred (12.4) |
| Q3 | Banks, insurers, finance companies | **Offered, LIMITED**, on borrowings-only debt, with the CreditGrades caveat on screen. Alternatives: not offered; or offered only with a negotiated mid |
| Q4 | Distressed names (model survival now under 95 %, or raw over 2,000 bp) | **A new deal needs your negotiated mid**; marks stay on the model, labelled |
| Q5 | IM | **The assumed spread-bucket schedule** (4-30 %, by broker group). Alternative: the BCBS-IOSCO standardised grid by maturity (2 / 5 / 10 %) |
| Q6 | Initial payment settlement | **T+3 business days** (bilateral standard). Alternative: T+1 as the CDX |
| Q7 | Coverage | **US 10-K / 10-Q filers only.** Foreign private issuers (20-F / 40-F, ADR ratios, IFRS) later |
| Q8 | Minimum reported debt | **USD 100M** (about 1,650-1,800 names; 1,663 on CY2026Q2I alone). Removing it gives about 2,400 (2,388 on CY2026Q2I), many with no real CDS market |
| Q9 | Single-name concentration warning | **WARN when one name's jump-to-default loss is over 10 % of NAV** |
| Q10 | Real traded single-name CDS prints beside the model? | **No.** DTCC's Terms of Use prohibit automated extraction and compiling the content into a database (orchestrator, 2026-10-28 18:32-18:35 ET), and no other free source with permissive terms is known. Your negotiated mid is the way to bring in a market level you see (3.5, 12.1) |
| Q11 | Default coupon | **100 bp unless the MODEL spread's bucket, before your negotiated basis, is BB-like or worse (then 500 bp)**. A wide negotiated mid never moves an investment-grade name to 500 (ORCL is ICE-eligible at 100 only). You can override per trade; the coupon is fixed when the dealer quotes |
| Q12 | Show the CDS IM schedule in Settings > Prime brokers as well? | **No**; it is edited on the CDS desk like the CDX terms |
| Q13 | Which build first: 1.0.6.1 (your FX / cross-currency trades) or 1.0.7 (single-name CDS)? | **1.0.6.1 first** (the FX plan's D1): the FX trades book Friday 10-30 08:00-12:00 ET and CDS deploys on Friday 10-30. CDS first: CDS about 12:30-15:30 EDT on Thursday 10-29; the FX trades then book Friday morning only if nothing slips, else Monday 11-02 (section 10) |
| Q14 | Dealing on the bare model | **A first deal per name and PB account needs your negotiated mid or your one-click confirmation "deal at the MODEL - no market level known"** (3.5). Alternative: no guard (the first draft), which would deal ORCL at about 116 bp while public news put it near 300 bp |

---

## 12. Deferred, with reasons

1. **Traded single-name prints beside the model: blocked by DTCC's terms, not planned (Q10).** DTCC's Terms of Use prohibit automated
   extraction and compiling the content into a database, and no other free source with permissive terms is known. Should the owner ever
   license a feed, the first draft's notes apply: SEF par-spread prints of the on-the-run maturity only, mapped by CIK through SEC names;
   unsigned upfronts need a sign rule.
2. **Bond-implied spreads from SPDR ETF holdings.** The SSGA terms restrict redistribution, and the CDS-bond basis is -90..+52 bp, so it needs
   calibration first.
3. **Per-company ratings.** No free machine-readable source; the 17g-7 apps are manual downloads only.
4. **Cboe implied volatility.** It needs the symbol-directory pre-filter, because of the `CboeChain` 403 pitfall, and an end-of-day snapshot,
   because IV of a past date cannot be refetched.
5. **The filing-instance XBRL fallback** (`*_htm.xml`, 3-18 MB, a streaming parse) for joint filers (NEE, DUK, PLD) and for companyfacts gaps.
   Until then those names are labelled with their older balance sheet.
6. **Implied-vol CS01 and volatility shocks.** (Equity-price scenarios through the model are in 1.0.7, 5.2.)
7. **iTraxx Europe Main** (owner V3). It is an index, not a single name, and is not offered.
8. **Restructuring clauses, subordinated / LCDS, sovereigns and munis, CDS options, off-standard tenors, auctions and DC rulings.**
9. **ICE Clear Credit's per-name coupons as the default coupon** (Oracle 100 only, KBH 500 only). The list is a legacy .xls that needs the
   research's stdlib reader, and its terms of use are not yet checked. Until then the default follows the MODEL bucket (2.1) and the owner
   can override it.

---

## Appendix A: CreditGrades test vectors

Float64 with `math.erfc`. L-bar 0.5, lambda 0.3, R 0.40. Every number is from `cg_vectors.py`.

| Vector | Inputs | LD | sigma_A | d | A_T | P(0) | P(T) | xi | z | G(xi) | G(T+xi) | H | c* | raw bp |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| V1 | S 50, sigma 0.35, D 40, T 5, r 0.04 | 20 | 0.25 | 3.82960999297 | 0.634428877022 | 0.999985261119 | 0.935395466934 | 1.44 | 1.23693168769 | 1.39793633687e-05 | 0.0531499540613 | 0.0562864701075 | 0.00759707452873 | 75.97 |
| V2 | S 300, sigma 0.30, D 5, T 5, r 0.04 | 2.5 | 0.297520661157 | 132.395088328 | 0.729789503266 | 1 | 0.999999999767 | 1.01673611111 | 1.07413473647 | 1.35673383664e-58 | 1.85061363504e-10 | 1.92742848471e-10 | 2.55191017886e-11 | 0.00 |
| V3 | S 12, sigma 0.55, D 30, T 5, r 0.04 | 15 | 0.244444444444 | 1.96951371067 | 0.62351057096 | 0.966800173471 | 0.621512807108 | 1.50619834711 | 1.26049314762 | 0.0316766906791 | 0.328638780548 | 0.315403389455 | 0.0586935300167 | 586.94 |
| V4 | V1 at T 3 | 20 | 0.25 | 3.82960999297 | 0.526782687643 | 0.999985261119 | 0.979444756474 | 1.44 | 1.23693168769 | 1.39793633687e-05 | 0.0178034406123 | 0.0188442196555 | 0.00402498076378 | 40.25 |
| V5 | S 2, sigma 0.90, D 40, T 5, r 0.04 | 20 | 0.0818181818182 | 1.20359171208 | 0.351384510729 | 0.413606633008 | 0.347385350754 | 13.4444444444 | 3.49293820214 | 0.483130472672 | 0.518433832295 | 0.060446332825 | 0.225821525599 | 2,258.22 (distressed: P(0) < 0.95) |

V2 does not take the ln d / A_T >= 8 shortcut (ln d / A_T = 4.886 / 0.7298 = 6.69). The full formula gives 2.55e-7 bp, which quantizes to
0.00 bp.

**The quadrature path** (3.1: P(0) < 0.95, or the closed form fails). Composite Simpson, 2,000 steps; every number from
`<scratchpad>\cds-design\rev\simpson_vectors.py`. Where the closed form is valid it agrees to 12 significant digits (V1: I 4.44654389858,
c* 0.00759707452873; V3: I 3.56362838512, c* 0.0586935300168), and 20,000 steps change nothing at that precision.

| Vector | Inputs | sigma_A | d | P(0) | P(T) | I | c* | raw bp | The closed form |
|---|---|---|---|---|---|---|---|---|---|
| V5 | as above | 0.0818181818182 | 1.20359171208 | 0.413606633008 | 0.347385350754 | 1.71863075879 | 0.225821525599 | 2,258.22 | 2,258.22: agrees; the module takes the quadrature value |
| V6 | S 0.01, sigma 2.0, D 40, T 5, r 0.04 | 0.000999500249875 | 1.09472137085 | 0.203664260132 | 0.203657657495 | 0.922937213944 | 0.517700475474 | 5,177.00 | raises OverflowError (e^(r xi), xi about 9.0e4) |
| V7 | S 0.05, sigma 1.5, D 40, T 5, r 0.04 | 0.00374064837905 | 1.09690971941 | 0.208201857772 | 0.20810748491 | 0.943308095548 | 0.503685073037 | 5,036.85 | 5,024.92: cancellation, never used |

**Calibrated, with the floor 42.00 bp**

| Vector | Model spread (bp) | Bucket | Why |
|---|---|---|---|
| V1 | 117.97 | BBB | \|ln(117.97/109)\| = 0.079 < BB 0.461 |
| V2 | 42.00 | AAA | |
| V3 | 628.94 | B | \|ln(628.94/322)\| = 0.669 < CCC 0.742 |
| V4 | 82.25 | A | 0.119 < BBB 0.282 |
| V5 | 2,300.22 | distressed | P(0) 0.414 < 0.95: quadrature, equal to the closed form here |
| V6 | 5,219.00 | distressed | quadrature; the closed form raises |
| V7 | 5,078.85 | distressed | quadrature (the closed form would give 5,066.92) |

**Equity shocks of V1** (`spread_at`, floor 42.00; 5.2): S x 0.7 = 35: raw 118.31, model 160.31 (BB); S x 1.3 = 65: raw 51.85, model 93.85
(BBB); S x 1.5 = 75: raw 41.09, model 83.09 (A).

**Realised-vol vector.** Closes 100, then alternately x1.01 and x0.99 ten times (100, 101, 99.99, 100.9899, 99.980001, ...). The returns
alternate 0.0099503309 and -0.0100503359; the mean is -0.0000500025; the sample sd is 0.01054127692; annualised that is 0.1673375834, stored
0.1673.

## Appendix B: design-time checks: what they show and what they cannot

**1. The first draft's comparison (a record; not reusable).** 55 names; traded levels from 7 DTCC files the research's script downloaded
before the DTCC terms decision (1); inputs from the unverified `universe.json` snapshot (PFE debt 4.0B, F 291M, ORCL 0 and so dropped), with
11 names on Cboe implied volatility (3.4). Aggregates only; the per-name traded levels of the first draft are not repeated. Its working
files stay in the scratchpad (`<scratchpad>\cds-design\calib_rows.json`, `<scratchpad>\cds-research\dl\dtcc\`): never copied into the
repository, opened by no builder; the orchestrator may delete them.

| Subset | Names | Median abs. error | Mean abs. error | Within +/-35 % | Mean log bias |
|---|---|---|---|---|---|
| All | 55 | 16.8 bp | 25.2 | 27 | +0.083 |
| At least 2 prints | 31 | 18.0 | 27.8 | 13 | |
| At least 4 prints | 12 | 16.0 | 29.1 | 7 | |
| The constant 42 bp floor alone, all | 55 | 22.0 bp | 29.2 | | -0.178 |

**2. The shipped model on real 2026-10-28 inputs** (`four_names.py`, `orcl_model.py`; SEC companyfacts and Yahoo files of 18:54 ET; the
latest dei cover shares; T = 1,879 / 365 to 2031-12-20; r 4.76 %; R 40 %; floor 42 bp; model = floor + raw). These rows are B3's regression
vectors (task 7). Liabilities for the variant: `Liabilities`, else `LiabilitiesAndStockholdersEquity` less the equity (ORCL and VZ report
no `Liabilities` total).

| Name | S | sigma (1000 d) | Shares | Balance sheet | Debt by 3.2 | Model bp, debt-only | Liabilities; accounts payable | CG Appendix B debt | Model bp, variant |
|---|---|---|---|---|---|---|---|---|---|
| ORCL | 144.54 | 0.4577 | 3,023,736,000 | 2026-08-31 | 125,337,000,000 (R6) | 112.54 BBB | 236.06B; 11.06B | 175.17B | 149.08 BB |
| ORCL (10-K) | 144.54 | 0.4577 | 3,023,736,000 | 2026-05-31 | 129,541,000,000 (R1) | 115.74 BBB | | | |
| VZ | 43.18 | 0.2371 | 4,154,775,202 | 2026-06-30 | 165,231,000,000 (R4b; the first draft's 143,448,000,000 gave 54.38) | 59.89 AA | 304.99B; 20.42B | 224.90B | 78.72 A |
| KBH | 46.17 | 0.3619 | 60,762,736 | 2026-08-31 | 2,109,145,000 (R6; the first draft: `NO_DEBT`) | 124.15 BBB | 3.17B; none tagged | 2.64B | 149.73 BB |
| KSS | 23.34 | 0.6834 | 113,376,445 | 2026-08-01 | 1,325,000,000 (R3) | 547.33 B | 9.24B; 1.42B | 4.57B | 812.29 CCC |

- ORCL against public news, 295-315 bp on 2026-10-19 (Appendix E): the model is about 2.5-2.8 times too tight, and the variant about 2
  times.
- At ORCL's model mid (BBB, GS half-spread 3.00 bp) USD 25M of 5Y protection deals at about 118.74 bp with an upfront of about USD 0.21M
  paid; at a mid of 300-315 bp (tier B) the upfront is about USD 2.0-2.2M and CS01 about USD 9.5-10K a bp (a planning approximation on a
  flat curve; the app computes the exact amounts).

## Appendix C: the worked deal (money test numbers)

Computed on the existing `swaps_cds` pure functions. B1's tests re-derive them by hand.

**Setup**
- Trade Thu 2026-10-29. On-the-run roll 2026-09-21, 5Y maturity 2031-12-20, 21 coupon periods. The first is 2026-09-21 -> 2026-12-21, paid
  2026-12-21; the last is 2031-09-22 -> 2031-12-21, paid 2031-12-22.
- The CDX tests' curve: DF = 1 / 1.02^k at 0.5k years.
- Buy protection, USD 5,000,000.00, coupon 100 bp, R 40 %.
- Model mid 65.47 (stub), tier A_OR_BETTER (GS: IM 4 %, half-spread 2.00, multiplier 1).

**Accruals, rebate and first coupon**
- Accrual start 2026-09-21: 20 Sep was a Sunday.
- Rebate: 39 days (09-21..10-29), 5,000,000 x 1 % x 39 / 360 = 5,416.666... = 5,416.67.
- First coupon: 91 days = 12,638.89.

**Pricing**

| | Buyer | Seller |
|---|---|---|
| Spread used | dealt 67.47: RPV01 4.5669965459, lambda 0.0112450000; upfront (buyer view) = 5,000,000 x (67.47 - 100) bp x 4.5669965459 = -16,265 x 4.5669965459 = -74,282.20 (received) | dealt 63.47: RPV01 4.5745045516; upfront (buyer view) -83,553.33, so the seller pays 83,553.33 |
| At mid 65.47 (RPV01 4.5707484462) | mark0 -78,913.97 (= -17,265 x 4.5707484462) | mark0 +78,913.97 |
| Cost of dealing | -4,631.77 | -4,639.36 |
| CS01 | +2,316.83 | -2,316.83 |
| Initial payment | 74,282.20 + 5,416.67 = 79,698.87 receivable, due T+3 = Tue 2026-11-03 | 83,553.33 + 5,416.67 = 88,970.00 payable |
| NAV at booking | +79,698.87 (1200) - 5,416.67 (2340) - 78,913.97 (MV) = -4,631.77 | -88,970.00 + 5,416.67 + 78,913.97 = -4,639.36 |

**Premium nights (buyer):** cumulative to_c(N x C x days / 360) from 39 days:

| Night | Days | Cumulative | Posted |
|---|---|---|---|
| 10-30 | 40 | 5,555.56 | 138.89 |
| 10-31 | 41 | 5,694.44 | 138.88 |
| 11-01 | 42 | 5,833.33 | 138.89 |
| 11-02 | 43 | 5,972.22 | 138.89 |
| 11-03 | 44 | 6,111.11 | 138.89 |

**Credit event (buyer):** event 11-02, entered 11-04.
- Accrued 5,972.22; late 138.89; protection 3,000,000.00; net 2,994,027.78, due 11-03.
- Entry: Dr 2340 6,111.11, Cr 4453 138.89, Cr 4450 3,000,000.00, Dr 1200 2,994,027.78. Debits 3,000,138.89 = credits 3,000,138.89.
- Then: cost basis Dr 2345 / Cr 4450 74,282.20; IM Dr 1010 / Cr 1330 200,000.00.
- Realised 4450 +3,074,282.20; premium 4453 net 555.55 (5,972.22 - 5,416.67).

**JTD at booking:** buyer 3,000,000.00 + 78,913.97 = 3,078,913.97; seller -3,078,913.97.

## Appendix D: hook-edit catalogue (anchors as of the 1.0.6 tree at 17:30-18:20 ET; re-anchor at the base commit, 1.0.6.1)

Every edit is idempotent. The marker in new text is `CDS-107` (Python `# CDS-107`, JS `/* CDS-107 */`). Paths are relative to the finsim
folder.

**hooks_b1.py**

| Id | File | Anchor (exact, once) | Change |
|---|---|---|---|
| H1-1 | `finsim/terminal/db.py` | `kind TEXT NOT NULL CHECK (kind IN ('IRS','TRS','XCCY','EQ_OPTION','FX_OPTION','EQ_FORWARD','FX_FORWARD','CDX')), terms_json TEXT NOT NULL,` | add `,'CDS'` after `'CDX'` |
| H1-2 | `finsim/terminal/db.py` | `OTC_KINDS = ("IRS", "TRS", "XCCY", "EQ_OPTION", "FX_OPTION", "EQ_FORWARD", "FX_FORWARD", "CDX")` | add `, "CDS"` |
| H1-3 | `finsim/terminal/prime.py` | `OTC_LIVE = ("IRS", "TRS", "XCCY", "EQ_OPTION", "FX_OPTION", "EQ_FORWARD", "FX_FORWARD", "CDX")` | add `, "CDS"` |
| H1-4 | `finsim/terminal/prime.py` | `"CDX": "CDS index (CDX.NA.IG / CDX.NA.HY 5Y)"}` | `"CDX": "CDS index (CDX.NA.IG / CDX.NA.HY 5Y)", "CDS": "single-name CDS (US reference entities)"}` |
| H1-5 | `finsim/terminal/server.py` | `"swaps_cds": "products.swaps_cds",` | append `"credit_entities": "credit_entities", "cds_model": "products.cds_model", "swaps_cds_names": "products.swaps_cds_names",` (that order) |
| H1-6 | `finsim/terminal/products/swaps.py` | `CDS_KINDS = ("CDX",)` | append the lines `SINGLE_CDS_KIND = "CDS"` and `SINGLE_CDS_MODULE = "swaps_cds_names"` |
| H1-7 | same | `"CDX": "CDS index (CDX)"}` (KIND_LABELS) | `"CDX": "CDS index (CDX)", "CDS": "single-name CDS"}` |
| H1-8 | same | `    if k in CDS_KINDS:\n        return getattr(services, CDS_MODULE, None)\n` (`_module_for`) | prepend `    if k == SINGLE_CDS_KIND:\n        return getattr(services, SINGLE_CDS_MODULE, None)\n` |
| H1-9 | same | `if (row["kind"] in CDS_KINDS and r is not None` (vm_keys) | `if (row["kind"] in CDS_KINDS + (SINGLE_CDS_KIND,) and r is not None` |
| H1-10 | same | `    elif kind in CDS_KINDS:\n        up = terms.get("upfront_c")` (`_display_fields`) | prepend a branch: `elif kind == SINGLE_CDS_KIND:` takes `fn = getattr(getattr(services, SINGLE_CDS_MODULE, None), "display_fields", None)`; `out.update(fn(row, terms, inputs))` when callable, else `units=n // 100, unit_label="USD notional"` |
| H1-11 | same | `FIRM_KINDS = ("IRS", "TRS", "CDX")` | add `, "CDS"` |
| H1-12 | same | `    if k == "XCCY":\n        r, p = str(t.get("recv_ccy")` (`rfq_describe`) | prepend: `if k == SINGLE_CDS_KIND:`, a guarded import of `swaps_cds_names` (ImportError gives the generic text), then `return _cdsn.rfq_describe(t, pv)` |
| H1-13 | same | `    if k == "XCCY":\n        legs = pv.get("legs") or {}` (`_quote_terms`) | prepend: `if k == SINGLE_CDS_KIND:` firm `{"dealt_spread_bp": pv.get("deal_spread_bp"), "coupon_bp": pv.get("coupon_bp"), "maturity_date": pv.get("maturity_date"), "tier": pv.get("tier")}` (all four fixed at quote time, 2.1), compare `{"deal_spread_bp": ..., "coupon_bp": ..., "maturity_date": ...}`, words `f"{pv.get('entity_label')} {bp} bp (MODEL mid {pv.get('mid_spread_bp')} bp); {pv.get('upfront_direction')} upfront"` |
| H1-14 | same | `    elif k == "CDX":\n        chk("deal_spread_bp", firm.get("dealt_spread_bp"), terms.get("deal_spread_bp"))` (`_quote_moved`) | append an `elif k == SINGLE_CDS_KIND:` branch checking `deal_spread_bp`, `coupon_bp` and `maturity_date` against the firm terms (equal by construction once the booking uses them) |
| H1-15 | same | `type IN ('CDX_CREDIT_EVENT','OTC_TERMINATED',` and `        if e["type"] == "CDX_CREDIT_EVENT":` (contract_timeline) | add `'CDS_CREDIT_EVENT'` to the IN list; prepend `if e["type"] == "CDS_CREDIT_EVENT":` adding step CREDIT_EVENT with words "simulated credit event (bankruptcy / failure to pay) of <entity> at R % recovery - net X settling D; the contract closed", then `continue` |
| H1-16 | same | `    open_rfqs = rfq_rows(services, scope)` and `"credit_event_offered": callable(getattr(cds, "h_credit_event", None)) if cds is not None else False,` (`_deriv_otc`) | before the first, merge `services.swaps_cds_names.credit_events_of(services, [CDS ids])` into `credit` (errors kept by name); after the second, add `"credit_event_offered_cds": callable(getattr(getattr(services, SINGLE_CDS_MODULE, None), "h_credit_event", None)),` |
| H1-17 | same | `allowed = ("IRS", "TRS", "XCCY", "CDX")` (otc_preview route) | add `, "CDS"` |
| H1-18 | `finsim/terminal/lifecycle_jobs.py` | `"swaprcv": ("swaps", "otc_contracts", "contract_id", ("IRS", "XCCY", "CDX")),` and the `"swappay"` line | add `"CDS"` to both tuples |
| H1-19 | `finsim/terminal/products/swaps_cds.py` | `"Off-the-run CDX series, other tenors, tranches, single-name CDS and index options"` | `"Off-the-run CDX series, other CDX tenors, tranches and index options"` (single-name CDS now has its own rows) |

**hooks_b2.py**

| Id | File | Anchor | Change |
|---|---|---|---|
| H2-1 | `finsim/terminal/instruments.py` | `" AND c.kind IN ('TRS','XCCY','EQ_OPTION','EQ_FORWARD','FX_OPTION','FX_FORWARD') ORDER BY c.contract_id"` and `if kind in ("TRS", "EQ_OPTION", "EQ_FORWARD") and terms.get("underlying_id"):` | add `'CDS'` / `"CDS"` |
| H2-2 | `finsim/terminal/accounting.py` | `"EQ_FORWARD": "OTC forward", "FX_FORWARD": "OTC forward"}` (OTC_ASSET_CLASS) | add `"CDS": "OTC CDS"` |
| H2-3 | `finsim/terminal/risk.py` | `"Credit derivatives (CDX notional)", "OTC swaps (notional)",` (BUCKET_ORDER) | insert `"Credit derivatives (single-name CDS notional)", ` after the CDX bucket |
| H2-4 | same | `"FX_FORWARD": "FX forward", "CDX": "CDX"}` (OTC_KIND_LABEL) | add `"CDS": "single-name CDS"` |
| H2-5 | same | `    "irs": "IRS / cross-currency swaps: measured by DV01` (DEFINITIONS) | prepend `"cds"` (the single-name credit notional with its sign; CS01 and jump-to-default in the single-name block) and `"jtd"` ("jump to default: what the contract would gain or lose if the company defaulted now at 40 % recovery = +/- (1 - 40 %) x notional less the contract's value now") |
| H2-6 | same | `    if kind == "CDX":\n        return f"{spec.get('index')` (contract_label) | prepend: `if kind == "CDS":` return `f"{spec.get('ticker') or und or 'CDS'} {spec.get('tenor_years') or '?'}Y CDS {'sell' if spec.get('receiver') else 'buy'} protection"` |
| H2-7 | same | `    if kind == "CDX":\n        v = base(p.notional_c)` (otc_exposure) | prepend the same branch for `"CDS"` with basis "single-name credit notional: protection bought = short credit (negative), sold = long credit" |
| H2-8 | same | `        elif swap_kind == "CDX":` (`_pos_risk`) | prepend the `"CDS"` branch: bucket "Credit derivatives (single-name CDS notional)", headline False, signed notional |
| H2-9 | same | `    credit_derivatives: Optional[dict] = None`; `    credit = _credit_derivatives(services, rows, base_c)`; `    view.credit_derivatives = credit`; `def _unpriced_warning(` | add the field `single_name_cds: Optional[dict] = None`; after the call, `single = _single_name_cds(services, rows, base_c)` and append `single["warnings"]` through `_w`; `view.single_name_cds = single`; a new function `_single_name_cds` (the `_credit_derivatives` pattern, calling `services.swaps_cds_names.risk_block`) inserted before `_unpriced_warning` |
| H2-10 | `finsim/terminal/api_views.py` | `"cdx_long_credit_c": 0, "cdx_short_credit_c": 0,` and `            elif k == "CDX" and x.get("exposure_c") is not None:` and `("market_value", "exposure", "trs", "cdx", "irs", "futures")` | add `"cds_long_credit_c": 0, "cds_short_credit_c": 0,`; prepend the `"CDS"` branch; add `"cds", "jtd"` to the definitions tuple |
| H2-11 | same | `"cdxcpn": "CDX coupon",` (LINK_WORDS); `CODE_ACRONYMS = {"FX", "OTC", "CDX",` | add `"cdscpn": "CDS coupon",`; add `"CDS", "JTD",` |
| H2-12 | `finsim/terminal/blotters.py` | `"CDX_CREDIT_EVENT": "credit event"}`; `'OTC_EXPIRED','CDX_CREDIT_EVENT')"`; `    if k == "IRS":\n        pay = bool(terms.get("pay_fixed"))` (`_otc_describe`) | add `"CDS_CREDIT_EVENT": "credit event"`; add `'CDS_CREDIT_EVENT'` to the IN list; prepend a `"CDS"` branch returning `(f"{ticker} {tenor}Y CDS", side words, None, deal_spread_bp, "spread bp")` |

**hooks_b4.py**

| Id | File | Anchor | Change |
|---|---|---|---|
| H4-1 | `finsim/terminal/api.py` | `                  "components/cdsticket.js",` (PLANNED_STATIC) | add `"components/cdsnames.js",` |
| H4-2 | same | `"dashboard": "F1-P1", "blotters": "F1-P2",` (PACKAGES) | prepend `"credit_entities": "CDS-B2", "cds_model": "CDS-B3", "swaps_cds_names": "CDS-B1",` |
| H4-3 | `finsim/terminal/static/index.html` | `<script src="/static/components/cdsticket.js" data-optional="1"></script>` | append the same line for `components/cdsnames.js` |
| H4-4 | `tests/terminal_fixture_server.py` | `"components/cdsticket.js",                                    # CDX (CDS index) ticket` (OPTIONAL_STATIC, line 67) | append the entry `"components/cdsnames.js",` with the comment `# single-name CDS desk (CDS-107)` |
| H4-5 | `finsim/terminal/static/pages/markets_otc.js` | `{ key: 'credit', label: 'Credit (CDX)' }];` | add `, { key: 'cds', label: 'Single-name CDS' }` |
| H4-6 | same | `'CDX_PROXY', 'CDX_DEAL', 'CDX_COUPON', 'ELIGIBILITY'];` | add `'CDS_MODEL', 'CDS_DEAL', 'CDS_COUPON', 'CDS_CREDIT'` before `'ELIGIBILITY'` |
| H4-7 | same | `.otc-desk.sub-credit .otc-swaps .swapticket > :not(.contracts)` (CSS) and the `cdsBox` creation / append, the `paintSub` credit branch, `paintBlocked`, `unmount` | add the `sub-cds` selector; a `cdsnBox`; in `paintSub` mount `T.mountOptional('cdsnames', cdsnBox, {account_id, blocked, note: false, onBooked: refresh})` when `state.sub === 'cds'`; setBlocked and unmount like `cdsH` |
| H4-8 | `finsim/terminal/static/components/swapticket.js` | `    if (row && row.kind === 'CDX') {` (sideText), `    if (c.kind === 'CDX' && (d.index_name` (kindShort), `    if (c.kind === 'CDX') {\n      return 'spread now '` (nowText) | prepend in each: `if (row && row.kind === 'CDS' && T.components.cdsnames) { return T.components.cdsnames.sideText(s, row); }` and the same for `kindShort(c)` / `nowText(c)` |
| H4-9 | `finsim/terminal/static/pages/derivatives.js` | `    if (c.kind === 'CDX') { return 'dealt '` (dealtNow); `    if (c.kind === 'CDX') { var d = c.detail` (sensitivity); `      if (c.kind === 'CDX' && st.data && st.data.credit_event_offered) {` (actions); `    if (c.kind === 'CDX') {\n      var ev = d.credit_events` (drawer) | prepend the CDS dispatch to `T.components.cdsnames` helpers; the button shown when `st.data.credit_event_offered_cds`; the dialog `T.components.cdsnames.creditDialog(st, c, function () { load(st); })` |
| H4-10 | `finsim/terminal/static/pages/cash.js` | `cdxcpn: 'CDX coupon',` and `/^(SWP|OTC|CDX)-/` | add `cdscpn: 'CDS coupon',`; regex `/^(SWP|OTC|CDX|CDS)-/` |
| H4-11 | `finsim/terminal/static/pages/positions.js` | `    if (k === 'CDX') { return q < 0 ? 'buy protection' : 'sell protection'; }` | add the same line for `'CDS'` |
| H4-12 | `finsim/terminal/static/pages/portfolio.js` | `      if (T.num(x, 'irs_dv01')) {` | prepend: `if (T.components.cdsnames) { var cf = T.components.cdsnames.footer(x, def); if (cf) { ex.push(cf); } }` |
| H4-13 | `finsim/terminal/static/pages/risk.js` | `      /* counterparties */` (after the CDX panel) and `      var cp = r.counterparties \|\| {};` | create the single-name panel through `T.components.cdsnames.riskPanel(body, {panel, gridIn, money, pct, nameOf})` when present; update it with `r.single_name_cds` (hidden when null) |
| H4-14 | `finsim/terminal/static/core.js` | `var CODE_ACRONYMS = { FX: 1, OTC: 1, CDX: 1,` | add `CDS: 1, JTD: 1,` |

## Appendix E: sources

**Primary**
- CreditGrades Technical Document, RiskMetrics Group (ed. C. Finger), May 2002: sections 2.1-2.2 (model, L-bar 0.5, lambda 0.3), 3 (the 1000-day
  volatility estimator), Appendix B (debt per share), and the note on banks and finance firms. https://support.msci.com/documents/10199/dd31bcce-6fe3-47b7-9fb7-10c4c8f750ba
- SEC EDGAR APIs: `company_tickers(_exchange).json`, XBRL frames, `companyfacts`, `submissions`.
- FRED ICE BofA OAS series `BAMLC0A1CAAA` ... `BAMLH0A3HYC`.
- DTCC PPD SEC cumulative credits, 2026-10-21..27: downloaded by the research's script before the DTCC terms decision; used only by the
  first draft's comparison; not used, reused or reproduced since (1, 3.4).
- ICE Clear Credit, list of clearing-eligible North American single names (research download, 2026-10-28): 337 names with their eligible
  coupons (Oracle Corporation: 100 bp only).
- Yahoo chart daily bars.
- ISDA, "Amending when single name CDS roll to new on-the-run contracts" (2015) and its FAQ:
  https://www.isda.org/2015/07/07/amending-when-single-name-cds-roll-to-new-on-the-run-contracts/ ,
  https://www.isda.org/a/vGiDE/amend-single-name-on-the-run-frequency-faq-revised-as-of-12-10.pdf

**Secondary**
- BCBS-IOSCO standardised initial margin schedule, BIS d499 Appendix A, as summarised in the FINOS CDM documentation:
  https://cdm.finos.org/docs/standardized-functions , https://www.bis.org/bcbs/publ/d499.pdf

**News, for the owner report only** (never fetched by the app; quoted with its source):
- Wealth Professional, "Oracle's default insurance hits high-yield levels as AI debt mounts", 2026-10-20, citing CNBC: Oracle's 5-year CDS
  "stood at 295 basis points on Monday morning after briefly touching 300 on Thursday", and a later CNBC report put it at 315 bp on that
  Monday (2026-10-19). https://www.wealthprofessional.ca/investments/equity-markets/oracles-default-insurance-hits-high-yield-levels-as-ai-debt-mounts/393817
- Yahoo Finance, "Oracle's Credit Risk Is At an All-Time High...", 2026-04-10: about 198 bp then.
  https://finance.yahoo.com/markets/stocks/articles/oracles-credit-risk-time-high-021100593.html

**Research files:** `<scratchpad>\cds-research\` (scripts, outputs, downloads). **Design checks:** `<scratchpad>\cds-design\`; the
revision's in `<scratchpad>\cds-design\rev\`.

---

## Critic answers

One line per finding of the independent critic, each verified before it was fixed or answered (scripts in `<scratchpad>\cds-design\rev\`).

- CDS-C-01 (critical) ACCEPTED, fixed. Verified with permitted sources only: `universe.json` has ORCL `total_debt` 0, so `calib_eval.py` dropped it silently; on real 10-28 inputs (Yahoo close 144.54, 1000-day sigma 0.4577, cover shares 3,023,736,000, r 4.76 %, T 5.148) the shipped model gives 115.74 bp with the 10-K's R1 129.541B and 112.54 bp with the 2026-08-31 10-Q under the new R6, both BBB-like; public news put ORCL 5Y at 295-315 bp on 2026-10-19 (CNBC via Wealth Professional, Appendix E), and USD 25M pays an upfront of about USD 0.21M on the model against about 2.0-2.2M at 300-315 bp. Fixed: the plain statement in 0, 3.4, Q1 and the 9.5 step 6 report; the dealing-level guard (2.1, 3.5, 3.6, B1 task 3 step 4, tests 15 and 18): a deal needs, per (PB account, CIK), the owner's negotiated mid or his explicit "deal at the MODEL - no market level known" confirmation, stored in `cds_basis` (`level_kind`), else the preview is not bookable and quote / booking answer 409 `DEALING_LEVEL_UNCONFIRMED` words first; 9.5 step 7 books the V3 ORCL line only at the owner's own negotiated mid, never the bare model; the V3 walk expects the guard on the ORCL USD 25M preview. Not added: the USD 10M size rule, because the confirmation already covers every size and a size rule would make the owner type a level he may not have (Q14 keeps the guard itself his choice). The research's DTCC observation is not quoted; the orchestrator decides.
- CDS-C-02 (major) ACCEPTED, fixed. Verified: BUILD_LOG 18:32 EDT and the 18:35 ET note at the top of OWNER_INSTITUTIONAL_V3 rule DTCC out, and the first draft read BUILD_LOG only to 18:14 and used DTCC in the five places named. Fixed: Q10 defaults to No (the terms); 12.1 is blocked by DTCC's terms and not planned; B3's acceptance has no DTCC (task 7 is a production-input report with no traded level; V6 likewise); the CDX DTCC note is deleted; section 1, 3.4 and Appendices B and E say the files were fetched before the terms check and are never stored, refreshed, reproduced or reused, and rule 7.5 forbids opening them. Beyond the fix: every per-name DTCC-derived level was removed from the plan (0, 3.4, the financial row of 3.5, 4.1, Appendix B), only aggregates remain, the roll basis in 2.1 no longer cites DTCC, and the Unsupported row no longer says "planned".
- CDS-C-03 (major) ACCEPTED, fixed. Verified in `calib_eval.py` and `calib_rows.json`: exactly AIG, AMD, BA, BAC, GS, INTC, LMT, MET, MSFT, NEE and VZ used Cboe implied vol; inputs were `universe.json`'s (PFE 4.0B, D 0.70 a share; F 291M; ORCL 0); 32 of 55 had raw under 5 bp (8 exactly at the floor); a constant 42 bp scores a median 22.0 bp; 27 of 55 were within 35 %. My production-input runs reproduce the critic's levels (VZ 54.38 as drafted, 59.89 with the R4b fix; KBH 124.15; KSS 547.33; ORCL 115.74 / 112.54) and its Appendix B variant (VZ 78.72, KBH 149.73, KSS 812.29, ORCL 149.08). Fixed: 3.4 describes the inputs truthfully and keeps the constant-floor baseline beside the model; 0, Q1 and the owner report replace "within a median 16.8 bp" with the qualitative statement; B3 task 7 reports the shipped model on production inputs for a fixed 20-name list beside the Appendix B variant, with the Appendix B rows as regression vectors; the owner chooses under Q1; nothing is tuned.
- CDS-C-04 (major) ACCEPTED, fixed, with two refinements. Verified on companyfacts and frames: ORCL's 2026-08-31 10-Q has only `LongTermNotesAndLoans` 117.712B and `NotesPayableCurrent` 7.625B, and 3.2 did not say which `end` rule applies; VZ's R4b 143.448B leaves out `LongTermDebtCurrent` 21.783B; PEP STB 10.602B with CP 6.100B and ICE 1.218B / 1.218B; about 40 national-exchange filers carry >= USD 100M only in R6 tags (39 by the largest single tag, plus CDNS whose only recipe tag is a zero `LinesOfCreditCurrent`), KBH, CME, CDNS, VRSN, ACGL and ILMN among them; on all CY2026Q2I filers 17 STB + CP pairs (14 with CP <= STB) and 18 NotesPayable + convertible pairs (10 with the convertible <= the total). Fixed in 3.2 and B2: `end` = the latest end among the recipe tags, shown with its age; R6; the R4b current fallback (`LongTermDebtCurrent`, else `DebtCurrent`); the same-end tie-break (latest `filed`, else `known_from`, then `accn`); fixtures with the ORCL 08-31, KBH, VZ, PEP and ICE shapes; the live check asserts ORCL at 125-130B and VZ at about 165B. Refinements: a part counts only when it is larger than its whole (rather than max(STB, CP), so a CP that cannot sit inside STB is still added), and R5b drops only the note parts when `NotesPayable` is present but keeps lines of credit, which exceed `NotesPayable` in 6 of the 10 filers reporting both and so are separate.
- CDS-C-05 (minor) ACCEPTED, fixed. Verified on the 18:52 ET file: Nasdaq 4,357, NYSE 3,269, OTC 2,577, null 171, CBOE 44, TXSE 12, no "NYSE American"; with the exchange filter the first-draft recipe gives 1,599 (>= USD 100M) and 2,161 (no minimum), as stated. Fixed: the allowlist {NYSE, Nasdaq, CBOE, TXSE}, case-insensitive, any other string dropped and logged (4.1, B2 tasks 1-2 and a test); 4.1 shows both recipes (the corrected one gives 1,663 and 2,388); Q8 and section 0 updated.
- CDS-C-06 (minor) ACCEPTED, fixed (both parts). Verified: S 0.01, sigma 2.0, D 40 raises OverflowError (xi is about 9.0e4 rather than 1.4e5; the overflow is the same); S 0.05, sigma 1.5, D 40 gives 4,989.37 bp against 4,999.42 by quadrature (T 5.148, r 4.76 %). Fixed in 3.1: when P(0) < 0.95, or the closed form raises or is non-finite, c* = (1 - R)(1 - P(T)e^-rT - rI) / I by Simpson's rule with 2,000 steps (equal to the closed form to 12 digits on V1, V3 and V5); `cg_spread` catches OverflowError, ZeroDivisionError and ValueError and answers MODEL_DOMAIN only when the quadrature fails too; V6 (overflow, raw 5,177.00 bp) and V7 (cancellation, 5,036.85 not 5,024.92) are in Appendix A and B3's tests.
- CDS-C-07 (minor) ACCEPTED, fixed. Verified: B3 task 3 kept splits in memory while a VM of a past date never fetches, so after a restart D would use unadjusted shares. Fixed: a `cds_splits` table written in the same `cds.vol_record` as the vol rows, with `events_through` on each vol row (3.2, 6.2, B3 task 3); a VM without the vol row of R answers CLOSE_PENDING and queues the fetch; a B3 test runs the VM in a new runtime on the same database; live, a 09:45 ET events pass over the open contracts' equities makes a split known on its ex-date.
- CDS-C-08 (minor) ACCEPTED, fixed. Verified in `swaps.h_rfq_accept`: the accept re-runs `h_book` on the original terms with only `ctx.rfq_firm` pinned, so an omitted coupon would be re-defaulted at the booking instant. Fixed: the firm dict carries `dealt_spread_bp`, `coupon_bp`, `maturity_date` and `tier` from the quote (H1-13 and H1-14, 2.1, B1 task 3 step 6) and the booking uses them; test 16 moves the model across the BBB / BB boundary between quote and accept.
- CDS-C-09 (minor) ACCEPTED, fixed. Verified in the research's ICE list: Oracle Corporation is eligible at the 100 bp coupon only (VZ 100 only, KBH 500 only). Fixed: the default coupon is 100 unless the MODEL bucket before the basis is BB-like or worse (2.1, 3.3, 5.1, Q11, B1 tasks 2-3); the coupon is always shown when a negotiated mid is set; the tier, IM and half-spread change caused by a basis is shown before Save (3.5, 5.4, test 18). The tier itself still follows the mid, because a name the owner marks wide costs more to margin and deal; it is now visible. ICE's per-name coupons as the default are deferred (12.9).
- CDS-C-10 (minor) ACCEPTED, fixed. Verified: V3 section 16 lists "Oracle rallies 50%" and "Nasdaq rallies 30% and credit spreads tighten", while the first draft's risk block had spread shocks only and 12.6 deferred the equity link. Fixed: B3's pure `spread_at(model, s_mult)`; risk_block scenarios for every name -30 % / +30 % and the largest-JTD name -30 / +30 / +50 %, with per-name equity columns in `by_name`, by full revaluation with the basis held (5.2, 6.4, 6.6, B1 task 12, test 19, the V1 shock vectors in Appendix A); 12.6 narrowed to implied-vol shocks.
- CDS-C-11 (minor) ACCEPTED, fixed. Verified: 26 tags x 5 quarters + 1 = 131 requests contradicted "about 50 requests, about 20-40 s". With the R6 and insurer tags the first run is 40 x 5 + 1 = 201 requests and about 22 MB (`Assets` alone 0.74 MB a quarter), about 2-3 minutes; daily runs about 80. Fixed in 4.3, the desk's loading text, 9.3 V3 and 9.5 step 5.
- CDS-C-12 (minor) ACCEPTED, fixed (both options). Verified: `LiabilityForFuturePolicyBenefits` has 41 filers in CY2026Q2I (34 listed with a value) and `Deposits` 407. Four more insurer tags fetched at 19:30 ET (GOV user agent) raise the insurer flag from 34 to 129 listed filers and catch TRV, ALL, PGR, ACGL, WRB, PRU, MKL and AFG. Fixed: the four tags join the frames list (3.5, 4.3, B2 task 1, a test); the browse flag carries its source and the filter says "known so far" until the SIC is checked (4.2, 6.3, B4).
- CDS-C-13 (minor) ACCEPTED, fixed; the optional part rejected. Verified: 3.5 applied a new basis at once to the marks and the next VM of every open contract on the name, while the 5.4 dialog showed no effect. Fixed: `GET /api/otc/cds/basis/effect` and both level dialogs list each open contract's value before and after by full revaluation, and the total, before Save; SETTINGS_CHANGED records the same (3.5, 5.4, 6.5, B1 task 11, test 17, the V3 walk). Rejected: "a new basis applies only to new deals unless re-mark is ticked", because one name must carry one mark per account; two levels for the same reference entity would value identical risk differently and make the name's CS01, JTD and concentration inconsistent.
- CDS-C-14 (minor) ACCEPTED, fixed; the critical-path claim only in part. Verified: FX_CCS_PLAN section 11 puts 15-20.25 h between the 1.0.6 commit and its trades and its gate at about 16:00-22:00 EDT on 10-29, so 1.0.7 deploys on 10-30, and the first draft presented the order as fixed. Fixed: Q13 puts the order to the owner with both timelines (FX first: FX books Friday morning, CDS deploys Friday; CDS first: CDS about 12:30-15:30 EDT Thursday, FX Friday morning only if nothing slips, else Monday 11-02), and the header, 9.1 and section 10 allow B3 and B2's core in a scratch folder outside the repository after the 1.0.6 gate if the memory check allows. In part only: that early start does not shorten the 1.0.7 pool, because B1 (now 5.25-5.75 h) is the longest package; it moves B2's and B3's live checks before the gate and halves the post-gate pool.
