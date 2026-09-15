# intelligence-terminal

A personal, automated intelligence terminal. Scheduled Claude Code cloud routines gather and verify news, market, economic, political, and spaceflight data from primary sources; generate structured intelligence reports; publish them as static mobile-first web pages to GitHub Pages; and push a short summary with a link via Telegram. No server, no app, no manual steps.

## Architecture

```
                 ┌──────────────────────────────────────────┐
   cron (UTC)    │  Claude Code cloud routine               │
  ──────────────▶│  (weekday AM/PM · Sat review · Sun       │
                 │   outlook — see prompts/ and CLAUDE.md)  │
                 └───────────────┬──────────────────────────┘
                                 │ fetches (allowlisted domains only)
                                 ▼
        ┌────────────────────────────────────────────────────┐
        │  Primary sources: SEC EDGAR, FRED, Treasury, BLS,  │
        │  BEA, Census, Federal Reserve, Congress, courts,   │
        │  Cboe, FINRA, market-data APIs, launch schedules   │
        └───────────────┬────────────────────────────────────┘
                        │ verify · analyze · write
                        ▼
        ┌────────────────────────────────────────────────────┐
        │  This repo (git push to main)                      │
        │  site/reports/…  state/  ledgers/  registry/       │
        └───────┬────────────────────────────────┬───────────┘
                │ push triggers                  │
                ▼                                ▼
   ┌─────────────────────────┐      ┌─────────────────────────┐
   │  GitHub Actions:        │      │  Telegram bot push:     │
   │  Pagefind index build   │      │  title · summary · top  │
   │  → GitHub Pages deploy  │      │  items · report link    │
   └────────────┬────────────┘      └────────────┬────────────┘
                ▼                                ▼
     https://shafferusa.github.io/          iPhone notification
        intelligence-terminal/
```

## Contents

- `site/` — the published static site: report pages, archive index, equations, PWA assets.
- `prompts/` + `CLAUDE.md` — run procedures the cloud routines execute.
- `state/`, `ledgers/`, `registry/` — story memory, corrections and forecast ledgers, entity registry.
- `curriculum/` — the three light learning tracks (physics, spaceflight, quant/ML).
- `docs/SPEC.md` — the authoritative product specification.
- `docs/RUNBOOK.md` — the complete operations manual (setup, DST bumps, token rotation, failure triage, recovery).

## Notes

- All credentials (Telegram token, data-API keys) live in the cloud environment's variables — nothing sensitive is stored in this repository.
- All state needed to rebuild or move the system lives in the repository itself.
- Reports favor primary sources, explicit uncertainty labels, and corrections over speed or drama; see `docs/SPEC.md`.

## finsim — institutional finance simulation (separate application)

`finsim/` is a self-contained, single-player simulation of an institutional financial
system (event-sourced double-entry ledger, simulated multi-asset market, order execution,
trade lifecycle, DVP settlement and custody, corporate actions, NAV/P&L explain, audit
trail). It is unrelated to the newspaper routines and never touches real markets.
See `finsim/README.md` — run with `cd finsim && python3 -m finsim serve`.
