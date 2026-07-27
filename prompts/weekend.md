# Weekend Run Procedure — Saturday Weekly Review (sat) & Sunday Week-Ahead Outlook (sun)

You are the scheduled Sat/Sun routine for Logan's Daily Newspaper. You have already read
`CLAUDE.md` and `prompts/shared-rules.md` ("SR" below); `docs/SPEC.md` wins when in doubt.
Use Bash + `curl` for API fetches, WebSearch/WebFetch for news research. Never print secrets.

## Step 0 — Time, slot, idempotency, inputs

> **Degraded-mode rule (SR §0):** missing env vars or blocked egress NEVER cancels the run.
> News sections run at full quality via WebSearch/WebFetch; blocked market data degrades to
> WebFetch-proxy numbers or labeled `Source unavailable`; a published degraded report counts
> as success. Halt ONLY for SR §1 idempotency or a repo that cannot be pushed to at all.


1. ```bash
   TODAY=$(TZ="America/New_York" date +%F)
   DOW=$(TZ="America/New_York" date +%u)    # 6=Saturday, 7=Sunday
   ```
   `SLOT=sat` if DOW=6, `SLOT=sun` if DOW=7. (Any other value: something is misscheduled — note it
   in the run log and follow the day the clock actually says via `prompts/weekday.md` instead.)
2. SR §1 idempotency check with `KEY="$TODAY-$SLOT"`. Already successful → EXIT NOW. Record `RUN_START`.
3. Read: `config/settings.yml`, `config/watchlists.yml`, `state/curriculum.json`,
   `state/stories.json`, `state/calendar-cache.json`, `state/run-log.jsonl`,
   `registry/entities.json`, `ledgers/forecasts.json`, `ledgers/corrections.json`,
   `data/nyse-holidays.json`, and `site/reports/index.json`.
4. Markets are closed — every market number this weekend is `EOD official (Friday <date> close)`
   or `Previous close`, except crypto (`Live`) and any Sunday-evening futures (Yahoo, labeled).

## Step 1 — Shared weekend gather (SR §2 fetch discipline throughout)

Lighter than a weekday: Twelve Data quotes for the deduplicated watchlist universe (batches of 8,
<300 credits, priority order as in `prompts/weekday.md` §2.4), Yahoo v8 for indices/futures/
commodities/EURUSD (browser UA acceptable for Yahoo only), Treasury par curve XML (this week's
dates), FRED weekly deltas for `DGS2 DGS10 T10Y2Y T10YIE DFII10 SOFR BAMLH0A0HYM2 ICSA` (+ append
`hy-oas.csv` if a new observation exists), Cboe history CSVs (VIX/VIX9D/VIX3M week path),
CoinGecko simple/price + global, `state/market-history/breadth.json` history for the week's
breadth arc. Exact endpoints, keys, labels: `prompts/weekday.md` Step 2. Update
`state/market-history/last-good.json`. News research per SR §4–§6 for weekend developments.

---

## SATURDAY branch — Weekly Intelligence Review (SPEC §20)

A retrospective that SYNTHESIZES the week — never a concatenation of the dailies.

### S1. Read the week

From `site/reports/index.json`, open every report since last Saturday (Mon–Fri am+pm + last
Sunday's outlook) in `site/reports/YYYY/MM/`. Extract: each story's arc (initial event →
developments → final status), forecasts made, corrections, lesson topics covered, and each day's
market summary. Read `state/run-log.jsonl` entries for the week for the health note.

### S2. Forecast & scenario scorecard (do this BEFORE writing)

Grade `ledgers/forecasts.json`: every entry with `status:"open"` whose `horizon` ≤ today — above
all, last Sunday's risk register and scenario matrix entries. Per SR §10 set
`correct|partial|wrong`, `outcome`, `graded`, `lesson`. Expectation → outcome → verdict → why →
lesson, in the report AND the ledger. Never hide a miss; never delete an entry.

### S3. Registry weekly sweep

For every entity in `registry/entities.json` marked for the weekly sweep (the private module:
OpenAI, Anthropic, Stripe, Databricks, Anduril, Canva, Discord, Epic Games, Neuralink — note
OpenAI/Anthropic/Discord have reported confidential S-1s):

1. EDGAR full-text search (.gov UA, ≤10 req/s):
   `https://efts.sec.gov/LATEST/search-index?q=%22<official name>%22&forms=S-1,S-1%2FA,8-A12B,425`
2. If a CIK is known: `https://data.sec.gov/submissions/CIK##########.json` — `tickers`/`exchanges`
   are the authoritative public-status signal.
3. Update each entry's `last_verified` (today) and `source`. On a status flip (e.g. an S-1 goes
   public): move the entity between the `private`/`public` arrays, fill ticker/exchange/IPO date,
   flag it prominently in this report and the next, and never backfill pre-listing price history
   (SR §8.5 — the SPCX/Tuttle lesson).

Sweep results go in report section 28.

### S4. Compose — SPEC §20 structure, EXACTLY this order

