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
```

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

### A4. Create the two routines

At **claude.ai/code/routines**, create both with: model **claude-sonnet-5**, environment **intelligence-terminal**, repository **shafferusa/intelligence-terminal** attached, and **"Allow unrestricted branch pushes" ENABLED** (the routine must push directly to `main`).

| Routine | Cron (UTC, summer/EDT) | Runs at (ET) | Prompt |
|---|---|---|---|
| Weekday briefs | `30 11,20 * * 1-5` | Mon–Fri 7:30 AM & 4:30 PM | `Read CLAUDE.md and prompts/weekday.md in this repository and execute the run procedure exactly.` |
| Weekend reports | `0 13 * * 0,6` | Sat & Sun 9:00 AM | `Read CLAUDE.md and prompts/weekend.md in this repository and execute the run procedure exactly.` |

Crons are UTC; the values above are correct **during US daylight saving time**. See section B for the twice-yearly bump.

### A5. Phase-1 verification tests

Run all three before trusting the schedule:

1. **Pages pipeline** — push any trivial change under `site/` to `main`; confirm the "Build & deploy site" Actions run fires and the site updates.
2. **Egress + Telegram** — start a run-now session in the environment and `curl -s https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe`; confirm a JSON reply naming the bot (proves both the allowlist and the token).
3. **End-to-end thin report** — trigger the weekday routine via "run now"; confirm it produces a (possibly thin) report page, prepends `site/reports/index.json`, pushes to `main`, Pages redeploys, and the Telegram message arrives with a working link.

---

## B. DST bump procedure (twice a year)

Routine crons are UTC; ET shifts. Two edits per year:

- **Nov 1, 2026** (fall back, EDT→EST): weekday cron `30 11,20 * * 1-5` → `30 12,21 * * 1-5`; weekend cron `0 13 * * 0,6` → `0 14 * * 0,6`.
- **Mar 14, 2027** (spring forward, EST→EDT): reverse it — weekday back to `30 11,20 * * 1-5`, weekend back to `0 13 * * 0,6`.

One-sentence instruction that works in any Claude session: *"Update my two intelligence-terminal routines' cron schedules for the DST change per docs/RUNBOOK.md section B (use the routine update / RemoteTrigger mechanism)."*

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
| Report missing at expected time | claude.ai/code/routines → run list. Did the run start? Open the transcript for the failing step. If no run started, check routine is enabled and cron/DST is right (section B). |
| Telegram silent but site updated | Run transcript: did the sendMessage call fail? Check `TELEGRAM_BOT_TOKEN` validity (A5-2 curl) and that `TELEGRAM_CHAT_ID` is `7805141860`. |
| Site stale but Telegram arrived | GitHub **Actions** tab: did "Build & deploy site" fire and pass? If it didn't fire, confirm the push touched `site/**` and hit `main`. Re-run via workflow_dispatch if needed. |
| Blocked domain (HTTP 403 with `x-deny-reason: host_not_allowed`) | A fetch hit a host missing from the environment allowlist. Add the exact hostname to the allowlist in the environment settings (section A3) and re-run. |
| Duplicate report risk | Runs are idempotent via `state/last-run.json` (date+slot). If a duplicate appears, inspect that file's last committed state in the run transcript. |

For any failed run: **claude.ai/code/routines → run list → transcript** is always the first stop. Manual regeneration is "run now" on the routine.

---

## F. Usage notes (Max plan)

- Routine runs draw from Max-plan usage, capped at **15 routine runs/day**. The standard schedule uses 2/day weekdays and 1/day weekends — well under the cap.
- Heavy interactive Claude usage on the same plan can starve scheduled runs near limits; if a run is skipped for usage, it will show in the routines run list — regenerate with "run now" once headroom returns.

---

## G. Recovery & portability

- **Everything rebuildable from the repo alone.** All state — reports, story memory (`state/`), ledgers, registry, curriculum positions, site — lives in git. Clone the repo anywhere and the full history and current state come with it.
- Losing the cloud environment loses only the secrets, which are re-creatable (section A3): re-enter the env vars, re-attach the repo, re-create the two routines per A4.
- **Worst-case portability:** the run procedures in `CLAUDE.md` + `prompts/` are plain instructions. The same prompts are runnable via the Claude Agent SDK on any host with a cron scheduler and the same environment variables — nothing depends on a specific runner.
