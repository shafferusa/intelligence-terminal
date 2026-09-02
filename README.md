# intelligence-terminal

Logan's Daily Newspaper — a personal, automated intelligence terminal. Scheduled Claude Code cloud
routines research, verify and write newspaper-style editions from primary sources, publish them as
static mobile-first pages to GitHub Pages, and GitHub Actions turns each one into a spoken MP3 and
sends a short Telegram push with a link. No server, no app, no manual steps, no human in the loop.

## The editions

| Edition | When (ET) | What it is |
|---|---|---|
| Learning Brief | Mon–Fri 6:00 AM | One lesson, taught properly, from a 150-day curriculum across seven subjects. Strictly learning — no news. |
| Morning Brief | Mon–Fri 6:30 AM | The overnight world, top stories, the economy, today's calendar, before the open, local news and weather. |
| Closing Brief | Mon–Fri 4:30 PM | What changed today, **The Board** (a fixed 25-row watchlist chart), what moved markets, tomorrow. |
| Weekly Review | Sat 9:00 AM | The weekend's news plus a synthesis of the week, a forecast scorecard, and what was over- and under-covered. |
| Week Ahead | Sun 9:00 AM | The weekend's news plus the week's calendar, risk register and scenarios that next Saturday grades. |

Every edition has a listen-to-text player: a generated MP3 (real neural voice, lock-screen and
CarPlay playback) with an in-browser speech fallback for anything older than the staging window.

## Architecture

```
                 ┌──────────────────────────────────────────┐
   cron (UTC)    │  Claude Code cloud routines              │
  ──────────────▶│  learning · weekday am/pm · sat · sun    │
                 │  (prompts/ + CLAUDE.md)                  │
                 └───────────────┬──────────────────────────┘
                                 │ fetches (allowlisted domains only)
                                 ▼
        ┌────────────────────────────────────────────────────┐
        │  Primary sources: SEC EDGAR, FRED, Treasury, BLS,  │
        │  BEA, Federal Reserve, Cboe, FINRA, NWS, launch    │
        │  schedules, market-data APIs, WebSearch/WebFetch   │
        └───────────────┬────────────────────────────────────┘
                        │ verify · analyze · write · one commit
                        ▼
        ┌────────────────────────────────────────────────────┐
        │  This repo (git push to main)                      │
        │  site/reports/…  state/  ledgers/  registry/       │
        └───────┬─────────────────────┬──────────────────────┘
                │                     │
                ▼                     ▼
   ┌────────────────────────┐  ┌────────────────────────────┐
   │ Build & deploy site    │  │ Generate report audio      │
   │ Pagefind index, status │  │ edge-tts → MP3 → GitHub    │
   │ + curriculum copies,   │  │ Release → rebuild so Pages │
   │ recent MP3s staged     │  │ serves it same-origin      │
   └────────────┬───────────┘  └────────────┬───────────────┘
                ▼                            ▼
   https://shafferusa.github.io/     Notify Telegram (waits for
      intelligence-terminal/         the MP3, then one push with
                                     title · summary · bullets · link)
```

## Contents

- `site/` — the published static site: report pages, the archive index (`reports/index.json`),
  the Academy page (the curriculum, lesson by lesson), the status page, equations, PWA assets.
- `prompts/` + `CLAUDE.md` — the run procedures the cloud routines execute, literally.
- `curriculum/` — `academy-150.json`, the live 150-lesson curriculum; the older physics, spaceflight
  and quant-ml sequences are retained as source material for it.
- `state/`, `ledgers/`, `registry/` — story memory and run state, the corrections and forecast
  ledgers (append-only accountability), the public/private entity registry.
- `config/` — the schedule, local beats and weather point (`settings.yml`) and the watchlists,
  including The Board (`watchlists.yml`).
- `docs/SPEC.md` — the authoritative product specification, including the dated in-session
  decisions that supersede it. `docs/RUNBOOK.md` — setup, DST bumps, token rotation, failure triage.
- `.github/` — the Pages build, the audio job, the Telegram notifier, and the (switched-off)
  breaking-news scanner.

## Notes

- All credentials (Telegram token, data-API keys) live in the cloud environment's variables —
  nothing sensitive is stored in this repository.
- All state needed to rebuild or move the system lives in the repository itself.
- Reports favor primary sources, explicit uncertainty, and corrections over speed or drama; every
  forecast is logged and graded, every correction is surfaced. See `docs/SPEC.md`.