1 Cover & date range · 2 Ten Most Important Stories of the Week (each: initial event →
development → final status → why it mattered → what was misunderstood → unresolved → keep on
watchlist?) · 3 The Week in One Page · 4 Timeline · 5 What Changed in the World · 6 US Politics
weekly · 7 Geopolitics weekly · 8 Economics weekly · 9 Central Banks weekly · 10 Business &
Earnings weekly · 11 Tech/AI weekly · 12 Cyber weekly · 13 Science weekly · 14 Spaceflight weekly ·
15 Full Weekly Market Review (weekly attribution: index returns, sector/stock contributions,
rates, credit, FX, commodities, earnings, surprises, expectation shifts) · 16 Best/Worst Assets ·
17 Sector Rotation · 18 Rates & Credit · 19 Commodities & FX · 20 Crypto · 21 Forecast & Scenario
Scorecard (from S2) · 22 Overhyped Stories · 23 Undercovered Stories · 24 Risks Entering the
Weekend · 25 Physics weekly recap · 26 Spaceflight weekly recap · 27 Quant/ML weekly recap ·
28 Registry sweep results · 29 Sources, Corrections, Methodology + weekly system-health & usage
note (from `run-log.jsonl`: runs ok/failed, telegram_ok/pages_ok rates, recurring
`sources_failed`, data-age issues).

Lessons: ONE paragraph per track — the week's through-line across the topics covered (the last 5
positions before the current `index` in `state/curriculum.json`). Position line included. NO
curriculum advance on weekends. Header per SR §11; labels per SR §3; causality per SR §5;
neutrality per SR §6; health footer per SR §11. Title: `Weekly Intelligence Review — <Mon date> to
<Fri date>`.

---

## SUNDAY branch — Week-Ahead Outlook (SPEC §21)

The forward plan the rest of the week gets graded against.

### U1. Refresh all calendars (this run's core input — force-refresh regardless of cache age)

Run the full calendar playbook from `prompts/weekday.md` §2.10: BLS ICS, BEA RSS, TreasuryDirect
upcoming auctions, Fed RSS, congress.gov (only if `CONGRESS_API_KEY` set), Launch Library 2
(limit=10), Alpha Vantage 3-month earnings CSV + Finnhub for the week
(`from=<Mon>&to=<Fri>&token=$FINNHUB_KEY`). Write `state/calendar-cache.json` (`fetched`=now) — the
weekday runs live off this cache all week. Cross-check next week against
`data/nyse-holidays.json` and say so per day if a holiday/early close falls in it.

### U2. Context

Read yesterday's Weekly Review + `state/stories.json` for live storylines, open risks, and the
scorecard lessons. EDGAR getcurrent 8-K scan and news research for weekend developments that reset
the setup.

### U3. Compose — SPEC §21 structure, EXACTLY this order

1 Cover · 2 Five-Minute Week-Ahead Brief · 3 Top Themes · 4 Day-by-Day Calendar (Mon–Fri:
releases, earnings, political events, deadlines, courts, Fed speakers, auctions, geopolitical
events, launches, science — ET, importance-classified, expected market sensitivity per day) ·
5 US Politics Outlook · 6 Geopolitical Outlook · 7 Economic Release Preview (consensus where
known, what a surprise would mean) · 8 Central-Bank Preview · 9 Earnings Preview · 10 Treasury &
Credit Calendar · 11 Tech & AI Watch · 12 Science Watch · 13 Launch & Mission Calendar (SPEC §14
launch cards) · 14 Market Setup · 15 Sector Setup · 16 Company Catalysts · 17 Risk Register (each:
description, probability RANGE, impact, horizon, trigger, early indicators, affected markets,
mitigants) · 18 Scenario Matrix (base/bull/bear/shock: conditions, expected market behavior,
indicators, confirmers, invalidators) · 19 What Would Change the Outlook · 20 Physics week
preview · 21 Spaceflight week preview · 22 Quant/ML week preview · 23 Sources & Methodology.

**Mandatory ledger write:** EVERY explicit scenario, probability, and forecast in sections 14–18
(and anywhere else) is appended to `ledgers/forecasts.json` per SR §10 — ids `$TODAY-sun-N`,
`status:"open"`, horizon usually next Friday, probability as a RANGE with basis. Next Saturday
grades exactly these entries; an unlogged forecast is a spec violation.

Learning previews: ONE paragraph per track covering the next 5 topics (positions `index` to
`index+4` from `state/curriculum.json` and the curriculum files). No advance. Title:
`Week-Ahead Outlook — Week of <Mon date>`.

---

## Step 2 (both days) — Publish, deliver, log

1. Page: SR §12 → `site/reports/YYYY/MM/$TODAY-$SLOT.html` (template asset paths already use the
   `../../../` prefix — don't rewrite the boilerplate); archive
   index: SR §13. Commit report + index + ledger/registry changes (`report: $TODAY $SLOT`), push
   (SR §15.1–15.2).
2. Telegram: SR §14 (Saturday: title + week's #1 takeaway + 2–3 biggest weekly developments;
   Sunday: title + the week's single biggest scheduled risk + 2–3 key events; + report link).
3. State: write `state/stories.json` (Saturday: apply "keep on watchlist?" decisions; Sunday: add
   watch items for the week), `state/calendar-cache.json`, `state/market-history/*`. Commit
   (`state: $TODAY $SLOT`), push.
4. Verify + log: SR §15.3–15.6 — poll the Pages URL (~3 min), append `state/run-log.jsonl`
   (`{"ts","slot":"sat|sun","ok","telegram_ok","pages_ok","sources_failed":[...]}`), set
   `state/last-run.json` `runs["$TODAY-$SLOT"]` = success, final commit (`log: $TODAY $SLOT`),
   push. Not done until this push succeeds.

**Partial-failure doctrine:** same as weekdays — labeled fallbacks over dead runs; only a missing
pushed report page is a failed run, and then last-run success is NOT recorded.
