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

### A4. Create the routines

At **claude.ai/code/routines**, create them with: model **claude-opus-5-5** for the weekday and weekend newspaper routines (since 2026-09-25; the other routines as set), environment **intelligence-terminal**, repository **shafferusa/intelligence-terminal** attached, and **"Allow unrestricted branch pushes" ENABLED** (the routine must push directly to `main`).

| Routine | Cron | Runs at (ET) | Prompt |
|---|---|---|---|
| Learning Brief | `CRON_TZ=America/New_York 50 4 * * 1-5` | Mon–Fri 4:50 AM start → push lands ~5:00, by 5:30 at the latest | `You are the scheduled Learning Brief author for Logan's Daily Newspaper. In the attached repository (shafferusa/intelligence-terminal): read CLAUDE.md, then prompts/shared-rules.md, then prompts/learning.md, and execute the Learning Brief run procedure exactly. prompts/learning.md and state/learning.json decide the curriculum, today's lesson, the length and the format. Never send a Telegram message yourself; GitHub Actions sends it when you push.` |
| Weekday briefs | `CRON_TZ=America/New_York 30 6,16 * * 1-5` | Mon–Fri 6:30 AM & 4:30 PM | `You are the scheduled news reporter for Logan's Daily Newspaper. In the attached repository (shafferusa/intelligence-terminal): read CLAUDE.md, then prompts/shared-rules.md, then prompts/weekday.md, and execute the weekday run procedure exactly. The current America/New_York time determines the edition: morning run = 6:30 AM Morning Brief, afternoon run = 4:30 PM Closing Brief. This report is STRICTLY NEWS — no lessons (those belong to the separate 6:00 AM Learning Brief). Never send a Telegram message yourself; GitHub Actions sends it when you push.` |
| Weekend reports | `CRON_TZ=America/New_York 0 9 * * 0,6` | Sat & Sun 9:00 AM (a `:00` cron starts ~6–11 min late) | `You are the scheduled weekend news reporter for Logan's Daily Newspaper. In the attached repository (shafferusa/intelligence-terminal): read CLAUDE.md, then prompts/shared-rules.md, then prompts/weekend.md, and execute the weekend run procedure exactly. The current day in America/New_York determines the edition: Saturday = Weekly Review, Sunday = Week Ahead. This report is STRICTLY NEWS — no lessons and no learning recaps (learning is the separate weekday 6:00 AM Learning Brief). Never send a Telegram message yourself; GitHub Actions sends it when you push.` |
| SIE Program (added 2026-09-25) | `CRON_TZ=America/New_York 28 11 * * *` | Daily 11:28 AM start → push lands ~noon | `Read CLAUDE.md, prompts/shared-rules.md and prompts/sie.md in this repository and execute the SIE run procedure exactly.` |

**The Learning Brief starts at 4:50 AM ET so it is in Telegram by 5:30** (Logan, 2026-09-25:
"5am … better be delivered by 5:30am"; SPEC §0c). The scheduler starts `:00` crons 9–12 minutes
late (the old `0 10` fired 6:08–6:10 ET) but other minutes on time, so 4:50 really means 4:50. A
lesson run pushes 8–12 minutes after it starts; `notify.py` then waits for the page to go live
(1–2 minutes, no audio) and sends. Expect the push around 5:00–5:10. It no longer overlaps the
6:30 Morning Brief; shared-rules §15.2's pull-rebase rule still covers any `index.json` or
`run-log.jsonl` race.

**Editing this routine is manual.** It was created through the API, and agents cannot edit
API-created routines. At **claude.ai/code/routines** → "Intelligence Terminal — Learning Brief":
set the schedule to weekdays at 4:50 AM Eastern (cron above; if the editor only accepts UTC, use
`50 8 * * 1-5` during EDT and `50 9 * * 1-5` during EST and add it to the section B bump), and
replace the prompt with the one in the table. The old prompt named `academy-150.json` and "25-30
minutes"; `CLAUDE.md` and `prompts/learning.md` tell a run to ignore that, but fix it anyway.

