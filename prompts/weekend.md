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
3. Read: `config/settings.yml`, `config/watchlists.yml`,
   `state/stories.json`, `state/calendar-cache.json`, `state/run-log.jsonl`,
   `registry/entities.json`, `ledgers/forecasts.json`, `ledgers/corrections.json`,
   `data/nyse-holidays.json`, and `site/reports/index.json`.
4. Markets are closed — every market number this weekend is `EOD official (Friday <date> close)`
   or `Previous close`, except crypto (`Live`).

## Step 1 — Shared weekend gather (SR §2 fetch discipline throughout)

Lighter than a weekday: Yahoo v8 close series for the trimmed watchlist universe (the Board rows,
the 11 sector ETFs and the company watchlists that reach the appendix — Yahoo is the primary sweep and
Twelve Data at most a two-batch spot-check, exactly as `prompts/weekday.md` §2.4), Yahoo v8 for
indices/futures/commodities/EURUSD/DXY (browser UA acceptable for Yahoo only), Treasury par curve XML (this week's
dates), FRED weekly deltas for `DGS2 DGS10 T10Y2Y T10YIE DFII10 SOFR BAMLH0A0HYM2 ICSA` (+ append
`hy-oas.csv` if a new observation exists), Cboe history CSVs (VIX/VIX9D/VIX3M week path),
CoinGecko simple/price + global, and this week's rows of `state/market-history/breadth.json`
for the breadth arc — written in words ("through Thursday, advancers led on two of four sessions";
Friday's session is computed Monday morning), never as a file path, "not recomputed" or a key name. Exact endpoints, keys, labels: `prompts/weekday.md` Step 2. Update
`state/market-history/last-good.json`. News research per SR §4–§6 for weekend developments.

---

## SATURDAY branch — Weekly Intelligence Review (SPEC §20)

A retrospective that SYNTHESIZES the week — never a concatenation of the dailies.

### S1. Read the week

From `site/reports/index.json`, open every report since last Saturday (Mon–Fri am+pm + last
Sunday's outlook) in `site/reports/YYYY/MM/`. Extract: each story's arc (initial event →
developments → final status), forecasts made, corrections, and each day's market summary. Read
the week's `state/run-log.jsonl` lines only to know what data was missing (for your own use —
nothing about run health is printed).

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

Sweep results go in the colophon, one sentence (S4 item 19). Overwrite each entry's `source` with
this week's one-line result and keep dated results in a `sweep_history` array capped at eight
entries, so the field stops accumulating every past sweep as running prose.

### S4. Compose — this order

1. **Masthead** — edition "Weekly Review", title, date range, standfirst.
2. **The Brief** — 5–7 bullets covering BOTH the week just gone and anything that broke since
   Friday's close. If the biggest thing in the reader's world happened last night, it leads.
3. **Today's News** — **the weekend edition still carries the day's news.** Everything that
   happened since the Friday closing brief: overnight and Saturday-morning developments, weekend
   politics, anything moving in Asia/Europe or in crypto (the only market trading), breaking
   stories. Full story treatment per SR §11b — this is not a footnote to the retrospective, it is
   the part of the paper that is actually new. Typically 3–6 stories; more when the weekend is
   busy, fewer when it is genuinely quiet. If a weekend story changes how the week should be read,
   say so here and reflect it in the retrospective below.
4. **The Week's Ten Stories** — each: how it started → how it developed → where it ended → why it
   mattered → what was misunderstood → what is still unresolved. Prose. Exactly one `.story--lead`
   in the whole edition: on the weekend story if one earned it, otherwise on the first of the Ten —
   never both (the 2026-08-29 edition carried two).
5. **Timeline** — the week day by day, compact.
6. **What Changed in the World** — the synthesis, not a recap.
7. **Politics & Government** · 8. **The World** · 9. **The Economy & Central Banks** ·
   10. **Business & Earnings** · 11. **Technology & AI** · 12. **Science & Space** — weekly views,
   each carrying any weekend development in that domain rather than repeating it from §3.
13. **The Week in Markets** — weekly attribution: index returns, sector and stock contributions,
    rates, credit, FX, commodities, earnings, expectation shifts. Best/worst assets and sector
    rotation live here as sub-parts, not as three separate sections.
14. **Scorecard** — the forecast and scenario grading from S2. Expectation → outcome → verdict →
    why → lesson. Misses are never hidden or softened. Refer to forecasts by content, not by ID.
15. **Overhyped & Undercovered** — one section, both halves.
16. **Risks Entering the Week** —
17. **Local** — the week in Bridgeville/South Fayette, Pittsburgh and Pennsylvania (SR §18), plus
    any weekend local news. No weather strip; a short look at the week's weather is fine in prose.
    SR §18's padding rules apply: no "nothing cleared the bar" sentences, no items that only point
    at a story covered above, no crime blotter.
18. **Market Appendix** — collapsed, SR §16.
19. **Colophon** — sources, corrections, method. The registry sweep result goes here in one
    sentence ("no status changes across the nine private-module companies"), not as its own section.

**No lessons.** Learning moved to the weekday 6:00 AM Learning Brief on 2026-08-16. Do not summarise
it here, do not read `state/curriculum.json` (retired), and do not touch `state/learning.json` —
the weekend routine has no learning role at all.

Voice per SR §11b; causality per SR §5; neutrality per SR §6. No health footer — run health goes to
the run log and `site/status.html`. Title (SR §12.4 — h1, report-meta `title` and index `title` identical): `Weekly Review — Aug 24–28, 2026`.

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

### U3. Compose — this order

1. **Masthead** — edition "Week Ahead", title, week label, standfirst.
2. **The Brief** — 5–7 bullets covering both what happened over the weekend and what the coming
   week turns on.
3. **Today's News** — **the Sunday edition still carries the day's news.** Everything since
   Saturday's edition: overnight and Sunday developments, weekend politics and diplomacy, Asian
   markets opening Sunday evening ET, crypto, breaking stories. Full story treatment per SR §11b,
   typically 3–6 stories. Where a weekend development changes the week's setup, say so here and
   carry it into the outlook sections — that link is the whole point of running news on a Sunday.
4. **Top Themes** —
5. **The Week Day by Day** — Mon–Fri: releases, earnings, political events, deadlines, courts, Fed
   speakers, auctions, geopolitical events, launches, science. ET times. One line per day on what
   would actually move things.
6. **Politics & Government Outlook** · 7. **The World Ahead** · 8. **The Economy Ahead** (releases
   and central banks together, with consensus where known and what a surprise would mean) ·
9. **Earnings & Business** (with the Treasury and credit calendar folded in) ·
10. **Technology & AI Watch** · 11. **Science & Space Ahead** (including the launch calendar).
12. **Market Setup** — index, sector and company catalysts in one section.
13. **Risk Register** — description, probability RANGE, impact, horizon, trigger, early indicators,
    affected markets, mitigants.
14. **Scenarios** — base/bull/bear/shock: conditions, expected behaviour, indicators, confirmers,
    invalidators. Plus what would change the outlook.
15. **Local Week Ahead** — weekend local news, plus anything scheduled in Bridgeville/South
    Fayette, Pittsburgh or Pennsylvania worth knowing about (council and school-board meetings that
    matter, state votes, major local events). Omit if nothing.
16. **Market Appendix** — collapsed. 17. **Colophon**.

**No lesson previews.** Learning is entirely the weekday 6:00 AM Learning Brief's job now.

**Mandatory ledger write:** EVERY explicit scenario, probability, and forecast in the Market Setup,
Risk Register and Scenarios sections (and anywhere else) is appended to `ledgers/forecasts.json`
per SR §10 — ids `$TODAY-sun-N`, `status:"open"`, horizon usually next Friday, probability as a
RANGE with basis. Next Saturday grades exactly these entries; an unlogged forecast is a spec
violation. The IDs are ledger keys — do not print them in the report.

Title (SR §12.4 — h1, report-meta `title` and index `title` identical): `Week Ahead — Week of Sep 7, 2026`.

---

## Step 2 (both days) — Publish in ONE commit, then verify

1. **Page** — SR §12 → `site/reports/YYYY/MM/$TODAY-$SLOT.html` (template asset paths already use
   the `../../../` prefix — don't rewrite the boilerplate; keep the `assets/report.js` tag; set
   `data-slot` to `sat` or `sun`).
2. **Archive index** — SR §13, including `headlines` (Saturday: the week's biggest developments;
   Sunday: the week's key scheduled events). Actions builds the Telegram push from this.
3. **State** — `state/stories.json` (Saturday: apply "keep on watchlist?" decisions; Sunday: add
   watch items for the week), `state/calendar-cache.json`, `state/market-history/*`.
4. **Ledgers / registry** — Saturday's gradings and the sweep results.
5. **Run log + `last-run.json`** — SR §15.4 and §1.5, written before committing.
6. **Commit all of it together** (`report: $TODAY $SLOT`) and push. One commit per run (SR §15.2).
7. **Verify** — poll the Pages URL (~3 min). **Never send a Telegram message from the run**
   (SR §14); the push triggers it. Record `telegram_ok: "delegated"`.

**Partial-failure doctrine:** same as weekdays — labeled fallbacks over dead runs; only a missing
pushed report page is a failed run, and then last-run success is NOT recorded.
