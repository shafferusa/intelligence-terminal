# RUNBOOK — Operations Manual

Operator manual for the intelligence-terminal system. `docs/SPEC.md` defines *what* the system produces; this document covers *how to run it*: one-time setup, recurring maintenance, failure triage, and recovery. Keep this file current — it is the only setup record.

---

## A. One-time setup checklist

Work through these in order. Each step has a verification.

### A1. Repository & GitHub Pages

1. Create the public repository **`shafferusa/intelligence-terminal`** on GitHub.
2. Push the staging directory (this repo) to `main`.
3. Enable Pages: **Settings → Pages → Source: GitHub Actions** (not "Deploy from a branch" — the workflow `.github/workflows/build-site.yml` builds the Pagefind search index and deploys `site/`).
4. Verify the first **Actions** run ("Build & deploy site") completes green. It fires on any push to `main` that touches `site/**`, and can also be run manually via **Actions → Build & deploy site → Run workflow**.
5. Verify the site loads at:

   **https://shafferusa.github.io/intelligence-terminal/**

### A2. Connect GitHub to Claude Code cloud

1. Go to **claude.ai/code**, connect the GitHub account, and grant access to `shafferusa/intelligence-terminal`.

### A3. Create the cloud environment

Create a cloud environment named **`intelligence-terminal`** with:

**Network access: Custom** — allowlist exactly these domains, and check **"include default package managers"** (required so GitHub itself and tooling keep working):

```
api.telegram.org
home.treasury.gov
api.stlouisfed.org
fred.stlouisfed.org
cdn.cboe.com
api.twelvedata.com
query1.finance.yahoo.com
query2.finance.yahoo.com
api.frankfurter.dev
api.coingecko.com
api.polygon.io
api.massive.com
cdn.finra.org
www.bls.gov
apps.bea.gov
www.census.gov
www.federalreserve.gov
www.treasurydirect.gov
api.fiscaldata.treasury.gov
api.congress.gov
clerk.house.gov
www.senate.gov
www.majorityleader.gov
www.supremecourt.gov
ll.thespacedevs.com
fdo.rocketlaunch.live
efts.sec.gov
www.sec.gov
data.sec.gov
finnhub.io
www.alphavantage.co
api.nasdaq.com
www.nyse.com
api.weather.gov
```

**`api.weather.gov` was added 2026-08-16** for the morning edition's Pittsburgh-area weather strip
(gridpoint for Bridgeville, PA). It is free and needs no key, but like every `.gov` host it
requires the `User-Agent: LoganTerminal/1.0 (loganshaffer87@gmail.com)` header. **Add it to the
environment allowlist before the first morning run**, or the strip will silently be omitted.

`cdn.cboe.com` stays on the list for VIX quotes and history. Its options market-statistics path
(put/call ratios) is retired — it returned 403 on every run for weeks.

**Environment variables** (secrets live ONLY here — never in the repo):

| Variable | Value / where to get a free key |
|---|---|
| `TELEGRAM_BOT_TOKEN` | From @BotFather for `@logannewspaperbot` |
| `TELEGRAM_CHAT_ID` | `7805141860` |
| `FRED_API_KEY` | https://fredaccount.stlouisfed.org (free API key) |
| `TWELVE_DATA_KEY` | https://twelvedata.com (free tier) |
| `FINNHUB_KEY` | https://finnhub.io (free tier) |
| `ALPHA_VANTAGE_KEY` | https://www.alphavantage.co (free key) |
| `COINGECKO_KEY` | https://www.coingecko.com (free "demo" API key) |
| `MASSIVE_KEY` | https://massive.com (free "Stocks Basic" key; ex-Polygon.io — enables EOD whole-market breadth; optional, reports degrade gracefully without it) |

### A4. Create the three routines

At **claude.ai/code/routines**, create all three with: model **claude-sonnet-5**, environment **intelligence-terminal**, repository **shafferusa/intelligence-terminal** attached, and **"Allow unrestricted branch pushes" ENABLED** (the routine must push directly to `main`).