**The SIE Program starts at 11:28 ET so the Telegram push arrives around noon**: the run takes
~15–25 minutes (grading, an 8,000-word lesson, a quiz and its answer key) and `notify.py` then waits
for the page to go live. Its cron carries `CRON_TZ`, like every routine, so it does **not** need a DST bump. It needs the **`sie-inbox` workflow** on `main` (it is scheduled there
automatically) so Logan's Telegram answers are captured — see section E4.

**Do not pin a routine to a branch.** The weekday routine had `claude/nice-bardeen` set as its
outcome branch, which forced the §15.2b PR fallback on most runs. Leave it unset so runs push
straight to `main`.

**Cron drift check:** on 2026-08-16 the weekday cron was found to be `30 9,19 * * 1-5` — 5:30 AM
and 3:30 PM ET, meaning the closing brief was being written *thirty minutes before the market
closed*. If report content ever looks early, check the actual cron first (`RemoteTrigger list`).

Every routine cron carries `CRON_TZ=America/New_York`, so the times above hold year-round — no DST bump (section B).

### A5. Phase-1 verification tests

Run all three before trusting the schedule:

1. **Pages pipeline** — push any trivial change under `site/` to `main`; confirm the "Build & deploy site" Actions run fires and the site updates.
2. **Egress + Telegram** — start a run-now session in the environment and `curl -s https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe`; confirm a JSON reply naming the bot (proves both the allowlist and the token).
3. **End-to-end thin report** — trigger the weekday routine via "run now"; confirm it produces a (possibly thin) report page, prepends `site/reports/index.json`, pushes to `main`, Pages redeploys, and the Telegram message arrives with a working link.

---

## B. Daylight saving time — no bump needed

Since 2026-09-26 every routine is pinned to `America/New_York` with `CRON_TZ` (verified with
`get_trigger` the same day). The scheduler follows EDT/EST on its own, so Nov 1 and Mar 14 need no
edits and the papers keep their ET times year-round.