| Routine | Cron (UTC, summer/EDT) | Runs at (ET) | Prompt |
|---|---|---|---|
| Learning Brief | `0 10 * * 1-5` | Mon–Fri 6:00 AM | `Read CLAUDE.md and prompts/learning.md in this repository and execute the run procedure exactly.` |
| Weekday briefs | `30 10,20 * * 1-5` | Mon–Fri 6:30 AM & 4:30 PM | `Read CLAUDE.md and prompts/weekday.md in this repository and execute the run procedure exactly.` |
| Weekend reports | `0 13 * * 0,6` | Sat & Sun 9:00 AM | `Read CLAUDE.md and prompts/weekend.md in this repository and execute the run procedure exactly.` |

**The 6:00 and 6:30 runs overlap by design and must not fight.** The Learning Brief writes a
~6,000-word lesson and can still be running when the Morning Brief starts. They touch different
report files, but both prepend to `site/reports/index.json` and append to `state/run-log.jsonl`.
Shared-rules §15.2 covers it: `git pull --rebase` before every push, and on a conflict in
`index.json` or `run-log.jsonl` take the remote version and re-apply your own addition. If the two
ever start colliding in practice, move the Learning Brief earlier (`0 9 * * 1-5` = 5:00 AM ET)
rather than delaying the news.

**Do not pin a routine to a branch.** The weekday routine had `claude/nice-bardeen` set as its
outcome branch, which forced the §15.2b PR fallback on most runs. Leave it unset so runs push
straight to `main`.

**Cron drift check:** on 2026-08-16 the weekday cron was found to be `30 9,19 * * 1-5` — 5:30 AM
and 3:30 PM ET, meaning the closing brief was being written *thirty minutes before the market
closed*. If report content ever looks early, check the actual cron first (`RemoteTrigger list`).

Crons are UTC; the values above are correct **during US daylight saving time**. See section B for the twice-yearly bump.

### A5. Phase-1 verification tests

Run all three before trusting the schedule:

1. **Pages pipeline** — push any trivial change under `site/` to `main`; confirm the "Build & deploy site" Actions run fires and the site updates.
2. **Egress + Telegram** — start a run-now session in the environment and `curl -s https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe`; confirm a JSON reply naming the bot (proves both the allowlist and the token).
3. **End-to-end thin report** — trigger the weekday routine via "run now"; confirm it produces a (possibly thin) report page, prepends `site/reports/index.json`, pushes to `main`, Pages redeploys, and the Telegram message arrives with a working link.

---

## B. DST bump procedure (twice a year)

Routine crons are UTC; ET shifts. Two edits per year:

- **Nov 1, 2026** (fall back, EDT→EST): learning `0 10 * * 1-5` → `0 11 * * 1-5`; weekday `30 10,20 * * 1-5` → `30 11,21 * * 1-5`; weekend `0 13 * * 0,6` → `0 14 * * 0,6`.
- **Mar 14, 2027** (spring forward, EST→EDT): reverse it — learning back to `0 10 * * 1-5`, weekday back to `30 10,20 * * 1-5`, weekend back to `0 13 * * 0,6`.

One-sentence instruction that works in any Claude session: *"Update my three intelligence-terminal routines' cron schedules for the DST change per docs/RUNBOOK.md section B (use the routine update / RemoteTrigger mechanism)."*

---

## C. Telegram token rotation

If the token leaks or as periodic hygiene:

1. Message **@BotFather** → `/token` → select `@logannewspaperbot` → it revokes the old token and issues a new one.
2. Update `TELEGRAM_BOT_TOKEN` in the **intelligence-terminal** cloud environment's variables.
3. Verify with the `getMe` curl from step A5-2. Nothing in the repo changes — the token never appears there.

---

## D. Annual chores