**If a routine is ever recreated or edited, keep the `CRON_TZ=America/New_York` prefix** (or set
the editor's timezone to America/New_York). A plain UTC cron silently shifts the edition by an hour
at every DST change — the drift that put the closing brief before the market close on 2026-08-16.
Newspaper values: weekday `CRON_TZ=America/New_York 30 6,16 * * 1-5`; weekend
`CRON_TZ=America/New_York 0 9 * * 0,6`.

These routines were created through the API, so agents cannot edit them — Logan changes them at
**claude.ai/code/routines**. Agents can still read them (`get_trigger`) to check.

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

---

## E. Failure triage

| Symptom | Check |
|---|---|
| Report missing at expected time | claude.ai/code/routines → run list. Did the run start? Open the transcript for the failing step. If no run started, check the routine is enabled and its cron still carries `CRON_TZ=America/New_York` (section B). |
| Telegram silent but site updated | **Actions sends it, not the run.** GitHub → Actions → "Notify Telegram on direct publish" (or the `notify` job of "Build & deploy site" on the PR path). Check the job log, then `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` **repository secrets** (not the environment variables). |
| Telegram message arrived twice | Should be impossible since 2026-08-16. It means something is sending besides Actions — check the run transcript for a `sendMessage` curl, which `prompts/shared-rules.md` §14 forbids. Do not "fix" it by adding a guard to the workflow; that is exactly what failed before. |
| Telegram push has no bullets | The report's `index.json` entry is missing `headlines` (shared-rules §13). Actions has no other source for them. |
| Site stale but Telegram arrived | GitHub **Actions** tab: did "Build & deploy site" fire and pass? If it didn't fire, confirm the push touched `site/**` and hit `main`. Re-run via workflow_dispatch if needed. |
| Blocked domain (HTTP 403 with `x-deny-reason: host_not_allowed`) | A fetch hit a host missing from the environment allowlist. Add the exact hostname to the allowlist in the environment settings (section A3) and re-run. |
| Duplicate report risk | Runs are idempotent via `state/last-run.json` (date+slot). If a duplicate appears, inspect that file's last committed state in the run transcript. |

For any failed run: **claude.ai/code/routines → run list → transcript** is always the first stop. Manual regeneration is "run now" on the routine.

---

## E2. Breaking-news alerts (built 2026-08-16)

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

## E3. Report audio — retired 2026-09-25

Removed at Logan's request ("no audio is needed"): `.github/workflows/audio.yml`,
`.github/scripts/make_audio.py`, the MP3 staging step in `build-site.yml`, the 20-minute MP3 wait
in `notify.py`, and the listen-to-text / MP3 player in `site/assets/report.js`. Telegram pushes now
go out as soon as the page is live: `notify.py` polls the page URL until Pages answers 200 (about
1.5 minutes after the push; up to 5 minutes, then it sends anyway) so the link never opens on a 404.

Existing `audio-<date>-<slot>` GitHub Releases were left in place (deleting them is irreversible);
they are unused and can be deleted from the repo's Releases page at any time.

## E4. SIE Program replies (built 2026-09-25)

`.github/workflows/sie-inbox.yml` runs `.github/scripts/sie_inbox.py` every ~15 minutes:

1. `collect` — `getUpdates` (no offset), keeps only messages from `TELEGRAM_CHAT_ID` that are SIE
   answers or commands, appends them to `state/sie/inbox.jsonl`.
2. The workflow commits and pushes the inbox (only when something new arrived).
3. `confirm` — replies to each message (instant score from `state/sie/quizzes/day-NN.json`, or a
   command acknowledgement), then confirms the updates with `offset=max+1`.

Reply formats Logan can use: `SIE 5: BDACA CBDAB`, `SIE 5: 1B 2D 3A`, `SIE exam 2026-10-24`,
`SIE pause`, `SIE resume`, `SIE stop`, `SIE note …`, `SIE help`.

| Symptom | Check |
|---|---|
| No score reply after answering | Actions → "SIE inbox" run log. Scheduled runs can lag 15–30 min. `workflow_dispatch` it by hand. |
| Reply says "no answer key on file" | The routine did not write `state/sie/quizzes/day-NN.json` that day — check the SIE run transcript. The noon run still grades from the inbox once a key exists. |
| Answers older than a day never arrived | Telegram drops unconfirmed updates after 24 h. The Action must be enabled (Actions → SIE inbox → Enable) — GitHub disables scheduled workflows after 60 days without repo activity. |
| `getUpdates` 409 Conflict | Someone set a webhook on the bot. `deleteWebhook` restores polling. |

Privacy: the repo is public, so the inbox file is public. Only SIE-formatted messages are stored;
anything else Logan sends the bot is ignored and never written.

**Egress for SIE fact-checking (recommended):** add `www.finra.org`, `www.irs.gov`, `www.msrb.org`
and `www.investor.gov` to the environment allowlist (section A3). They were blocked when the program
was built, so runs verify through WebSearch and `www.sec.gov`, which is slower and less direct.

## F. Usage notes (Max plan)

- Routine runs draw from Max-plan usage, capped at **15 routine runs/day**. The standard schedule uses 4/day weekdays (learning + morning + closing + SIE) and 2/day weekends (weekend edition + SIE) — well under the cap.
- Heavy interactive Claude usage on the same plan can starve scheduled runs near limits; if a run is skipped for usage, it will show in the routines run list — regenerate with "run now" once headroom returns.

---

## G. Recovery & portability

- **Everything rebuildable from the repo alone.** All state — reports, story memory (`state/`), ledgers, registry, curriculum positions, site — lives in git. Clone the repo anywhere and the full history and current state come with it.
- Losing the cloud environment loses only the secrets, which are re-creatable (section A3): re-enter the env vars, re-attach the repo, re-create the two routines per A4.
- **Worst-case portability:** the run procedures in `CLAUDE.md` + `prompts/` are plain instructions. The same prompts are runnable via the Claude Agent SDK on any host with a cron scheduler and the same environment variables — nothing depends on a specific runner.