- **NYSE holiday table** (each December): refresh `data/nyse-holidays.json` from https://www.nyse.com/markets/hours-calendars — holidays and early closes, kept ~3 years ahead.
- **FOMC calendar** (when the Fed publishes next year's dates, usually mid-year): refresh the committed FOMC meeting dates from federalreserve.gov so calendar sections stay accurate.
- **Watchlist hygiene** (whenever `site/status.html` shows the same symbol degraded on five consecutive runs): a fund that returns no bar for a week has been delisted or liquidated (FM was, in 2025, and sat in the failed-sources list for weeks). Remove or replace it in `config/watchlists.yml`; the routine picks the change up on the next run.

---

## E. Failure triage

| Symptom | Check |
|---|---|
| Report missing at expected time | claude.ai/code/routines → run list. Did the run start? Open the transcript for the failing step. If no run started, check routine is enabled and cron/DST is right (section B). |
| Telegram silent but site updated | **Actions sends it, not the run.** GitHub → Actions → "Notify Telegram on direct publish" (or the `notify` job of "Build & deploy site" on the PR path). Check the job log, then `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` **repository secrets** (not the environment variables). |
| Telegram message arrived twice | Should be impossible since 2026-08-16. It means something is sending besides Actions — check the run transcript for a `sendMessage` curl, which `prompts/shared-rules.md` §14 forbids. Do not "fix" it by adding a guard to the workflow; that is exactly what failed before. |
| Telegram push has no bullets | The report's `index.json` entry is missing `headlines` (shared-rules §13). Actions has no other source for them. |
| Site stale but Telegram arrived | GitHub **Actions** tab: did "Build & deploy site" fire and pass? If it didn't fire, confirm the push touched `site/**` and hit `main`. Re-run via workflow_dispatch if needed. |
| Blocked domain (HTTP 403 with `x-deny-reason: host_not_allowed`) | A fetch hit a host missing from the environment allowlist. Add the exact hostname to the allowlist in the environment settings (section A3) and re-run. |
| Duplicate report risk | Runs are idempotent via `state/last-run.json` (date+slot). If a duplicate appears, inspect that file's last committed state in the run transcript. |

For any failed run: **claude.ai/code/routines → run list → transcript** is always the first stop. Manual regeneration is "run now" on the routine.

---

## E2. Breaking-news alerts (built 2026-08-16, switched OFF 2026-08-17)

**Off at Logan's request** ("just turn off the alerts, not needed"). The schedule in
`.github/workflows/breaking-alerts.yml` is commented out; `workflow_dispatch` still works for a manual
run. To turn it back on, uncomment the cron — nothing else needs to change. The rest of this section
describes how it behaves when it runs.

`.github/workflows/breaking-alerts.yml` + `.github/scripts/breaking_alerts.py`, every ~10 minutes.
**News only** — no market-move alerts, by Logan's instruction. Message is deliberately minimal:
`BREAKING — <headline>` plus a link, WSJ preferred. He reads the real story in the next edition.

**How it decides something is big.** Cross-source corroboration, not keyword severity: a story
alerts when **3+ independent newsrooms** carry the same event inside 90 minutes (2+ if a tight
`SEVERITY` list matches). Headlines are clustered by Jaccard overlap of content words at 0.35.

**Plus a topic gate**, because corroboration measures "widely covered", not "important". On the
night this was built, three outlets all led with four Renaissance paintings stolen from a Sicilian
museum — perfectly corroborated, not worth a push. So a cluster must also hit the `BEAT` list
(war/diplomacy, government/law, economy/markets, tech/AI/cyber, disasters/public safety, space)
and must not hit the `SOFT` list (arts, celebrity, royals, sport, "world's tallest"). `SEVERITY`
overrides a `SOFT` veto, so a shooting at a museum still gets through.

**Guards:** one push per 20 minutes, 6 per ET day, 23:00–06:30 ET is severity-only, and a dedupe
record in `state/alerts.json` (matched on token overlap, so a re-worded headline of the same event
does not alert twice). Committing that file is the only thing this workflow writes, it happens only
when an alert actually fires, and it never touches `site/` so it cannot trigger a site build.

**Tuning:** the lists and thresholds are constants at the top of the script; `config/settings.yml`
documents them. Too noisy → raise `min_newsrooms` to 4, or add terms to `SOFT`. Too quiet → add
outlets to `FEEDS` (they must be genuinely independent newsrooms; three feeds republishing one wire
are one source) or add terms to `BEAT`.

**Latency:** GitHub's scheduled workflows are best-effort and get delayed under load, so the real
cadence is nearer 10–25 minutes. For "big news, details in the morning" that is fine. If it ever
matters, port the script to a **Cloudflare Worker** (free tier, 1-minute cron): it is stdlib-only
and the only pieces to swap are `urllib` → `fetch` and the dedupe file → Workers KV.

## E3. Report audio (built 2026-08-16)

`.github/workflows/audio.yml` + `.github/scripts/make_audio.py`, on every push that changes
`site/reports/index.json`. Uses **edge-tts** (Microsoft read-aloud; free, no key, good neural
voices) to synthesise the newest report, and publishes the MP3 as a **GitHub Release asset**.

**Not committed to the repo, deliberately:** three reports a day at ~11 MB is ~12 GB a year, which
has no business in git history. Release assets are free and outside history.

**The URL is predictable**, which is what makes the whole thing work without a commit-back step or
a race against the page build:

```
https://github.com/shafferusa/intelligence-terminal/releases/download/audio-<date>-<slot>/<date>-<slot>.mp3
```

`site/assets/report.js` points an `<audio>` element at that URL. If it loads, the reader gets a
real player — lock screen, background, CarPlay, scrub bar, resume-where-you-left-off, and
Media Session metadata. If it 404s, the page falls back to the Web Speech reader.

**Synthesis takes ~9 minutes**, so the audio lands after the Telegram push. The page therefore
re-probes every 90 seconds for ~12 minutes and upgrades silently — but only while speech is idle,
never mid-sentence. A reader who opens the report immediately starts on speech and gets swapped to
real audio a few minutes later, or on any reload.

**Failure is non-fatal by design.** edge-tts is an unofficial client and can break; the job is
`continue-on-error`, publishes nothing, and the page falls back on its own. Nothing else notices.

**Voice:** `TTS_VOICE` / `TTS_RATE` env vars in `audio.yml`. Logan's pick (2026-08-16) is
`en-GB-ThomasNeural` at `+0%` — a UK news-register voice at its natural pace, chosen after comparing
+8% / 0% / −8% on a real edition. The earlier default (`en-US-AndrewMultilingualNeural` at `+8%`) read a
newspaper flat and rushed. `edge-tts --list-voices` shows the alternatives.

**Where the phone plays it from:** GitHub Releases serve MP3s as `application/octet-stream` with an
attachment disposition, which iOS Safari refuses to play inline. So `build-site.yml` stages the most
recent release MP3s into `site/audio/` at build time (Pages serves them as `audio/mpeg`, same-origin,
with range requests) and `report.js` tries that URL first. A report older than the staging window falls
back to the release URL — which works in desktop Chrome and NOT on the iPhone — and then to browser
speech. If a lesson from last week has the wrong voice, the staging window in `build-site.yml` is why.

## F. Usage notes (Max plan)

- Routine runs draw from Max-plan usage, capped at **15 routine runs/day**. The standard schedule uses 3/day weekdays (learning + morning + closing) and 1/day weekends — well under the cap.
- Heavy interactive Claude usage on the same plan can starve scheduled runs near limits; if a run is skipped for usage, it will show in the routines run list — regenerate with "run now" once headroom returns.

---

## G. Recovery & portability

- **Everything rebuildable from the repo alone.** All state — reports, story memory (`state/`), ledgers, registry, curriculum positions, site — lives in git. Clone the repo anywhere and the full history and current state come with it.
- Losing the cloud environment loses only the secrets, which are re-creatable (section A3): re-enter the env vars, re-attach the repo, re-create the two routines per A4.
- **Worst-case portability:** the run procedures in `CLAUDE.md` + `prompts/` are plain instructions. The same prompts are runnable via the Claude Agent SDK on any host with a cron scheduler and the same environment variables — nothing depends on a specific runner.
