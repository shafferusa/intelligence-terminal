# FinSim1 completion plan (planner, 2026-10-27 12:45 EDT; revision 2 from 13:49 EDT; revision 3 written from 14:52 EDT)

Build this right after finsim-104-complete (wf_f1df0abe-ecd) finishes. Ship it in two releases, 1.0.5 then 1.0.6 (section 15). It completes FinSim1, the live paper-trading
terminal (package `finsim\finsim\terminal`), as the owner's directive requires (`docs\OWNER_DIRECTIVE_FINSIM1_2026-10-27.md`, read in
full). That directive includes the OWNER CLARIFICATION of 2026-10-27 11:29. It is binding and supersedes sections 5, 7, 9, 10, 11, 12
and 17 where they conflict.

**Revision 2.** An independent critic reviewed revision 1 and raised 22 findings (F01-F22: 1 critical, 7 major, 14 minor). I checked
each one against the code and a fresh read-only copy of the live database. All 22 hold, and all 22 are fixed in this revision.
Section 13 ("Review answers") gives each answer, the evidence and where the fix lives. The import batch was rebuilt to match
(revision 2, below).

**Revision 3.** Two independent reviewers checked revision 2 and raised 35 findings (R2A-01..R2A-13 and R2B-01..R2B-22: 9 major,
26 minor). I checked each one against the plan, the batch, the code and a fresh read-only copy of the live database (14:54 EDT,
state_version 3208, `scratchpad\checks\plan-rev3-145422`). All 35 hold and all 35 are fixed where the builders read them: the decisions,
contracts, package tasks and acceptances, and the batch (revision 3). Four pairs overlap: R2A-04 / R2B-11, R2A-07 / R2B-19, R2A-06 /
R2B-08 and R2A-03 / R2B-02. Section 14 gives one line per finding. Two more inputs since revision 2:
- The owner's decision of 2026-10-27 15:04 EDT ("Use Goldman terms", now at the top of the directive) answers Q2, Q3 and Q5: the GS
  schedule's TRS terms and initial margin, and no T-bill reserve. Batch line 34 is now `declined`.
- P1 is built in two steps: P1a (the hooks and every P1 change that other packages' code, tests or walks depend on; first, alone) and P1b
  (the rest; in the wave-2 pool). Section 5 and the P1 section say exactly what each holds.

**Inputs read**
- The directive and its clarification.
- The owner's audited workbook, `FinSim_Final_Audited_Blotter_2026-10-23.xlsx` (read from its text dump).
- `BUILD_LOG.md`, newest lines up to 13:08 EDT: the integrator (11:43), PORT-HOTFIX (12:25), CDX HOOKS (12:47) and VERIFIER r1 SUITES
  (13:08).
- `BUILD2_PLAN.md`, contracts C1-C27.
- `RESUME_2026-10-21.md` and `OWNER_PORTFOLIO_2026-10-21.md`.
- The scratchpad file `hotfix_port_notes.md`.
- The four read-only inventories, with 78 gap ids in total:
  - ui-news-nav: UI-01..21
  - oms-blotters: OMS-01..17
  - portfolio-accounting: PORTFOLIO-01..20
  - startup-tests: STARTUP-01..10 and TEST-01..10
- The reconciliation: 35 lines plus an import design.
- The repo code: router and C1 hook, schema and chart seeding, ledger, lifecycle jobs and tick order, swaps / CDX / TRS, curve store,
  marks, orders, execution and funding, the release and commit scripts in the scratchpad.
- Read-only copies of the live database: 12:10 EDT (state_version 3191) and 13:22 EDT (state_version 3200,
  `scratchpad\checks\plan-rev-1323`).
- The critic's 22 findings.
- For revision 3: a fresh read-only copy at 14:54 EDT (state_version 3208, `scratchpad\checks\plan-rev3-145422`), the 35 re-review
  findings and the owner's decision of 15:04 EDT.

**Batch file.** The final import batch is
`C:\Users\logan\AppData\Local\Temp\claude\C--Users-logan-intelligence-terminal\5e0edd51-72c4-438a-89eb-77c644650257\scratchpad\import\batch_final.jsonl`:
- Revision 3, built 15:33 EDT from revision 2 (built 13:46 EDT, option match note corrected 14:02 EDT; sha256
  `2e3b8d2b82fd05d5085921847abf02709bbf5839e525bcd204e640a06f6c695c`, 109,124 bytes).
- 45 records: 1 header, 6 setup steps (S0-S4 with S2b), 35 lines, 1 package record (the QQQ spread command), 1 catch-up plan
  (C1-C6 with C5b), 1 summary.
- Decisions: 23 preserve, 10 import, 1 declined (line 34, the owner's answer to Q5), 1 unresolved (line 35).
- sha256 `75690c19327713c6648d20b710495f5ef0b5a924ed263748b3f74aa51bd11d2f` (121,468 bytes).
- Package P5 copies it into the repo, because the scratchpad is not durable (Storage Sense).

## 0. Summary

Eight packages over disjoint files cover all 78 gap ids (P1 is built in two steps, P1a and P1b). A few slices of them are deferred,
each with its reason (section 9). They ship in two releases (section 15):
- 1.0.5: P1a, Part A of P2 / P3 / P4 / P6, P5's phase-1 scope and P8-lite, with the owner's positions imported;
- 1.0.6: P1b, Part B of P2 / P3 / P4 / P6, P7, the rest of P5 and the full P8.

The "Starts after" column gives the order inside each phase. Where it, a package section or any other section disagrees with section
15, section 15 wins (15.1 rule 1 lists the statements it supersedes).

| Package | Scope | Starts after |
|---|---|---|
| P1a | The shell hooks (routes, keys, optional modules, the attention module, CSS, the refusal recorder, Unsupported rows), the ledger chart sync, the news-module retirement other packages' code and tests touch, the duplicate-instance message and the 1.0.3.1 schema fixture | nothing (runs alone) |
| P1b | The rest of P1: the remaining news removal, the Dashboard, the directive's navigation (sidebar) and the keys UI | P1a (wave-2 pool, first batch) |
| P2 | Builds the two trade blotters (Pending with the order history, Completed with every execution source) on the order engine: amend / replace, GTD and next-session orders, lifecycle stages, review, execution detail, rejections from every ticket, as-of listed fills, as-of funding on business-date balances | P1a (wave-2 pool, first batch) |
| P3 | Portfolio, NAV, history and performance: as-of NAV engine with restated snapshot versions and their split, closed positions, drill-down, dated marks, NAV history, returns, sector, the Activity / Risk margin views | P1a (wave-2 pool, first batch) |
| P4 | Derivatives and the OTC lifecycle: as-of OTC booking at the owner's dealt terms, dated inputs, historical VM, the IRS curve spread, intraday swap marks, OTC GL reclassification, RFQ and confirmation, simulated credit events, the Derivatives page | P1a (wave-2 pool, first batch) |
| P6 | Cash, settlement, collateral and margin: settlement fail / resolve, restricted collateral (net per CSA), account-level Reg T, margin calls, T-bill holdings and CSA pledges, custody, as-of sweeps and re-accrual, the Accounts and Cash & Collateral pages | P1a (wave-2 pool, the first free slot) |
| P7 | Fixes Windows startup (the repo-root `python -m finsim` failure, the launcher, the two-tree messages) and rewrites the FinSim1 parts of the README | P1a (wave-2 pool, the next free slot) |
| P5 | The historical import tool (data pack fetched on the apply day, report / dry-run / apply, idempotent, backup first) and the owner's batch IMP-F1-20261027-A | P1b, P2, P3, P4, P6, P7 |
| P8 | Acceptance: browser smoke, directive test map, whole-fund reconciliation, end-to-end proof on an owner-DB copy | all others |

The build proceeds on the owner's answers to Q2, Q3 and Q5 (2026-10-27 15:04 EDT, "Use Goldman terms") and on the defaults of the
other 11 owner questions (section 8). The import is applied to the live database only at the 1.0.5 deploy (or a 1.0.5.1 hotfix's,
section 15.8 step 8), no later than 2026-11-19 and before 16:00 ET that day. Under the owner's standing pre-approval (2026-10-27 15:12
EDT) it follows a dry run on a fresh copy on which P8-lite's checker passes his five conditions, and the dry-run report and its
plain-English summary are sent to him (section 15.8). The gate also updates the commit and release tooling (section 12).

## 1. Where the build starts (facts checked 12:10-13:30 EDT, rechecked 14:54 EDT)

### Live app

**The release.** finsim-terminal 1.0.3.1 (release `2026-10-21-r1031b`) runs as pid 17272 on port 8765.

**The Fund book (F1).** Unchanged between the 12:10 copy (state_version 3191) and the 13:22 copy (state_version 3200). The commands
in between were rates.record x4 at 12:22 EDT (SOFR, ECBDFR, BAMLH0A0HYM2, VIXCLS), provider_health.update x4 and one
lifecycle.integrity_full.
- Orders, fills and settlements:
  - 23 FILLED orders and 2 CANCELLED (O-000008 PG 6,400 "PG 6,400 BUY -> replaced by 4,790"; O-000018 STT calls "STT calls (owner: not
    intended)").
  - 315 fills, all origin ORDER.
  - 309 settlements, all SETTLED.
- Other activity: 5 OPEN borrows; no OTC contracts; no FX deals.
- NAV snapshots (F1:ALL):

  | Date | NAV | Quality |
  |---|---:|---|
  | 10-21 | 25,000,000.00 | |
  | 10-22 | 24,880,554.10 | stale (taken 10-23 08:04) |
  | 10-23 | 25,215,528.08 | |
  | 10-24 | 25,214,886.43 | |
  | 10-25 | 25,221,106.71 | |
  | 10-26 | 25,531,898.56 | |

**Facts the import depends on** (13:22 copy).
- The chart table has 52 rows. It lacks the lending codes 1420 / 2420 / 2520 / 4360 / 5350 and every GL this plan adds. On a clone with
  foreign keys on, a journal line on 4451 is refused ("FOREIGN KEY constraint failed") while 4450 is accepted.
- No ORCL or META CLOSE mark exists (89 CLOSE marks over 23 instruments). SOFR and BAMLH0A0HYM2 run to 2026-10-26; BAMLC0A0CM has
  no rows. UST_PAR curves exist for 10-20, 10-21, 10-22, 10-23 and 10-26.
- F1:TREASURY business-date cash: 25,000,000.00 at the close of 10-21; 1,467,580.80 at the close of 10-22. The owner's transfers made at
  19:39-19:43 ET on 10-21 carry business date 10-22 (the 17:00 ET accounting-day cut-off).
- F1:PB_GS PB credit interest nights 10-22..10-26 were accrued as "0.00 on 0.00".
- Nightly accruals for night D post with business date D+1 (e.g. `accr:cashint:F1:USD:2026-10-21` is dated 2026-10-22).
- Snapshots and accruals run every calendar day: `nav_snapshots` holds a row per F1 account and F1:ALL for every date 10-21..10-26
  (Saturday and Sunday included); F1:PB_GS has rows 10-22..10-26 only, all 0.00 (it opened 19:44:13 ET on 10-21, after that day's 17:45
  snapshot); Treasury cash interest was accrued for every night 10-21..10-26 (entries 698 and 711 for the weekend nights).
- 14:54 EDT copy (state_version 3208, `scratchpad\checks\plan-rev3-145422`): F1 still has 315 fills (all origin ORDER) and 5 OPEN
  borrows (B-000001..5, stored lender "street lender (modelled)"); no OTC contract, FX deal, repo, loan or private loan. Each account's
  TODAY baseline is its own latest snapshot (`accounting._last_snapshot(conn, account_id, today)`).

**Known problems.**
- `hedge_packages` PK-000001 still reads WORKING although its primary O-000017 is FILLED and its only leg O-000018 is CANCELLED.
- The live Portfolio grid is broken (`by_class` is a dict). The repo fixes it, and the fix ships with the 1.0.4 deploy.

### Repo (after finsim-104-complete)
- Build 2 integrated, CDX hooks applied, and the 1.0.3.1 hotfix ported (FUNDS_WAIT, short collateral at the fill, ADV).
- Suite (VERIFIER r1, 13:08 EDT): 552 tests, 0 failures, 0 errors, 0 skips on py-3.12 and on py-3.14, across 47 `test_terminal_*.py`
  files.
- VERSION is still "1.0.3"; only the gate changes it.

### Gaps in short
- News is live and is the landing page; the attention summary lives in `news.py`.
- No Dashboard, no blotters, no amend / replace, no GTD.
- No as-of booking or NAV restatement; no closed positions.
- No settlement fail state, no margin calls, no restricted cash.
- `python -m finsim` fails from the repo root.
- 22 of the directive's 25 test areas are covered, some only partly; areas 10, 11 and 22 have no tests.

## 2. Binding decisions

**D1. Owner clarification: as-of bookings, not modeled ones.**
- TRS ORCL / META, CDX IG / HY and the USD SOFR IRS are booked as Goldman Sachs contracts in F1:PB_GS, with value date 2026-10-21.
- The five options are listed fills backdated to the 2026-10-22 closing print.
  - Each premium equals that day's closing print: ORCL 3.55, META 23.59, IBIT 2.10, QQQ 700P 17.64, QQQ 675P 10.25.
  - Those prices differ from the 10-21 closes.
- These rows carry booking `AS_OF` and `historical_model` false. They are never labelled "modeled" or "hypothetical" (owner), and they
  are always labelled simulated (directive section 15):
  - options: "As-of booking - simulated fill at the 2026-10-22 closing print, entered \<time\> by the operator at the owner's
    instruction (batch IMP-F1-20261027-A)";
  - OTC: "As-of booking - simulated OTC trade with Goldman Sachs, value date 2026-10-21, entered \<time\> by the operator at the owner's
    instruction (batch IMP-F1-20261027-A)".
- The "Historical model (not an independent execution)" label exists in the tool for `historical_model` true lines (directive sections
  5 and 17; booking `HISTORICAL_MODEL`). This batch has none; the tool's tests carry one. That is how the task's "labelled modeled
  source" is met without contradicting the owner.
- The equities, PDBC, the 5 short sales and GCZ26 / ZNZ26 are preserved untouched, and their differences are reported.

**D2. Schema and rollback.**
- Additive only, through `Database.ensure_schema_ext`, called from the bind of the module that writes the column. No CHECK widening;
  SCHEMA_VERSION stays 1. New lifecycle states are derived or held in columns:
  - an order replace is CANCELLED plus `replaced_by_order_id`;
  - GTD is GTC plus `gtd_date`;
  - a settlement failure is a `fail_state` column;
  - RFQs live in their own table.
- Every `ledger.CHART` code is inserted into the `chart` table (INSERT OR IGNORE) on every open, so a GL added by a release can post on a
  database created by an older one (C11).
- Rollback before the import: 1.0.4 / 1.0.3.1 can still open a file 1.0.5 has opened (extra columns, tables and chart rows are inert to
  them), but only until the first journal line on a GL their CHART lacks (1335, 2345, 4451, 4452, 4453): older code computes NAV with
  `CHART[gl]` for every `gl_balances` row (`accounting.py` about line 1064) and fails on such a row. One OTC trade booked from the 1.0.5
  UI is enough; after that, going back to an older release needs the pre-1.0.5 install backup.
- Rollback after the import: NOT safe with an older release. The file then holds CDX contracts, lines on GLs their CHART does not know,
  restated NAV versions and AS_OF rows. The only rollback is the restore procedure of section 7 with the pre-import backup and the 1.0.5
  release; going back to 1.0.4 needs the pre-1.0.5 install backup.

**D3. Records and NAV are never forced.**
- Existing orders, fills, lots, borrows, settlements, futures VM, transfers and `nav_snapshots` rows are never altered.
- There are no balancing entries, and NAV is never forced to a workbook figure.
- Restated NAV rows are versions kept beside the originals, for every F1 account and for F1:ALL. History, the TODAY / MTD baselines and
  Performance read the latest version and label it. Every restated row carries its split: original NAV (0 where no original row
  exists), import effect by line, funding moves (as-of sweeps and transfers), interest restatement, mark-policy effect by position,
  residual 0 (C9d).

**D4. Every number shows its source, as-of time and quality.**
- Fills are labelled "Simulated fill" (or the D1 as-of labels) and are never claimed as real exchange executions.
- OTC values are MODEL (computed, with their inputs) and are never shown as dealer-confirmed.

**D5. Every execution has a parent.** It is the order id, or for immediate tickets (bonds, FX, private, OTC) and financing trades the
ticket id `T-<command id>` of the command that booked it.

**D6. Out of scope, never touched.**
- FinSim2 (`finsim\finsim2`, its tests and its news).
- The website (`site\`).
- `%USERPROFILE%\.finsim` and ports 8765 / 8865 during the build.
- The old engine code (`finsim.api`, `finsim.engines`) and `finsim.db`.

**D7. As-of dating.**
- An as-of row takes business date = the owner's value / trade date: 2026-10-21 for the OTC contracts, 2026-10-22 for the options. For
  the OTC rows this explicitly overrides the 17:00 ET accounting-day cut-off (ACCT_DAY), which would put a 19:45 ET booking on 10-22.
  The execution time shown is 19:45 ET 10-21, one minute after the GS ISDA / CSA / PBA were recorded (19:44 ET, command 1772); the
  option fills show 16:00 ET 10-22, inside their accounting day.
- The lifecycle of an as-of row before its entered day carries the business dates real time would have given: settlement on its due date,
  VM and payments of date D on D, the accrual of night D on D+1 (as every nightly accrual posts today).
- `fills.ts`, `lots.open_ts` and the contract's trade time are the as-of execution time; `entered_ts` is the true entry time.

**D8. As-of cash is funded on business-date balances.**
- An as-of cash need of date D is measured against the account's and Treasury's settled cash at the close of D (business date), not the
  current balance, and its sweep is dated D (C20). The current settled cash must stay at or above zero as well (L18 holds at every
  commit): when it cannot carry the sweep, the booking is refused and the dry run says how much to move into Treasury first.
- After the catch-up, every account's business-date settled cash must close at or above zero on every date from 2026-10-21 through the
  day before the apply. The catch-up runs the as-of sweeps per past business date (C15, at most the import's own cash) and then checks
  the whole table; anything still negative is moved by a dated, labelled internal transfer, or the import refuses (section 7).

**D9. Collateral received is restricted, netted per CSA.**
- Restricted cash = net credit support received under each PB account's CSA (GL 2330 minus GL 1320 of that account, floored at 0) plus
  securities-lending cash collateral (2520).
- It is excluded from available, spendable, buying power and SWEEP_OUT. A variation-margin or swap payment to the same CSA counterparty
  may use it (netting under one ISDA / CSA); any other need may not (C21).
- It earns no PB credit interest. CSA interest on cash collateral is not simulated, and the Unsupported page says so (Q7).

**D10. The owner's dealt terms are booked exactly.**
- TRS reset prices 129.13 / 697.52, CDX dealt spreads 64 / 352 bp, the IRS fixed rate 4.720 %, the option premiums.
- Market closes and proxies are evidence and valuation inputs only. A booking that would store any other term is refused (409) and
  writes nothing (C10, C8).

## 3. Rules for every package

1. **Ownership.** Edit only the files in your owns list (new files included); each file has one owner across this build.
   - Other files are read-only. A needed edit goes into `requests_for_others` with the exact text, and the orchestrator applies it at
     integration.
   - Files nobody owns (read-only):
     - Python: `books.py`, `calendar.py`, `clock.py`, `commands.py`, `compliance.py`, `credit.py`, `fx.py`, `hedges.py`,
       `lifecycle.py`, `lifetimes.py`, `smoke.py`, `strategies*.py`, `units.py`, `winpaths.py`.
     - `products\bonds.py`, `bonds_jobs.py`, `futures.py`, `options.py`, `private.py`, `private_credit.py`; `quotes\cache.py`,
       `http.py`, `parsers.py`, `providers.py`, `__init__.py`.
     - Static: `components\freshness.js`, `chart.js`, `chain.js`, `bookswitcher.js`, `header.js`, `hedge.js`, `lifetimes.js`,
       `privateticket.js`, `privatecredit.js`, `bondticket.js`, `fxdesk.js`, `creditdesk.js`; `pages\markets*.js` (except
       `markets_otc.js`), `instrument.js`, `watchlists.js`, `private_markets.js`.
     - Tests: `tests\helpers.py`, `tests\terminal_helpers.py`, `tests\_cdp.py` (FinSim2's).
     - The package `__init__.py` holds VERSION, which only the gate changes.
   - Shared test fixture: `tests\fixtures\terminal\schema_1031.sql` is P1's; P4 and P7 read it.
   - One sequenced exception: P1a, which runs alone, also makes the one-line import switch in P3's `api_views.py` (C3). From wave 2
     on, P3 owns `api_views.py` as listed.
2. **Start from the current tree.**
   - Re-read every file you own before editing: finsim-104-complete edited many of them, and the line numbers in this plan are as of
     12:15-13:30 EDT.
   - Build your contract hooks (section 4) FIRST, in two tiers, and report "Pn hooks landed: Cx ..." in your result (rule 8):
     - tier 1, within about 45 minutes: interfaces, columns, signatures and stubs that answer "unavailable" - C1-C6 and the C11 sync
       (P1a); C7's routes and `counts`, C8's columns, `orders.gtd_supported`, C20 / C21's `fund` / `own_funds_c` signatures (P2); C9 (a),
       (b), (c), (f), (g), (h) (P3); C10's columns, C11, C12, a C13 stub, the C14 table, command names and `rfq_rows` (P4); C15's columns,
       Treasury stamping and `sweep_in` under `ctx.asof_date`, the C16 / C21 functions (P6);
     - tier 2, as early as the work allows: the features others consume - C8's bookings, C9 (d), (e), (i), C10's booking, the C14 flow,
       C15's as-of dating, sweeps and transfers, C17, C20's business-date funding. Only P5 (wave 3) and the tests that name them need
       these.
   - Consumers code against the contract and verify once the hook is in the tree.
3. **Tests.**
   - The whole suite stays green on py-3.12 and py-3.14 (`py -3.12 <scratchpad>\run_suite.py` until P7's
     `tests\run_terminal_suite.py` lands).
   - Update the tests you own when behaviour changes on purpose.
   - Every money test is hand-worked, with the arithmetic in comments; each package's acceptance lists its money tests.
4. **Honesty.**
   - No invented prices, fills, quotes or balances.
   - Missing data shows as "unavailable" with the reason.
   - Real institution names appear only with ASSUMED, editable terms.
   - Trader wording on screen: no endpoints, package ids, internal codes or doc references.
5. **UI.**
   - Vanilla JS, ASCII, no CDN.
   - Reuse existing screens and classes plus P1's classes (C4).
   - New tables fit 1440 px with the dock open (actions visible) and scroll, not clip, at 1100 px.
   - 0 console errors; no "undefined", "NaN" or "[object Object]" on screen.
   - A page calls a route of an optional module (C2) only when `T.hasModule(name)` is true; otherwise its section shows "unavailable"
     with the reason and makes no request, so no page answers 404 while a module is still being built.
6. **Verify like Build 2.**
   - Fresh temp homes, or a fresh sqlite3-backup copy of `~/.finsim/terminal.db` (opened `mode=ro`) in a NEW folder under
     `scratchpad\checks\`.
   - `py -3.12 -m finsim serve --port 0 --home <folder> [--offline]`, or `serve(offline=True)` with a seeded FakeProvider and a
     shifted clock for fills.
   - Playwright py-3.14 headless at 1440x900 and 1100 px.
   - One server and one browser per agent at a time, each stopped with POST `/api/shutdown`, its pid confirmed gone in tasklist.
   - Real data is fetched read-only and gently.
7. **Never:**
   - deploy, write `%USERPROFILE%\.finsim`, or touch ports 8765 / 8865;
   - run any git command, or change `finsim.terminal.VERSION`;
   - stop a process you did not start, or modify FinSim2 or `site\`.
8. **Finish.**
   - Do NOT write `docs\BUILD_LOG.md` yourself: it has no owner and parallel builders would overwrite each other's lines.
   - Put ONE log line in your structured result (clock time taken from `date` in the same step as the line). The orchestrator is the
     only writer of BUILD_LOG and appends the lines in order.
   - Return the structured result: what you did, test counts, walk results, requests for others, the log line, and doc text for the
     orchestrator.

## 4. Contracts (hooks built first by their owner)

### P1 contracts

**C1. Shell registrations.**
- `core.js` ROUTES:

  | Route | Behaviour |
  |---|---|
  | `#/dashboard` | default route, and the unknown-route fallback ("no page X - showing the Dashboard") |
  | `#/news` | replaceHash to `#/dashboard`, one-time toast "That page was removed - showing the Dashboard" (no feature name, so P1's news-string search passes on it) |
  | `#/trading` | the last venue used (`T.store` `last_venue`), else US Exchange Markets |
  | `#/pending?query` | `T.pages.pending` (P2) |
  | `#/completed?query` | `T.pages.completed` (P2) |
  | `#/accounts` | `T.pages.accounts` (P6) |
  | `#/cash?query` | `T.pages.cash_collateral` (P6) |
  | `#/derivatives(/tab)` | `T.pages.derivatives` (P4) |
  | `#/history(/tab)` | `T.pages.history.hub` (P3) |

- The existing routes (`#/h/...`, `#/activity`, `#/risk`, `#/accounting`, `#/portfolio`, `#/treasury`, `#/watchlists`, `#/settings`,
  account pages) keep working.
- `index.html` loads `pages\dashboard.js`: P1a lists it as `data-optional` (so `#/dashboard` shows the not-built panel and makes no API
  call until the page exists); P1b makes it required when it lands the real page. It loads as `data-optional` these files:
  `pages\pending.js`, `pages\completed.js`, `pages\accounts.js`, `pages\cash_collateral.js`, `pages\derivatives.js`,
  `components\imports.js`.
- `api.PLANNED_STATIC` and `tests\terminal_fixture_server.py` `OPTIONAL_STATIC` list the same optional files (with `pages/dashboard.js`
  until P1b). A missing one is served as a 200 stub, so no 404 reaches the console.
- `keys.js` actions:
  - New: `page.dashboard` F12, `page.pending` F8, `page.completed` Shift+F8, `page.accounts` Shift+F4, `page.cash_collateral` Shift+F5,
    `page.derivatives` Shift+F6.
  - `page.trading` has no default key.
  - `page.history` (Shift+F10) is re-targeted to the hub.
  - `page.news` is removed.
  - `nav.orders`, `nav.positions`, `nav.cash`, `nav.transactions` and `page.activity` keep their actions with no default key.
  - The registry ends at 74 actions.
- `core.js` RUN handlers:
  - F8 / Shift+F8 on an account page open `#/pending?account=<id>` / `#/completed?account=<id>`.
  - Shift+F5 on an account page opens that account's Cash & financing page.
- `sidebar.js` follows the directive's order (P1 task 3, P1b).

**C2. Optional modules.** `server.OPTIONAL_MODULES` gains:
- `"dashboard": "dashboard"` (P1)
- `"blotters": "blotters"` (P2)
- `"navhistory": "navhistory"` (P3)
- `"collateral": "collateral"` (P6)
- `"importer": "importer"` (P5)

`api.PACKAGES` gains their ids for 501 messages. A module not yet in the tree stays "missing" (tolerated).

`core.js` (P1a) gains `T.hasModule(name)`: true when `/api/health` `modules[name]` is "bound" (read from the header's health poll,
cached). Pages gate every optional-module route on it (rule 5): the C19 import routes until P5 lands, the collateral routes until P6, the
blotter routes until P2, the Dashboard's optional providers on the server side by their own presence checks.

**C3. Attention.** `terminal\attention.py` (P1) holds a verbatim copy of `attention_lifetime`, `attention_book`, `attention_in_books`,
`attention_summary` and their helpers. The copy:
- leaves out the `news_state` block;
- makes the provider-pause count ignore the retired providers `yahoo_news`, `fed`, `newspaper` and `news_*`.

Who switches what (all in P1a, which runs alone):
- `api.py` imports from `.attention`.
- `api_views.py` line 44 becomes `from .attention import attention_lifetime` (the one cross-owner edit of this build, rule 1).
- `news.py` is deleted (P1 owns it now), with the tests and fixtures that import it (P1 split). It must go in P1a: `news.py` imports
  `config.NEWS_*`, `server.py` guards its import against ModuleNotFoundError only, and `test_terminal_news.py` imports both at module
  level, so a later removal of those constants (P1b) would otherwise break the server start and the suite for every builder.

**C4. CSS classes.** `terminal.css` adds `.filterbar`, `.stage-chip` (with `.stage-working`, `.stage-partial`, `.stage-held`,
`.stage-queued`, `.stage-filled`, `.stage-cancelled`, `.stage-expired`, `.stage-rejected`, `.stage-settling`, `.stage-failed`),
`.badge-asof`, `.badge-model`, `.drawer`, `.drawer-tabs` and `.kv-grid`. Pages use only these and the existing classes; there are no
page CSS files.

**C5. Refusal-recorder hook.**
- `api.add_refusal_recorder(kinds, fn)` is called from `api.run_command` for every 4xx result of those command kinds whose code is not
  in NOT_REJECTIONS, as `fn(req, kind, payload, result)`.
- It never changes the HTTP answer and never raises: an exception is logged.

**C6. Unsupported and planned rows.**
- Static rows:
  - "Extended-hours trading: NOT_SIMULATED - an order entered outside the regular session rests until the next regular open".
  - "Forced liquidation: never - positions are never sold for you; margin calls are recorded and met from cash / Treasury / PB headroom
    or stay open". The old "margin calls NOT_SIMULATED" row is reworded.
  - "CSA interest on cash collateral: NOT_SIMULATED - variation margin received under a CSA is restricted and earns no PB credit
    interest; collateral posted earns none" (D9).
- PLANNED rows disappear by themselves when their module ships: margin calls (`collateral`), settlement failure simulation
  (`collateral`), OTC dealer quotes (`swaps.rfq_rows`, C14), GTD / next-session orders (`orders.gtd_supported`, C7), historical import
  (`importer`), CSA securities collateral (`collateral.pledges_view`, C16). A function probe is `callable(...)` on the named function.

### P2 contracts (blotters.py, execution.py)

**C7. Blotter routes.**
- `GET /api/blotter/pending?scope=&account=&asset_class=&status=&instrument=&limit=&offset=`
  - `status` = `open` (default: WORKING, PARTIAL, SUSPENDED, WAITING_PARENT plus open RFQs), `filled`, `cancelled`, `expired`,
    `rejected` (from `order_rejections`) or `all` (the order history).
- `GET /api/blotter/executions?scope=&from=&to=&instrument=&account=&asset_class=&counterparty=&status=&source=&batch=&format=csv&limit=&offset=`
  - One row per execution, never merged, from: every fill (all origins); every `fx_deals` row; every `otc_contracts` booking,
    termination and maturity; and the financing trades - repo / reverse repo open, roll and close (`repos`), cash loans drawn and
    repaid (`loans` CASH_LOAN), private-credit commitments, fundings and repayments (`private_loans`), securities lent / recalled /
    returned (`sec_loans`) and stock borrows opened / returned (`borrows`), each with asset class "Financing" and parent
    `T-<command id>`. Automatic PB financing draws are funding, not trades: they show in Cash & Collateral (section 9).
- `GET /api/executions/{id}`
- `GET /api/orders/{id}` takes over the existing path, adding settlements and `?review=1`.
- `PATCH /api/orders/{id}` (amend) and `POST /api/orders/{id}/replace`.
- `blotters.counts(services, scope)` returns `{working, partial, held, queued, waiting_parent, rfq_open, rejected_today, settling,
  failed}`.
- `orders.gtd_supported(services) -> True` (`orders.py`): the C6 probe for GTD and next-session orders.
- The stage vocabulary is published with `tables.add_conventions`.

**C8. As-of listed booking.** Two commands; both refuse actor `ui` (403 AS_OF_FORBIDDEN).
- `trade.book_as_of`, payload exactly:
  `{account_id, instrument_id, instrument_spec, side, qty, price, order_type: "LIMIT", tif: "DAY", fee_rule, as_of: {trade_date,
  exec_ts, batch_id, line_key, historical_model?}}`.
  - `instrument_spec` = `{occ, underlying_id, expiry, strike, right, style, multiplier, chain_row}`. When the instrument does not exist,
    the command creates it with `InstrumentMaster.ensure_option(tx, occ, underlying, chain_row)` inside its own transaction, so the
    contract's creation is in the command row (audit trail).
  - `fee_rule` is an enumerated value: `SCHEDULE` (the account's fee schedule at the trade date; DEFAULT USD 0.65 per contract) or
    `NONE`.
  - No prose, evidence or apply-order text in the payload: those live in the batch line, outside the command.
- `trade.book_as_of_package`, payload exactly:
  `{account_id, package: {strategy_key, fill_mode, label}, legs: [{line_key, instrument_id, instrument_spec, side, qty, price}, ...],
  order_type, tif, fee_rule, as_of: {trade_date, exec_ts, batch_id}}`.
  - ONE command, request id = the package key; the legs book in the listed order (long before short), each with its own order and fill
    and `import_key` = its `line_key`.
  - One `hedge_packages` row; one option-margin recompute.

Each booking writes:
- an order row: FILLED, LIMIT at the price, DAY, `created_ts` = exec_ts, booking `AS_OF` (or `HISTORICAL_MODEL` when
  `as_of.historical_model` is true), `entered_ts` = now, `import_key` = line_key;
- order_events SUBMITTED ("as-of; entered \<now\> by the operator at the owner's instruction") -> FILLED, at exec_ts;
- the fill through `execution.apply_fill` with `FillSpec.trade_date` and a new `FillSpec.exec_ts`, so `fills.ts` and `lots.open_ts` =
  exec_ts;
- every journal entry with business_date = trade date (through `ctx.asof_date`, C11);
- the settlement due at T+1 from the trade date;
- the order is FILLED at exactly the payload's price, quantity and side; anything else is 409 ASOF_TERMS_MISMATCH and nothing is written.

Duplicates:
- A second command with the same request_id is a replay; a different payload with the same key is 409 REQUEST_ID_REUSED.
- An order or fill that already carries the `import_key` is 409 DUPLICATE_IMPORT.

Columns added in `orders.bind`:
- orders: `booking`, `entered_ts`, `import_key`, `replaces_order_id`, `replaced_by_order_id`, `amend_count`, `gtd_date`,
  `activate_ts`.
- fills: `booking`, `entered_ts`, `import_key`.

**C20. As-of funding** (P2 implements in `execution.py`; P5 drives it).
- When `ctx.asof_date` = D is set, `execution.fund` measures:
  - own cash: the account's business-date settled cash at the close of D (`fill` mode: plus receivables minus payables due by the
    settle date, as of business date D), through C9h, less `collateral.restricted_c(..., as_of=D)` (C21);
  - Treasury availability: Treasury's business-date settled cash at the close of D, less the book's reserve.
- The sweep it books is dated D (business_date D, through C15's Treasury stamping). `treasury.sweep_in` (P6, C15) checks the same
  business-date figure under `ctx.asof_date`, so it never refuses an as-of sweep on the current balance; P2 depends on that P6 hook.
- The current balance still binds: the sweep must leave Treasury's current settled cash at or above zero (L18 checks it at commit). When
  it would not, `fund` refuses 409 ASOF_CURRENT_FUNDS before anything is written, naming the current balance and the need; the dry run
  reports it and the owner moves cash into Treasury first (an ordinary transfer, dated that day). At 14:54 current Treasury held
  4,948,770.57 against the import's peak draw of about 2.14M (1,701,918.50 on the bookings, 436,835.22 at the option settlement).
- PB financing is used only if the account has it on.
- It guarantees nothing about later dates: P5's step C3 checks the whole business-date table (D8).
- Without `ctx.asof_date`, `fund` is unchanged.

### P3 contracts

**C9. Accounting hooks.**
- (a) `accounting.reserved_for_order(conn, order_row, snapshot, master) -> cents`: the per-row body of `reserved_c`. The totals are
  unchanged.
- (b) Cash adjusters:
  - `accounting.add_cash_adjuster(key, label, fn(conn, account_id, ccy, snapshot, master) -> cents)`. Adjusters are subtracted in
    `_available_parts` (so in `available_c`, `spendable_c`, `immediate_available_c` and buying power).
  - They are listed in `CashView` as `adjustments [{key, label, amount_c}]`.
  - `accounting.adjustments_c(conn, account_id, ccy, snapshot, master)` gives SWEEP_OUT their total.
- (c) OTC positions:
  - Rows of kind SWAP / OTC use `swaps.position_fields(...)` (C13) when present, else today's overlay.
  - Their cost basis is the per-contract balance of GL 1335 (asset) less GL 2345 (liability), and unrealised = MV - cost.
  - 1335 and 2345 are contract cost GLs (`ledger.OTC_COST_GLS`, C11). Like the position cost GLs 1700 / 2900 they stay OUT of
    `NAV_ASSET_GLS` / `NAV_LIAB_GLS`, so in assets / liabilities the contract's MV (`swaps_pos_c` / `swaps_neg_c`) replaces its cost and
    A - L = NAV holds, while NAV (S + U) changes by the cost of dealing only (P3 money test d). At the CDX IG booking: A - L = 51,075.90
    receivable - 2,583.33 accrued premium - 49,176.13 MV = -683.56 = the NAV change; with 2345 in the liability set it would be off by
    48,492.57. They are not in `COST_LONG_GLS` / `COST_SHORT_GLS` either: integrity check L9 ties those to open lots.
- (d) `accounting.nav_as_of(scope, d)` and the command `nav.restate`:
  - Positions and cash alike come from journal entries with business_date <= d (a fill at 19:58 ET on D counts from D+1, its
    business date).
  - Marks of d: equity / ETF CLOSE, futures SETTLE, the OTC `otc_vm.mark_c` of d (or the last on or before d), options: the recorded
    CLOSE of d (the Cboe official close where it was recorded, else the Yahoo last trade, quality stale when that day's volume was 0).
    Weekends and holidays carry the previous trading day's marks.
  - `nav.restate {lifetime_id, from_date, through_date, reason, batch_id, groups}` writes `nav_snapshot_versions` (navhistory.py);
    `nav_snapshots` is never replaced. `groups` = `[{key, label, refs: {contract_ids, order_ids, fill_ids, settle_ids, transfer_ids,
    entry_ids}}]`: P5 passes one group per import line plus one `funding` group (the C2 as-of sweeps and C3 transfers, which belong to no
    line). Every journal entry the import created belongs to exactly one group (a line's command, the lifecycle entries of its contract /
    order / fill / settlement and the sweeps linked to them) or to the interest effect (C17); an unassigned entry leaves a residual and the
    restatement refuses.
  - It restates every account row and the `{lifetime}:ALL` row of every date in the range, so each account's TODAY baseline moves too.
    Where no original row exists but the restated figures are not zero (F1:PB_GS on 10-21: the account opened at 19:44 ET, after that
    day's 17:45 snapshot) it writes a version row with `original_nav_c` 0, labelled "added by restatement".
  - Each version row stores the split: `original_nav_c` (the `nav_snapshots` row, or 0), `import_effect [{key, amount_c}]` (by group;
    the `funding` group nets to 0 at the lifetime level, not per account), `interest_effect_c` (the C17 corrections dated <= d),
    `mark_policy [{instrument_id, original_mark, dated_mark, amount_c}]` (preserved positions: dated mark minus the original snapshot
    mark), `dating_c` (only for an original snapshot flagged late: the entries it counted "from the ledger as it stood" that belong to
    another business date, listed), and `residual_c`, which must be 0 for every account and for `{lifetime}:ALL` (else the restatement
    refuses).
  - Idempotent: a date whose latest version for the same `batch_id` already has the same figures gets no new version, so a re-run or a
    second dry run on the result writes nothing.
  - Readers take, per account and date, the latest version if any, else the original; a date with only a version row counts.
    `_last_snapshot`, Performance, History and the TODAY / MTD / YTD baselines read it that way.
- (e) `performance.returns_summary(services, scope)` returns `{today_pct, mtd_pct, ytd_pct, itd_twr_pct, itd_simple_pct, as_of,
  basis}`.
- (f) JS: `T.pages.accounting.renderCashTab(el, {book, account})`.
- (g) A settlement with `fail_state` FAILED is not an integrity failure; it is shown as FAILED.
- (h) `accounting.cash_view_at(conn, account_id, ccy, d) -> {settled_c, receivable_c, payable_c}`: business-date balances (journal lines
  with business_date <= d). Used by C20, C15 (`sweep_in` under `ctx.asof_date`, `cash.asof_sweep`), C21 (`restricted_c(as_of=d)`), C17
  and P5's business-date cash table.
- (i) The integrity checks that read snapshots (M4 continuity, M5 gaps) read the latest-version chain, so the first live snapshot after a
  restatement chains to the restated row without a WARN.

### P4 contracts

**C10. `otc.book_as_of`.**
- Payload: `{account_id, kind, terms, as_of: {trade_date, value_date, exec_ts, batch_id, line_key, historical_model?}}`. Actor `ui` is
  refused; the UI's `otc.book` and the RFQ flow are unchanged.
- The owner's dealt terms are part of `terms` and are booked exactly (D10):
  - TRS `reset_px` (the initial price), units, side, tenor and reset frequency;
  - CDX `dealt_spread_bp`, notional, side, index, series, tenor;
  - IRS `fixed_rate`, notional, side, tenor, leg conventions and `calibrate_curve_spread_to_rate: true`.
- Inputs pinned to the trade date are valuation inputs only:
  - the curve: `CurveStore.for_date` (C12);
  - SOFR: `fixing_for(trade_date)`;
  - the CDX proxy: `rates.as_of_le(series, trade_date)` plus the account basis (the in-memory RateStore, which P5 reloads after it
    records the pack's observations, P5 task 4);
  - the TRS underlying CLOSE of the trade date (day-0 VM = (reset_px - close) x units, sign by side; 0 when they are equal).
  - When an input is missing: 409 ASOF_INPUT_MISSING, and nothing is written.
- Refusals that write nothing:
  - 409 ASOF_TERMS_MISMATCH when the contract it would store differs from the payload in any dealt term;
  - 409 ASOF_BASIS_NOT_CALIBRATED when the account's CDX basis would not deal the payload's spread (|proxy + basis + half-spread -
    dealt| > 0.01 bp for buy protection): run S4 first, so no day-one basis gain or loss is booked.
- Dates and settlement (D7):
  - start / effective = trade date; IM and upfront entries carry business_date = the value date (overriding ACCT_DAY);
  - the CDX initial payment's settlement is due at T+1 from the trade date, with `ref_type` `otc` / `ref_id` contract id;
  - TRS and IRS accrual nights start at the start (value) date; a CDX keeps the state `swaps_cds.book` seeds (premium leg from the IMM
    accrual start with the rebate as its cumulative and `accrued_through` = the trade date), so its first new night is the trade date + 1
    (10-22) and night 10-21, which the rebate covers, is never accrued twice; the accrual of night D posts with business date D+1; VM
    and payments of D post on D.
- SWAP_VM for a past date d uses the inputs of d (TRS CLOSE of d; CDX proxy of d plus basis; the curve of d for discounting), sets
  `ctx.asof_date = d` so its funding sweep and entries share d, and is CAUGHT_UP only when d's input truly does not exist.
- IRS `calibrate_curve_spread_to_rate`: at booking it sets `curve_spread_bp` so the dealt rate is par + spread + half-width (about
  -18.0 bp on 10-21). It is stored in terms and used in every mark, DV01 and VM.
- `otc_contracts` columns: `booking`, `entered_ts`, `import_key`, `trade_date`, `value_date`, `confirm_status`, `confirmed_ts`.
- A contract that already carries the `import_key` is 409 DUPLICATE_IMPORT.

**C11. Ledger and chart.**
- `ledger.post`: business_date = `entry.business_date`, else `getattr(ctx, "asof_date", None)`, else today.
- New GLs:

  | GL | Name | Classified as |
  |---|---|---|
  | 4451 | Swap financing leg (TRS funding) | financing |
  | 4452 | Dividend equivalents | income |
  | 4453 | Swap coupons and CDS premium | financing |
  | 1335 | OTC contract cost (upfront / premium paid) | contract cost basis (asset) |
  | 2345 | OTC contract cost (upfront / premium received) | contract cost basis (liability) |

- 4450 stays the realised "Swap results" GL: return leg, STM, termination, maturity.
- `ledger.OTC_COST_GLS = ("1335", "2345")`: contract cost GLs, kept out of `COST_LONG_GLS` / `COST_SHORT_GLS` (which L9 ties to open
  lots) and out of the NAV asset / liability sets (C9c).
- `ledger.ensure_chart_rows(conn, codes=None) -> int` (P4): INSERT OR IGNORE of every `CHART` code (or the given ones) into `chart`.
  `swaps.bind` calls it.
- `Database.open` (P1) inserts every `ledger.CHART` row (INSERT OR IGNORE, the `_seed` loop factored into a `_sync_chart`) on EVERY
  open, inside the migration transaction. Today the chart is seeded only for a brand-new file, and `lending.bind` adds only the lending
  codes; the owner's file has 52 rows. Any package that adds a GL relies on this sync.

**C12. `quotes\rates.CurveStore.for_date(d)`.** A curve view from `curve_points` UST_PAR, as of d or the last business date before it
within 3 business days. It reads `curve_points` from the database (a per-date cache refreshed by `load_persisted`), never only the
in-memory latest curve, so a curve the importer records is visible. Its `discount_factors`, `par_rate` and `annuity` use the same method
as the live store; it returns None when absent.

**C13. `swaps.position_fields(services, row, snapshot, now_ts)`.**
- Returns `{mv_c, unrealised_c, mark_label, quality, as_of, units, unit_label, entry, current, notional_c, extra}`.
- The intraday MV is the live model mark when it is fresh ("model, intraday"); otherwise the last-VM overlay ("as of the last VM").

**C14. RFQs and confirmation.**
- `rfqs` table; statuses REQUESTED -> QUOTED -> ACCEPTED / DECLINED / EXPIRED.
- The simulated dealer is a lifecycle product job `RFQ_DEALER`, registered from `swaps.bind` through the existing
  `lifecycle_eod.add_product_job` hook. On every scheduler tick (about 30 s) it quotes REQUESTED RFQs and expires QUOTED ones past
  their TTL. It needs no matcher, so it works in offline runtimes and with the matcher stopped. The import never uses RFQs:
  `otc.book_as_of` books directly.
- `swaps.rfq_rows(services, scope)` feeds the Pending blotter.
- Commands: `otc.rfq.request {account_id, kind, terms}`, `otc.rfq.accept {rfq_id}`, `otc.rfq.decline {rfq_id}` (P4). P2's C5 refusal
  recorder lists them (P2 task 8), so a refused accept (for example after the quote's TTL) shows under the Rejected filter.
- `otc_contracts.confirm_status` and `confirmed_ts`.

### P6 contracts

**C15. Settlements and as-of cash.**
- Columns on `settlements`: `fail_state`, `fail_reason`, `failed_ts`, `resolved_ts`, `resolution`, `ref_type`, `ref_id` (added in
  `collateral.bind`). Any writer may repeat the identical `ensure_schema_ext` statement; it is idempotent.
- Commands `settlement.fail` and `settlement.resolve`; `collateral.settlement_state(row) -> {stage, label}`. P2's Completed Trades
  drawer offers them (C7).
- As-of dating in SETTLE_DUE: a settlement whose fill or contract carries booking `AS_OF` posts with business_date = its due date and
  `ctx.asof_date` = due date.
- `treasury.py` stamps `transfers.business_date` (and its journal entry) from `ctx.asof_date` when it is set.
- `treasury.sweep_in` under `ctx.asof_date` = D checks Treasury availability on the business-date figure C20 uses
  (`accounting.cash_view_at(TREASURY, D)` less the book's reserve) instead of `sweepable_c` (the current balance), and leaves the
  current-balance floor to C20 / L18. Without `ctx.asof_date` it is unchanged.
- `cash.asof_sweep {lifetime_id, accounts, from_date, through_date, batch_id, groups}` (actor import only): for each date d ascending and
  each listed account, sweep = min(excess, import cash), where excess = settled - restricted as of d (C21) - payables - buffer at the
  close of d (business date, C9h) and import cash = the account's settled cash created by the import's groups (their 1010 lines with
  business_date <= d) less earlier as-of sweeps of that account; when sweep > 0, one SWEEP_OUT to Treasury dated d, labelled "as-of
  sweep", idem `asofsweep:<batch>:<account>:<ccy>:<d>`. Restricted collateral is never swept, and the owner's pre-import idle cash is
  never swept on a past date real time did not sweep it (US Exchange held +509,392.94 of pre-import excess at the close of 10-22).
- `cash.asof_transfer {from_account, to_account, amount, business_date, reason, batch_id, line_key}` (actor import only): an internal
  transfer dated `business_date`, stored as `transfers.kind` TRANSFER (the CHECK lists six kinds and is not widened, D2) with
  `linked_type` "asof_funding" and `linked_id` = the line key; its journal entry has kind ASOF_FUNDING (`journal_entries.kind` has no
  CHECK) and the memo "as-of funding". NAV-neutral; L5 nets it.

**C16. Collateral functions.**
- `collateral.summary(services, scope)` returns `{margin_required_c, margin_posted_c, margin_excess_c, collateral_posted_c,
  collateral_received_c, restricted_c, encumbered_c, open_calls, failed_settlements, by_counterparty[]}`.
- `collateral.position_requirements(services, account_id)` returns `{iid: {margin_c, collateral_c, basis}}`.
- `collateral.margin_calls_rows(services, scope, from, to)` feeds P3's Activity type "margin calls"; `collateral.summary` and
  `position_requirements` feed P3's Risk Reg T panel.
- `collateral.custodian_of(account_row)`.
- `collateral.pledges_view(services, scope)` returns the CSA pledges (account, instrument, face, haircut, eligible value, pledged to,
  status): the C6 probe for CSA securities collateral and the Cash & Collateral pledges section.
- `lending.lender_label(name) -> str`: a stored lender name ending "(modelled)" is shown as "(assumed terms)" (rule 4), e.g. "street
  lender (modelled)" -> "street lender (assumed terms)"; stored rows stay untouched and new borrows store the new wording. P2's Completed
  Trades, P3's Portfolio counterparty column and P6's pages show lenders through it.

**C17. `cash.reaccrue`.**
- Payload: `{lifetime_id, accounts, kinds, from_night, through_night, reason, batch_id}`; kinds = the balance-based nightly accruals
  `cashint` (Treasury), `pbcredit` and `pb` (PB accounts).
- For every listed account whose business-date balance changed, it recomputes each night's base on business-date balances (pbcredit
  base = max(0, settled - restricted as of that night), D9) and posts the difference from what was accrued. Each night keeps the rate
  its original accrual used (from that accrual's event); only the base changes.
- Rounding: the restated accrual follows `accrue_segment`'s rule (a segment = consecutive nights with the same base and rate; its
  cumulative = round half-even(base x rate x n / basis); each night = cumulative minus the previous cumulative). A night's correction =
  its restated amount minus the amount originally posted for it.
- `through_night` defaults to each account's `accrual_state.accrued_through` when the command runs, so it also corrects a night first
  accrued inside the catch-up before the as-of sweeps.
- Each correction carries business_date = night + 1 (the day that night's accrual posted) and idem key
  `reaccrue:<batch>:<account>:<kind>:<night>`.

**C21. Restricted collateral and CSA netting** (P6 specifies, P2 implements the funding side, P4 passes the flag).
- `collateral.restricted_c(conn, account_id, ccy, csa_netting=False, as_of=None) -> cents` (P6) = max(0, VM received (GL 2330) - VM
  posted (GL 1320)) of the account (one CSA per PB relationship) + securities-lending cash collateral received (GL 2520), all in natural
  signs (`ledger.natural`); with `csa_netting=True` only the 2520 part. With `as_of` = d it reads business-date balances (journal lines
  with business_date <= d, C9h): C20's as-of funding, `cash.asof_sweep` (C15) and `cash.reaccrue` (C17) use the restricted amount of
  that date, never today's (close of 10-22: 489,357.00 - 309,027.55 = 180,329.45, not today's 291,589.14).
- P6 registers it as the C9b adjuster "restricted (collateral received)", so available, spendable, buying power and SWEEP_OUT exclude
  it.
- `execution.own_funds_c(..., immediate=True, csa_netting=False)` and `execution.fund(..., csa_netting=False)` (P2) subtract
  `restricted_c(..., csa_netting)` from settled cash when the collateral module is bound (else today's behaviour).
- P4's variation-margin, swap-payment, CDX-coupon and termination payments pass `csa_netting=True`: a payment to the CSA counterparty may
  use cash that is that counterparty's collateral; every other immediate need (a borrow collateral top-up, a settlement) may not.

### P7 contract

**C18.** `python -m finsim import ...` calls `finsim.terminal.importer.main(argv)` (imported lazily). When the module is missing it
prints "not in this build" and exits 2.

### P5 contract

**C19. Import routes and panel.**
- `GET /api/import/batches`, `GET /api/import/batches/{id}`, `POST /api/import/report` (read-only).
- JS: `T.components.imports.render(el, {book})`. P3's History hub mounts it, and P2's Completed Trades uses the batch list for its
  Source / batch filter; both call these routes only when `T.hasModule("importer")` is true (C2), else they show "no import batches (the
  import tool is not in this build)".

## 5. Order of work

Section 15 runs this order as two releases:
- phase 1 (1.0.5): steps 1-2, Part A of P2 / P3 / P4 / P6 in the wave-2 pool, P5's phase-1 scope, P8-lite and the gate;
- phase 2 (1.0.6): P1b, Part B of P2 / P3 / P4 / P6 and P7 in the pool, the rest of P5, the full P8 and the gate.

The steps below hold inside each phase as section 15 adjusts them. Every dry run of the real batch, in either phase, runs between 00:00
and 16:00 ET (15.1 rule 7).

1. **Precondition.** finsim-104-complete has finished: integrate, port, CDX hooks, verify on an owner-DB copy, fix. Its suite is green
   on both interpreters.
2. **Wave 1: P1a alone** (about 1.5-2 h; the P1 section lists exactly what it holds). It lands the shell hooks C1-C6, the chart sync
   (C11, P1 part), the news-module retirement other packages' code and tests touch (C3), the duplicate-instance message, the 1.0.3.1
   schema fixture and the P1 goldens. Nothing else runs while it works.
3. **Wave 2: a pool of at most 4 builders**, all starting after P1a. In phase 1, Part A of P2, P3, P4 and P6 start together;
   phase 2 follows section 15.9. In the full order: P1b, P2, P3 and P4 start together; P6 takes the first free slot
   (normally P1b's, about 1.5-2 h in) and P7 the next one.
   - Why this order: P1b rewrites files every builder imports or loads (`api.py`, `server.py`, `db.py`, `config.py`,
     `quotes\worker.py`, `tables.py`, `core.js`, `index.html`, `terminal_fixture_server.py`), so it runs early, while the others are
     still coding, never during their final walks; P6's hooks are consumed latest (P2's drawer and funding, P3's pages, P5), so it waits.
   - P1b saves every edit of a shared file whole, in one write, and re-runs the suite green after each; `quotes\worker.py` drops its
     `NEWS_INTERVALS` import before `config.py` drops the constant.
   - Requests for P1's files from wave-2 packages are applied at integration, after P1b has landed. Every builder receives the P1a / P1b
     definition with its own section.
   - Each wave-2 package lands its tier-1 hooks first (rule 2): P2 C7, C8's columns, `orders.gtd_supported`, the C20 / C21 signatures;
     P3 C9 (a)-(c), (f)-(h); P4 C10's columns, C11, C12, a C13 stub, the C14 table and command names; P6 C15's columns, Treasury
     stamping and `sweep_in` under `ctx.asof_date`, C16, C21. Then the tier-2 features.
   - Hook dependencies inside the wave:
     - P2 uses P3 C9a / C9h; P4 C11 (`ledger.post` business date) and C14 (RFQ rows and command names); P6 C15 (Treasury stamping and
       `sweep_in` under `ctx.asof_date`, without which an as-of sweep is refused on the current balance), C16 (`lender_label`) and C21
       (`restricted_c` with `as_of`);
     - P3 uses P4 C11 / C13 and P6 C16; its as-of test (a) books through P2's C8 (tier 2) and, until that lands, posts the day-1 buy with
       `ledger.post` and `entry.business_date` (C11); its acceptance (d) runs after P4 task 7 (upfront to 2345, tier 2);
     - P4 uses P3 C9c, P2's `fund(csa_netting=...)` (C21) and C15's `ref_type` / `ref_id` settlement columns for the CDX initial
       payment (P4 adds them itself with C15's identical `ensure_schema_ext` statement, so it does not wait for P6);
     - P6 uses P3 C9b / C9f / C9h, P2's `booking` column on orders / fills (C8) and P4's on `otc_contracts` (C10) for the SETTLE_DUE
       as-of dating, and C20 for funding.
4. **Wave 3: P5.** It needs every as-of hook (tier 2); its acceptance is the dry run of the real batch on a fresh owner-DB copy.
5. **Wave 4: P8.**
6. **Gate (orchestrator, section 12).**

Machine notes:
- Memory is tight. Run at most 4 builders, and at most one server and one browser per builder.
- If a run is cut off, launch a remainder workflow for the unfinished packages; never resume a stopped concurrent run.
- Pass each builder its FULL package section plus sections 2-5, 13-14 and 15, never sliced (P1a and P1b: the whole P1 section with
  its split).
- Give the owner a plain-English progress line with an ETA every hour.
- The orchestrator is the only writer of `docs\BUILD_LOG.md` (rule 8).

## 6. Packages

Path prefixes used in this section:
- **T** = `C:\Users\logan\intelligence-terminal\finsim\finsim\terminal`
- **X** = `C:\Users\logan\intelligence-terminal\finsim\tests`

### P1 - Shell, navigation, Dashboard, news removal and the chart sync

**Owns**
- Python:
  - `T\attention.py` (new), `T\dashboard.py` (new), `T\news.py` (deleted by P1a; it was P3's in revision 2)
  - `T\api.py`, `T\api_products.py`, `T\server.py`, `T\config.py`, `T\db.py`, `T\tables.py`, `T\quotes\worker.py`
  - in P1a only, line 44 of P3's `T\api_views.py` (rule 1's sequenced exception, C3)
- Static:
  - `T\static\index.html`, `T\static\core.js`, `T\static\keys.js`, `T\static\terminal.css`
  - `T\static\components\sidebar.js`, `T\static\components\attention.js`
  - `T\static\pages\news.js` (deleted), `T\static\pages\dashboard.js` (new), `T\static\pages\settings.js`
- Tests:
  - `X\test_terminal_news.py` (deleted), `X\test_terminal_attention.py` (new), `X\test_terminal_dashboard.py` (new)
  - `X\test_terminal_static.py`, `X\test_terminal_api.py`, `X\test_terminal_reconcile.py`, `X\test_terminal_core.py`,
    `X\test_terminal_server.py`, `X\test_terminal_bus.py`, `X\test_terminal_quotes.py`
  - `X\terminal_fixture_server.py`
  - `X\fixtures\terminal\news\` (deleted), `X\fixtures\terminal\api\` (`news.json` deleted; `health*.json`, `state.json`,
    `settings.json` regenerated), `X\fixtures\terminal\gen_api_fixtures.py`
  - `X\fixtures\terminal\schema_1031.sql` (new: the 1.0.3.1 schema as SQL text; P4 and P7 read it)

**Depends on:** nothing. **Sections:** 2, 13 (Dashboard figures), 16, 19 (duplicate-instance message).

**Gaps:** UI-01, UI-02 (deleting `news.py` included), UI-03, UI-04 (`attention.py`, `api.py`, the `api_views.py` switch, Dashboard),
UI-05, UI-06, UI-07 / UI-08 / UI-09 / UI-10 / UI-11 / UI-12 (route, sidebar and key only; the pages belong to P2, P6,
P4 and P3), UI-13, UI-15, UI-16, UI-17, UI-20, OMS-16, PORTFOLIO-07 (the Dashboard), STARTUP-03, TEST-07.

#### Split: P1a, then P1b

P1 is built in two steps by two builders (section 5). Task numbers refer to the task list below.

**P1a** (first, alone, about 1.5-2 h): every P1 change another package's code, tests or browser walks depend on.
- Task 1 whole: C1 (the routes, RUN handlers, the `keys.js` registry with its 74 actions, the `index.html` tags, PLANNED_STATIC /
  OPTIONAL_STATIC with `pages/dashboard.js` listed as optional until P1b), C2 (OPTIONAL_MODULES, PACKAGES, `T.hasModule`), C3
  (`attention.py`, the `api.py` and `api_views.py` switches, `news.py` deleted), C4, C5, C6, the chart sync and the goldens.
- From task 2, the news pieces other packages' tests or walks touch: delete `news.py`, `pages\news.js` and
  `X\fixtures\terminal\news\`; remove the `api_products.py` `news()` handler and its route (`/api/news` answers 404); in `index.html`
  drop the `#news` root and the `pages\news.js` tag; in `core.js` drop the `#news` container switch and the notBuilt "Daily News"
  branch; in `tests\terminal_fixture_server.py` drop the `/api/news` route and `pages/news.js` from OPTIONAL_STATIC.
- Task 6 (the duplicate-instance and port messages, the lock JSON `code`): P7's launch acceptance asserts this wording.
- Task 7 (`schema_1031.sql`): P4 and P7 read it.
- From task 8, every test change these need so both suites stay green: delete `X\test_terminal_news.py`; move the two attention tests to
  `X\test_terminal_attention.py`; remove `test_news_route_with_a_stub_store`; repoint D12; the keybind sample action `page.dashboard`
  (the `page.news` keybind tests); the static tests (REQUIRED_IDS without `news`, routes, `#/news`, 74 actions, OPTIONAL_STATIC and the
  fixture server); the server message tests; the chart sync test.
- Acceptance proven by P1a: item 1; from item 2 the chart sync, integrity ok, the golden attention equality and the NAV identity (both
  captured before P1a's first edit and compared after C3 / C11 on the same copy); from item 3 the routes, the two toasts, F12 / F8 /
  Shift+F10 and 0 console errors on the shell (other packages' pages and the Dashboard as stubs).
- Result line: "P1a hooks landed: C1 C2 C3 C4 C5 C6 C11-sync"; P1b, P2, P3 and P4 start on it.

**P1b** (in the wave-2 pool, about 1.5-2 h): the rest of P1.
- Task 2's remaining removals, in this order: `quotes\worker.py` (the slots and the `NEWS_INTERVALS` import), then `config.py` (the
  constants and `newspaper_index()`); the `server.py` news block (`_offline_getter`, `rt.news`, `modules['news']`; the `news_enabled`
  kwarg stays); the `api.py` news package, health key and setting; `db.py` (`news_enabled` default, `news_items` DDL and index,
  TABLES); `tables.py`'s NEWS convention; `attention.js`; the news keys of `core.js`'s `T.PROVIDER_LABEL`; the `index.html` meta
  description and header comment; `settings.js` (Market data); the api fixtures regenerated.
- Task 3 (sidebar), task 4 (the Dashboard: `dashboard.py` and the real `pages\dashboard.js`, which becomes required: out of
  `data-optional`, PLANNED_STATIC and OPTIONAL_STATIC), task 5 (keys UI and the KEYBINDS text) and the rest of task 8.
- Every edit of a shared file is saved whole in one write, and the suite is re-run green after each (section 5).
- Acceptance proven by P1b: item 1 again; from item 2 the Dashboard (its own work within budget, task 4), the worker without `news_*`
  slots, and a re-check of the attention block and NAV against P1b's own capture taken just before its first edit (wave-2 packages
  change attention items and accounting meanwhile, so the P1a goldens are not compared again); item 3 on P1's own pages (Dashboard,
  sidebar order and keys, Settings > Market data, the news-string search, the meta description). Cross-page checks are P8's.

#### Task

**1. Hooks (first ~45 min; P1a).**
- Build C1, C2, C3, C4, C5 and C6.
- Chart sync (C11, P1 part): factor the `_seed` chart loop of `db.py` into `_sync_chart(conn)` and call it on EVERY open, inside the
  migration transaction (INSERT OR IGNORE of every `ledger.CHART` row). Today `_seed` runs only for a brand-new file (version 0), so the
  owner's file has 52 chart rows and a line on a new GL is refused by the `journal_lines.gl` foreign key.
- Before changing anything, capture on one fresh owner-DB copy the golden `/api/news` attention block of the Fund book (the C3
  equality check) and the Fund book's `/api/portfolio` NAV (the NAV identity); compare both on the same copy after C3 and C11.

**2. Remove news (directive section 2; split between P1a and P1b as above).**
- `quotes\worker.py`: drop the `news_yahoo`, `news_fed` and `news_paper` slots and the `NEWS_INTERVALS` import.
- `config.py`: drop `NEWS_YAHOO`, `NEWS_FED_RSS`, `NEWS_PAPER_URL`, `NEWS_PAPER_INDEX_URL`, `NEWS_INTERVALS` and `newspaper_index()`.
  Keep `repo_root()` and `holidays_path()`.
- `server.py`: drop the news block, `_offline_getter`, `rt.news` and `modules['news']`.
  - Keep the `news_enabled` kwarg of `build_runtime`, accepted and ignored, documented "removed in 1.0.5". Eight test files still
    pass it.
  - Skip the retired providers' `provider_health` rows on load and never show them; leave the stored rows inert.
- `api.py`: drop PACKAGES `news`, the health `news` key, the setting `news_enabled` (SETTING_KEYS, SETTING_LABELS, `_check`; a PATCH
  answers 400) and the news import.
- `db.py`: drop `DEFAULT_SETTINGS` `news_enabled` and the `news_items` DDL and index, and update TABLES. The owner's existing table
  and its 500 rows stay untouched; drop the table later only in an owner-approved migration.
- `tables.py`: drop the NEWS convention.
- `api_products.py`: remove the `news()` handler and its route, so `/api/news` answers 404 NOT_FOUND.
- `attention.js`: remove the INJECTION_FLAGGED news row and the "Daily News" wording in comments.
- `core.js`: drop the news keys of `T.PROVIDER_LABEL`, the `#news` container switch and the `T.notBuilt` "Daily News" branch.
- `index.html`:
  - drop the `#news` root, the `pages\news.js` tag, and the news wording in the header comment;
  - the meta description becomes "Ledger-backed paper-trading simulator: real delayed market data, simulated executions, fictional
    capital." (today it says "no simulation").
- `settings.js`:
  - Data Status becomes "Market data" (UI-20): status first, with no news section and no `PROVIDER_WHAT` entries for `yahoo_news`,
    `fed` or `newspaper`.
  - Link the participation rate and session overrides from it.
- `tests\terminal_fixture_server.py`: drop the `/api/news` route (line 165) and `pages/news.js` from `OPTIONAL_STATIC` (line 59).
- Delete `pages\news.js` and `X\fixtures\terminal\news\`.
- Delete `news.py` in P1a, with the C3 switches and the tests that import it: it imports `config.NEWS_*`, so it goes before those
  constants (P1b).

**3. Navigation (section 16; UI-06; P1b).** The sidebar order is:
1. Dashboard (F12)
2. Portfolio (F7)
3. TRADING group:
   - the venue rows with their NAV / avail / working / settling lines (US Exchange F1, Debt F2, Credit F3, Foreign Markets & FX F4,
     Private F5, each PB F6);
   - Watchlists (Shift+F9);
   - the group title links to `#/trading`.
4. Pending Trades (F8, badge = working + partial + held across the book)
5. Completed Trades (Shift+F8, badge = settling)
6. Accounts (Shift+F4; Treasury F10 shown inside)
7. Cash & Collateral (Shift+F5)
8. Derivatives (Shift+F6)
9. History (Shift+F10). It lights up on `#/history` and `#/h/...`; Settings stops lighting there, reversing SHELL-23.
10. Settings (Shift+F11)
11. A small "Book views" group: Risk (F11), Accounting (Shift+F7)

The sidebar header comment is updated. "+ Open prime broker account" leaves the sidebar; P6 puts it on the Accounts page header, and
`T.components.sidebar.openPbModal` stays exported.

**4. Dashboard (UI-05, PORTFOLIO-07, OMS-16; P1b).**
- `dashboard.py` serves `GET /api/dashboard?book=`. It calls the existing functions directly, not over HTTP: portfolio_view, risk,
  treasury_view, account rows, `attention.attention_summary`, plus the optional providers `performance.returns_summary` (C9e),
  `collateral.summary` (C16) and `blotters.counts` (C7). It returns:
  - `capital` {starting (book default 25,000,000.00), contributed}
  - `nav`
  - `pnl` TODAY / MTD / YTD / ITD, with return % or "unavailable" and the reason
  - the ITD explain (realised, unrealised, income, financing, fees, FX)
  - `exposure` (gross, net, long, short, % NAV, leverage, gross notional / NAV, TRS notional)
  - `cash` (settled, available, reserved, restricted, receivables, payables)
  - `margin` (required, posted, excess, open calls)
  - `collateral` (posted, received, encumbered)
  - `accounts` (by-account rows)
  - `counters`, `attention`, and `sources` (source, as-of and quality per section)
- `pages\dashboard.js` renders, refreshed every 15 s:
  - the "Requires your attention" STRIP tiles first, re-linked:

    | Tile | Opens |
    |---|---|
    | working orders | `#/pending` |
    | pending settlements | `#/completed?status=settling` |
    | failed settlements | `#/completed?status=failed` |
    | collateral calls and recalls | `#/cash` |
    | compliance | `#/risk?section=compliance` |
    | integrity | `#/accounting/integrity` |
    | provider pauses | `#/settings/market-data` |
    | open items | `T.attention.expand()` |

  - then the Fund tiles (reuse `T.pages.portfolio` navTile / pnlTile), Exposure, Cash & margin, Accounts table and Order & settlement
    counters.
- The global attention strip and the per-account sidebar badges stay as they are.
- `sources` records each optional provider's time in ms. The Dashboard's own work (without the optional providers) stays within 300 ms
  of server time (P1b); P8 measures the whole answer, providers included, on the integrated build.
- Its optional sections ask their provider only when the module is bound (server side) and show "unavailable" with the reason
  otherwise.

**5. Keys (UI-13).** Registry per C1 (P1a); the UI below and the KEYBINDS text are P1b's. Settings > Keyboard and the "?" help follow
the registry. Return the new KEYBINDS.md default-map tables as text for the orchestrator.

**6. Duplicate-instance message (STARTUP-03, `server.py`; P1a).**
- Home lock held:

  > FinSim is already running: finsim-terminal \<version\>, PID \<pid\>, since \<ET time\>, http://127.0.0.1:\<port\>/ (code
  > \<tree\>). Open it with `python -m finsim open` or the FinSim desktop icon. To run this copy instead: `python -m finsim stop`, then
  > `python -m finsim serve`.

  Exit code 3. The lock JSON gains `code` (the code tree); older locks without it just omit that clause.
- Port taken:

  > port \<p\> is used by another program (no FinSim lock in \<home\>); run `python -m finsim status` or pass --port

  No PID of the refused process.

**7. The 1.0.3.1 schema fixture (`schema_1031.sql`; P1a).**
- The CREATE statements from `sqlite_master` of a fresh read-only copy of the owner's database (last opened by 1.0.3.1), plus INSERTs of
  `meta` (schema_version 1, app_version_last_opened 1.0.3.1), the 52 chart rows and the three books. No owner rows (no orders, fills,
  journal, positions, transfers or owner settings).
- It is SQL text, because `finsim\.gitignore` ignores `*.db`: tests build the database at test time with `executescript`.

**8. Tests (UI-17, TEST-07; split as above).**
- Remove the NewsStore and news-page tests.
- `test_terminal_attention.py`:
  - the two attention tests, verbatim, now against `attention.py`;
  - provider pauses exclude the retired providers;
  - `/api/attention/summary` filters by book.
- `test_terminal_reconcile.py`: D_MAP D12 points to `test_terminal_attention.test_attention_shows_integrity_failure`.
- `test_terminal_api.py`:
  - remove `test_news_route_with_a_stub_store`;
  - the keybind sample action becomes `page.dashboard`;
  - add: `/api/news` is 404; health has no `news` key and no `yahoo_news` / `fed` / `newspaper` provider; PATCH `news_enabled` is
    400; the C5 hook records a refused bond ticket and leaves the HTTP answer unchanged.
- `test_terminal_static.py`:
  - REQUIRED_IDS without `news`;
  - default and unknown routes lead to the Dashboard;
  - `#/news` redirects;
  - 74 actions and the new owner map;
  - every global action has a `core.js` handler;
  - sidebar order and keys;
  - the PLANNED_STATIC files are listed in `index.html` and in the fixture server, and the fixture server has no news route.
- `test_terminal_core.py`: TABLES; and the chart sync: a database built from `schema_1031.sql` and opened by the repo's `Database` has
  every `ledger.CHART` code in `chart`, and with foreign keys on a journal line on each code is accepted (a code missing from `chart`
  would raise "FOREIGN KEY constraint failed").
- `test_terminal_server.py`: the new duplicate and port messages.
- `test_terminal_dashboard.py`: the shape of `/api/dashboard` on a seeded runtime, "unavailable" fallbacks while the optional
  providers are missing, and a timing budget.
- Regenerate the api fixtures without news.

#### Acceptance
1. **Suites.** The suite is green on py-3.12 and py-3.14; report the counts. The new tests above pass.
2. **Owner-DB copy, served `--offline --port 0`.**
   - On start the chart holds every `ledger.CHART` code (52 rows before, all codes after); integrity full is ok.
   - `/api/attention/summary?book=F` equals the golden pre-change attention block (open items, severities, risk warnings, compliance,
     collateral calls today, recalls, working orders, pending settlements, integrity, hold). Only provider pauses differ: they no longer
     count the news providers.
   - `/api/dashboard?book=F` does its own work in 300 ms or less of server time (each optional provider's time reported in `sources`),
     with every section present or "unavailable" with a reason.
   - The worker registers no `news_*` slot.
   - `/api/portfolio` NAV is identical before and after P1's code on the same copy and marks: no money path changed.
3. **Browser walk** (Playwright py-3.14, 1440x900 and 1100 px, on the copy):
   - `/` lands on `#/dashboard`.
   - `#/nope` toasts "no page #/nope - showing the Dashboard".
   - `#/news` redirects with the one-time toast "That page was removed - showing the Dashboard".
   - The sidebar order and keys are exactly as in step 3.
   - Each new route renders: P1's own pages fully; other packages' pages as their stub or notBuilt.
   - F12 opens the Dashboard; F8 on `#/a/F1:US_EXCHANGE/markets` opens `#/pending?account=F1:US_EXCHANGE`; Shift+F10 opens the
     History hub.
   - Settings > Market data shows no news.
   - A DOM text search on every page (P1b: P1's pages, the toast included; P8: every page) finds none of the news feature's own
     strings: "Daily News", "Newspaper", "briefing", "headlines", "news feed", "/api/news". Allowlist (not news): the private-company
     master's source URLs (for example `anthropic.com/news/...` in the private round sources) and Risk's "not in the headline gross"
     wording.
   - The page's meta description reads "simulated executions".
   - 0 console errors and 0 HTTP >= 400.
   - Screenshots go to `scratchpad\sweep\finsim1-p1\`.

### P2 - Orders, the two trade blotters and as-of funding

**Owns**
- Python: `T\blotters.py` (new), `T\orders.py`, `T\orders_preview.py`, `T\orders_rules.py`, `T\matcher.py`, `T\execution.py`,
  `T\sessions.py`
- Static: `T\static\pages\orders.js`, `T\static\pages\pending.js` (new), `T\static\pages\completed.js` (new),
  `T\static\components\ticket.js`, `T\static\components\preview.js`, `T\static\components\grid.js` (backward-compatible additions
  only: P3's pages use it)
- Tests:
  - new: `X\test_terminal_blotters.py`, `X\test_terminal_order_amend.py`, `X\test_terminal_asof_listed.py`
  - existing: `X\test_terminal_orders.py`, `X\test_terminal_funds_queue.py`, `X\test_terminal_sessions.py`,
    `X\test_terminal_equities.py`, `X\test_terminal_fill_live_bars.py`, `X\test_terminal_hedges.py`, `X\test_terminal_options.py`,
    `X\test_terminal_strategies.py`

**Depends on:** P1a. **Sections:** 3 (3A, 3B, lifecycle), 4 (position-trade links), 5 / 17 (listed as-of and its funding), 11 (legs
grouped as a strategy), 12 (collateral netting in funding), 15, 16 (Pending / Completed Trades), 18 #1-7.

**Gaps:** OMS-01, OMS-02, OMS-03, OMS-04 (listed as-of and source labels), OMS-05, OMS-06, OMS-07, OMS-08, OMS-11 (GTD, next session,
extended-hours disclosure), OMS-12 (parent ids; resting bond / FX orders deferred), OMS-13, OMS-14, OMS-15, OMS-17, UI-07, UI-08,
TEST-02.

#### Task

**1. Hooks first:** C7 (routes, `counts`), C8 (commands, columns), C20 (as-of funding) and the funding side of C21
(`own_funds_c` / `fund` with `csa_netting`).

**2. Pending Trades (OMS-01, OMS-05, OMS-06).**

Rows: per the C7 status filter, default `open` = orders WORKING, PARTIAL, SUSPENDED and WAITING_PARENT, plus open RFQs (C14). The
filter also offers Filled, Cancelled, Expired, Rejected and All, so every order's history (directive: "View order history"; the
Canceled and Expired paths) stays reachable.

Every row carries:
- order id, instrument, symbol, asset class (Equity / ETF / Option / Future / Bond / FX / OTC);
- side words (BUY, SELL, SELL SHORT, BUY TO COVER, BTO, STO, BTC, STC, and for OTC PAY / RECEIVE FIXED, BUY / SELL PROTECTION, PAY /
  RECEIVE TOTAL RETURN);
- type, limit / stop / trail, original / filled / open quantity (open only for live statuses), avg fill;
- the current market price from the quote cache (`api.quote_dict`, never fetched): `{last, bid, ask, quality, age_s, source}`, and
  the distance to the limit / stop in %;
- submitted time;
- expiration words ("DAY, expires 10-22 16:00 ET", "GTC to 2027-01-25", "GTD 11-20");
- account name and book;
- counterparty: PB account -> its broker; listed -> "exchange (simulated), OCC / CME / NSCC clearing"; OTC -> the dealer;
- the reservation per order (C9a);
- the reason (for cancelled / expired / held / rejected rows);
- stage and stage label.

Stages are derived:

| Stage | When |
|---|---|
| CREATED | the stored preview (its `as_of`), shown as the first timeline step |
| SUBMITTED | the first order event; queued for the next session, or awaiting a fresh quote |
| QUEUED | `activate_ts` in the future: "queued for \<open\>" |
| WORKING | |
| PARTIALLY FILLED | |
| HELD | "waiting for funds" (FUNDS_WAIT) or "fill refused: \<reason\>" |
| WAITING PARENT | |
| AWAITING DEALER QUOTE | RFQ REQUESTED |
| AWAITING YOUR ACCEPTANCE | RFQ QUOTED, until the quote expires |
| FILLED / CANCELLED / EXPIRED | final states, under their filters ("x of y filled" when partly filled) |
| REJECTED | `order_rejections`, under the Rejected filter |

Packages, filters and actions:
- Package legs are grouped by `package_id` / `strategy_key`, each leg kept as its own row.
- The package state is derived from its legs: PK-000001 is never listed as open, and the owner's data is not written.
- Filters: status, account, asset class, instrument, book / all books.
- A "New order" button opens the docked ticket for the chosen account (the filtered account, or a venue picker on the book view): the
  directive's Submit action.
- Row actions: cancel, resume, modify, replace, review / history.
- The page polls every 5 s while anything is working.

**3. Completed Trades (OMS-02, OMS-07, OMS-12, OMS-13).**

Source rows: the C7 union, one row per execution, never merged - fills, FX deals, OTC contract events and the financing trades.
Borrow rows name the lender through `lending.lender_label` (C16: "street lender (assumed terms)").

Columns:
- execution id;
- parent: the order id, or the ticket id `T-<command id>` for immediate tickets and financing trades;
- instrument, asset class (incl. Financing), side words, quantity or notional, price / rate / spread, execution time, trade date;
- settle date (expected / actual), account / book, counterparty;
- fees: the fill's fee lines and its 5000 journal lines;
- status;
- source and batch.

| Status | When |
|---|---|
| Executed | |
| Settling | |
| Settled | |
| Failed / Resolved | C15 |
| daily VM | futures |
| proceeds applied to the borrow collateral | post-hotfix short sales |
| no cash leg | expiry at 0 |
| IM posted, CTM daily VM | OTC |
| open / rolled / closed / repaid / returned | financing trades |

Sources:
- "Simulated fill" (live matcher fills);
- the D1 as-of labels ("As-of booking - simulated fill at the 2026-10-22 closing print ..." / "As-of booking - simulated OTC trade
  with Goldman Sachs ..."), with a `.badge-asof`, the entered time and the batch;
- "System (exercise / assignment / expiry / final settlement / maturity)";
- "Historical model (not an independent execution)" only for booking `HISTORICAL_MODEL`.

Filters: date range, instrument, account, asset class, counterparty, status, source / batch. The source / batch filter lists import
batches only when `T.hasModule("importer")` (C2, C19). The server CSV (`format=csv`) carries every column; paging uses `limit` /
`offset` and "load more".

**4. Execution detail (OMS-14).** `GET /api/executions/{id}` returns:
- the execution;
- the parent order and its events;
- journal entries by reference: fill, settlement, `collat:`, `im:`, `otc:`;
- linked transfers (`transfers.linked_id`) and settlement rows with their state;
- lots opened (`lots.open_fill_id`) and relieved (`lot_relief`), with realised P&L;
- the current position, with a link to its drawer;
- the audit trail: the `commands` row (request_id, actor, started_ts, app_version, payload sha256) and booking / entered_ts / batch.

The drawer tabs are Trade, Cash movements, Accounting (reusing `T.pages.accounting.entryModal`), Position and Audit. On a row with a
settlement, the drawer offers the C15 actions:
- "Simulate counterparty fail" (`settlement.fail`): owner-triggered, off by default, labelled "simulated counterparty fail" (Q11);
- "Resolve" (`settlement.resolve`: retry / cash compensation / buy-in) on a FAILED row.
- Both appear only when `T.hasModule("collateral")` (C2).

**5. Amend and replace (OMS-03).**
- `order.amend` works on WORKING / PARTIAL / SUSPENDED orders:
  - new limit / stop / trail, qty >= filled, TIF / expiry;
  - re-validated on the remaining quantity through `orders_preview.validate` (funding / reservation, tick, STOP_SIDE);
  - resets `first_eval_done` and `limit_rested` (EXEC-5);
  - event "AMENDED limit 1.00 -> 1.05"; `amend_count` + 1.
- `order.replace`, in ONE transaction:
  - the old order becomes CANCELLED with reason "replaced by O-x" and `replaced_by_order_id`;
  - the new order gets `replaces_order_id`;
  - it moves `borrow_id` and never calls `lending.release_for_cancel`;
  - it re-parents WAITING_PARENT hedge children.
- Both run behind a preview. The routes are per C7. `ticket.js` gets an amend mode (prefilled; Ctrl+Enter submits).

**6. Review (OMS-08).** `?review=1` runs a pure `orders_rules.evaluate()` on the cached quote and the session now. It returns "would
fill N at P now" or the reason (STOP_NOT_REACHED, NOT_MARKETABLE, AWAITING_SESSION, STALE, BAR_CAP_USED) and the distance to the
trigger. It issues no command and writes nothing. The order drawer (Enter / double-click in Pending, Orders and Completed) shows:
- the timeline: Created (preview) -> Submitted -> Working -> each partial fill -> the final state, from `order_events`;
- fills with the quote used, the cap note and the settlement state;
- journal entries;
- linked sweeps (`orders._sweeps_for`);
- the borrow and package legs;
- a summary of the stored preview.

**7. TIF (OMS-11).**
- GTD: tif stays GTC; `gtd_date` is set; `expires_ts` = session close of `gtd_date`. ExpireOrders expires it with "GTD date reached".
- Next-session instruction: `activate_ts` = the next session open. The matcher skips the order until then; the blotter shows "queued
  for \<open\>".
- The ticket TIF select offers DAY / GTC (90 days) / GTD (date picker) / "Next session".
- The ticket shows one line: "Extended hours: not simulated - rests until the regular open" (C6).
- `orders.gtd_supported(services)` returns True (the C6 probe, C7).

**8. Rejections from every ticket (OMS-15).** Register through C5 a recorder (`orders.record_rejection`, with `ticket_kind` in
`payload_json`) for the commands of the bond ticket, FX spot / forward, OTC book / terminate, the RFQ commands `otc.rfq.request` /
`otc.rfq.accept` / `otc.rfq.decline` (C14) and private trade. They appear under the Rejected filter.

**9. As-of listed (OMS-04).** Build C8 exactly as specified: the payloads, the package command, `fee_rule` values, the option instrument
created inside the command, and booking `HISTORICAL_MODEL` when `as_of.historical_model` is true. `fills.origin` stays ORDER.

**10. As-of funding and CSA netting in `execution.py` (C20, C21 funding side).**
- `fund` / `own_funds_c` read `ctx.asof_date`; with it set, own cash and Treasury availability are business-date figures at the close of
  that date (C9h), and the sweep is dated that date.
- `own_funds_c(immediate=True, csa_netting=...)` subtracts `collateral.restricted_c(...)` when the collateral module is bound; the default
  (`csa_netting=False`) excludes all restricted cash, `True` (CSA payments, passed by P4) excludes only securities-lending collateral.
  Under `ctx.asof_date` = D it passes `as_of=D` (C21).
- Under `ctx.asof_date`, `fund` sizes the Treasury sweep on the business-date figure (P6's `sweep_in` checks the same figure, C15) and
  refuses 409 ASOF_CURRENT_FUNDS, writing nothing, when the sweep would leave Treasury's current settled cash below zero (C20, L18).

**11. Account Orders page.** `orders.js` keeps its sections - working, the order history (all statuses with their reasons), rejections
and fills - now reading the blotter APIs filtered to the account (`account=`), with "load more" paging. Today F1:US_EXCHANGE shows only
200 of its 315 fills. A "New order" button opens the account's ticket.

**12. Tests (TEST-02, OMS-17).** In `test_terminal_blotters.py`, `test_terminal_order_amend.py` and `test_terminal_asof_listed.py`;
see the acceptance below.

#### Acceptance
1. **Hand-worked money tests** (temp home, FakeClock, FakeProvider):
   - (a) A resting LIMIT BUY 100 @ 50.00 changes no position and no NAV. Reserved = 100 x 50.00 + the schedule fee.
   - (b) Amend 50.00 -> 51.00 re-reserves 5,100.00 + fee.
   - (c) Replace of a working SELL_SHORT keeps its `borrow_id` and links both orders.
   - (d) An order of 1,000 filled 400 @ 10.00 then 600 @ 10.10 gives two executions under one parent, avg 10.06, fees per fill.
   - (e) GTD expiry at the `gtd_date` close.
   - (f) A next-session order does not fill before `activate_ts`.
   - (g) As-of BTO 387 ORCL261120C00140000 @ 3.55, trade date 2026-10-22, exec 16:00 ET:
     - the option instrument is created inside the command (from the payload's `instrument_spec`);
     - premium 137,385.00 (387 x 3.55 x 100) and fee 251.55 (387 x 0.65);
     - Dr 1700 / Dr 5000 / Cr 2100;
     - settlement due 2026-10-23;
     - `fills.ts` = `lots.open_ts` = 2026-10-22 16:00 ET; journal business_date 2026-10-22; `entered_ts` = now.
     - Replaying the same request posts nothing; a different payload with the same key is 409.
   - (h) The QQQ package as ONE `trade.book_as_of_package` command (BTO 150 700P @ 17.64 = 264,600.00, STO 150 675P @ 10.25 =
     153,750.00, fees 97.50 each): one `hedge_packages` row BEAR_PUT_SPREAD, each leg its own order and fill with its own import_key,
     reserved option margin 0.
   - (i) As-of funding (C20), three cases on a PB account with no cash of its own and PB financing off:
     - the business-date figure binds: Treasury holds 1,000,000.00 at the close of D and a deposit dated D+1 of 4,000,000.00 makes it
       5,000,000.00 now. An as-of need of 1,500,000.00 with `ctx.asof_date` = D is refused 409 INSUFFICIENT_FUNDS (Treasury available at
       the close of D 1,000,000.00); the same need without `asof_date` sweeps 1,500,000.00 from the current balance.
     - the sweep is dated D: Treasury holds 25,000,000.00 at the close of D and a transfer dated D+1 of 23,000,000.00 leaves 2,000,000.00
       now. An as-of need of 1,701,918.50 sweeps 1,701,918.50 with business_date D (P6's `sweep_in` checks the business-date figure, not
       `sweepable_c`); Treasury then closes D at 23,298,081.50 and D+1 at 298,081.50, and holds 298,081.50 now.
     - the current balance binds: with a transfer dated D+1 of 23,532,419.20 instead (current Treasury 1,467,580.80), the same as-of need
       is refused 409 ASOF_CURRENT_FUNDS before anything is written (it would leave current Treasury at -234,337.70, which L18 forbids);
       an as-of need of 1,000,000.00 sweeps 1,000,000.00 dated D and leaves 467,580.80.
   - (j) CSA netting (C21): a PB account with settled 300,000.00, GL 2330 200,000.00 and GL 1320 50,000.00 has restricted 150,000.00. A
     VM payment of 250,000.00 to its CSA counterparty (`csa_netting=True`) is paid from own cash (300,000.00), no sweep. A non-CSA
     immediate need of 250,000.00 sees own 150,000.00 and sweeps 100,000.00 from Treasury.
   - (k) A `HISTORICAL_MODEL` as-of fill shows the source "Historical model (not an independent execution)" and never the as-of label.
2. **API tests.** Every 3A / 3B field is present; the union covers fills, FX deals, OTC contracts and each financing source; status
   filters (open / filled / cancelled / expired / rejected / all), paging and stages work; the CSV equals the rows; rejections are
   recorded from all five tickets.
3. **Owner-DB copy.**
   - Completed Trades lists F1's 320 executions: the 315 fills (all origin ORDER) and 5 Financing rows, the stock borrows B-000001..5
     opened (parent `T-<command id>`, lender "street lender (assumed terms)"); GXO O-000014 shows 225 executions under one parent. (Counts
     of the 14:54 copy; a later copy adds its own new rows: the check is fills + financing rows = CSV rows.)
   - Pending (open) shows 0 rows (PK-000001 not listed as open).
   - The Cancelled filter lists O-000008 (reason "PG 6,400 BUY -> replaced by 4,790") and O-000018 (reason "STT calls (owner: not
     intended)"); each drawer shows the timeline Created -> Submitted -> Cancelled with the 2026-10-21 times.
   - The CSV has 320 data rows plus a header; with the asset-class filter excluding Financing it has 315.
4. **Browser walk** (P1 standards):
   - On a seeded home: place a resting LIMIT with "New order"; then modify, replace, cancel and review it from Pending; find it again
     under the Cancelled filter.
   - In Completed: filter, open the drawer tabs, simulate a counterparty fail on a pending settlement and resolve it (retry).
   - The account Orders tab shows its working, history, rejections and fills sections.
   - The ticket shows the GTD picker.
   - 0 console errors, 0 HTTP >= 400.

### P3 - Portfolio, NAV, history and performance

**Owns**
- Python:
  - `T\accounting.py`, `T\accounting_checks.py`, `T\api_views.py`, `T\performance.py`, `T\risk.py`, `T\marks.py`,
    `T\lifecycle_eod.py`, `T\instruments.py`, `T\products\dividends.py`
  - `T\navhistory.py` (new)
- Static: `T\static\pages\portfolio.js`, `positions.js`, `accounting.js`, `history.js`, `activity.js`, `transactions.js`, `risk.js`;
  `T\static\components\performance.js`
- Tests:
  - existing: `X\test_terminal_accounting.py`, `X\test_terminal_performance.py`, `X\test_terminal_display_units.py`,
    `X\test_terminal_marks.py`, `X\test_terminal_risk.py`, `X\test_terminal_dividends.py`, `X\test_terminal_lifetimes.py`,
    `X\test_terminal_instruments_foreign.py`, `X\test_terminal_private.py`, `X\test_terminal_private_credit.py`,
    `X\test_terminal_private_listed.py`, `X\test_terminal_units_consumers.py`
  - new: `X\test_terminal_asof_restate.py`, `X\test_terminal_closed_positions.py`, `X\test_terminal_portfolio_contract.py`

**Depends on:** P1a. **Sections:** 4, 6 (sector exposure, account allocation), 12 (the cash tab reused), 13, 14 (margin calls on
Activity), 16 (Portfolio, History), 18 #12, #22, #23.

**Gaps:** PORTFOLIO-01 (as-of NAV engine, versions, readers, late catch-up snapshots), PORTFOLIO-02 / PORTFOLIO-06 (consumer side of
C13), PORTFOLIO-03, PORTFOLIO-04, PORTFOLIO-05, PORTFOLIO-07 (C9e), PORTFOLIO-08 (OTC cost basis, explain rows), PORTFOLIO-13,
PORTFOLIO-14, PORTFOLIO-15, PORTFOLIO-17, PORTFOLIO-19, PORTFOLIO-20, UI-12 (the History hub page). (UI-02 and UI-04 moved to P1: P1a
deletes `news.py` and makes the `api_views.py` import switch, C3.)

#### Task

**1. First step.**
- `api_views.py` already imports from `.attention` and `news.py` is gone (P1a, C3); check with grep that nothing imports `news`.
- Update the news wording in `activity.js`'s header comment.
- Build C9's tier-1 hooks ((a)-(c), (f)-(h)) first, then (d), (e) and (i) (rule 2).

**2. As-of NAV (PORTFOLIO-01).**
- `nav_as_of(scope, d)` and `navhistory.py` per C9d:
  - positions and cash from journal entries with business_date <= d (the entry's business date, never `fills.ts`);
  - `nav_snapshot_versions` (account_id, snap_date, version, ts, reason, batch_id, the same figures as `nav_snapshots`, the split
    columns, detail_json), written by `nav.restate`;
  - the `pnl_day` / `flows_day` chain is recomputed across the restated range;
  - the split per date: original NAV, import effect per group, interest effect, mark-policy effect per preserved position, and for a
    snapshot flagged late a dating line (entries the original counted "from the ledger as it stood" that belong to another business
    date); residual 0, else the command refuses;
  - per account and `{lifetime}:ALL` (C9d): the `funding` group carries as-of sweeps and transfers between accounts (it nets to 0 only at
    the lifetime level); a version row with `original_nav_c` 0 where an account has no original row; idempotent per (batch, date).
- Readers prefer the latest version and label it "restated on \<date\> (batch X)", with a toggle to the original: `_last_snapshot`,
  Performance, History, the TODAY / MTD / YTD baselines, and the integrity checks M4 / M5 (C9i).
- The Snapshot job computes a LATE (catch-up) snapshot with `nav_as_of`, never "from the ledger as it stands". The 10-22 row taken on
  10-23 08:04 is the motivating case.
- `cash_view_at` (C9h).

**3. Dated marks (PORTFOLIO-14).**
- Snapshot rows use that date's CLOSE for equities / ETFs, SETTLE for futures, the OTC VM mark of that date, and for options the
  recorded CLOSE (Cboe official close where recorded, else the Yahoo last trade, stale when that day's volume was 0).
- Weekends and holidays carry the previous trading day's stored marks; no re-mark on non-trading days.
- `detail_json` records each position's mark kind, source and as-of.

**4. Closed positions (PORTFOLIO-03).**
- `Accounting.closed_positions(scope, from, to)` builds from lots with `qty_remaining` 0, `lot_relief` and fills; plus OTC contracts
  TERMINATED / MATURED, expired / exercised options and closed borrows.
- Per row: open / close dates, qty, average open / close price, realised, fees, income, financing, days held, closing fill ids.
- `GET /api/portfolio/closed` (with CSV); a "Closed" tab on Portfolio and in the History hub.
- `position_detail` also returns closed lots and their relief rows.

**5. Position rows (PORTFOLIO-04, PORTFOLIO-06).**
- New fields:
  - `total_pnl_c` = unrealised + realised + income + financing + fees to date;
  - a finer asset class (Equity, ETF, Bond, Option, Future, FX, OTC TRS / IRS / CDX / option / forward);
  - counterparty (borrow lender through `lending.lender_label`, C16; PB / dealer for OTC; "exchange-cleared (OCC / CME)" for listed);
  - `margin_c` and `collateral_c` from C16, or "n/a" until present.
- Derivative rows render the C13 fields (units / notional with a unit label, entry price / spread / rate, underlying price / current
  spread / par, MV = contract value).
- Subtotals by asset class; the Positions CSV carries the new columns.
- Fees to date are shown on the Portfolio.

**6. Drill-down (PORTFOLIO-05).**
- The drawer opens from a Portfolio row (click / Enter) and on a single click in account Positions, docked beside or above the grid.
- `position_detail`:
  - pages its entries ("all" by default for the fills that created the position);
  - adds `net_cash` per entry;
  - links each fill's order and shows its settlement state (and C15 `fail_state`);
  - groups borrow, collateral and accrual entries by type.
- `dividends.position_notes` shows only ex-dates inside the holding period, else "not held on the ex-date" (the STT note).

**7. Returns (PORTFOLIO-07).** C9e:
- TODAY = (NAV now - baseline NAV - flows today) / baseline;
- the cumulative time-weighted return chains trading-day returns from the (restated) snapshots;
- simple ITD = (NAV - contributed capital) / contributed capital.
- Return tiles go on the Portfolio as well.

**8. OTC cost basis and explain rows (PORTFOLIO-08, consumer side).**
- C9c cost basis from GL 1335 / 2345 (`ledger.OTC_COST_GLS`), kept out of the NAV asset / liability sets, so MV replaces cost there and
  A - L = NAV holds.
- The P&L explain shows 4451 / 4453 under financing and 4452 under income, with correct signs; the residual stays 0.

**9. NAV history and portfolio as of a date (PORTFOLIO-13).**
- `GET /api/history/nav?scope=&from=&to=` returns the snapshot rows (latest version), daily and cumulative returns, quality, the late /
  restated flags and, for a restated date, its split.
- `GET /api/portfolio?as_of=YYYY-MM-DD` serves positions and marks from that day's snapshot detail, read-only.
- History hub (UI-12) at `#/history`, tabs:

  | Tab | Content |
  |---|---|
  | NAV history | table and chart, restated / original toggle; a restated date opens its split (original NAV, import effect by line, interest, mark policy by position) |
  | Transactions | reuses the accounting transactions view |
  | Account activity | reuses `activity.js`; `#/activity` keeps working |
  | Performance | |
  | Closed positions | |
  | Imports | mounts C19 when present |
  | Past lifetimes | the existing `#/h` pages |

**10. Performance statistics (PORTFOLIO-15).** Returns on trading days only, folding weekend and holiday accruals into the next trading
day. Annualised ratios show only after 20 trading days, otherwise "insufficient history". Cumulative TWR and the dated daily series are
always shown.

**11. Exposure definitions (PORTFOLIO-17).** The Portfolio footer adds OTC exposure beside futures notional (TRS units x S; CDX notional
with its credit sign; IRS shown by DV01, not notional), labelled "market value" vs "exposure", with the same definitions Risk uses.

**12. Sector (PORTFOLIO-19).** A `by_sector` block (MV long / short / net, % NAV, "unclassified") in portfolio_view and Risk, and a
group-by-sector toggle. The light command `instrument.classify {instrument_id, sector}` (source "owner", SETTINGS_CHANGED) is offered
from the instrument row; VMRK shows "unclassified" until set. Account allocation is the existing by_account weights.

**13. Margin views of P6's data (C16).**
- Activity gets a "margin calls" type from `collateral.margin_calls_rows` (issued, counterparty, amount, due, MET / OPEN / UNMET, the
  funding transfers).
- Risk gets a Reg T panel per account from `collateral.summary` / `position_requirements` (requirement, eligible collateral, excess or
  deficit, the assumed terms).
- Both show "unavailable" until the collateral module is bound, and call no collateral route before (`T.hasModule("collateral")`, C2).

**14. Portfolio contract (PORTFOLIO-20).** `test_terminal_portfolio_contract.py` asserts that a real `build_runtime` portfolio_view
answer has the shape `portfolio.js` reads: `by_class` list rows, footer keys, position row keys.

#### Acceptance
1. **Hand-worked money tests:**
   - (a) Restatement with its split. Day-1 NAV 1,000,000.00; on day 4 an as-of BUY 100 X @ 10.00 is booked for day 1, fee 1.00; closes D1
     10.50, D2 11.00, D3 Saturday. A preserved position of 200 Y, whose original D2 snapshot mark was 20.00, has a D2 CLOSE of 20.02.
     - Import effect: D1 +49.00, D2 +99.00, D3 +99.00. Mark-policy effect: D2 +4.00 (200 x 0.02), D3 +4.00 (Friday's marks carried).
     - Restated D2 = original D2 + 99.00 + 4.00; residual 0 on every date; `pnl_day` D3 0.00.
     - The originals are unchanged in `nav_snapshots`, and the TODAY baseline on D4 = the restated D3.
     - Per account: the buy is booked in account A and funded by an as-of sweep from Treasury inside its own command (the line's group);
       on D2 a separate as-of sweep returns 500.00 of A's cash to Treasury (the `funding` group: A -500.00, Treasury +500.00, the
       lifetime 0). On every date the rows of A, Treasury and the lifetime each close with residual 0. An account B opened after D1's
       snapshot that holds a restated D1 balance gets a D1 version row with original 0. Running the same restatement again writes no
       new version.
   - (b) Closed position: BUY 10 SPY @ 769.47 then SELL 10 @ 769.67 gives realised 2.00 and the fees shown separately.
   - (c) Total P&L = unrealised + realised + income + financing + fees, on a position with all five.
   - (d) OTC cost basis: CDX upfront received 48,492.57 with mark -49,176.13 gives unrealised -683.56, realised 0, NAV change -683.56.
     Built with P4's real functions; it runs after P4 task 7 lands (section 5).
   - (e) Performance with 5 snapshots including a weekend: 3 trading-day returns; annualised = "insufficient history".
   - (f) `by_sector` sums to gross.
   - (g) The seam (C9i): after restating D1..D3, the D4 snapshot's `pnl_day` = NAV D4 - restated NAV D3 - flows; the M4 check on the
     latest chain raises nothing (on the original chain alone it would raise one WARN at D3 -> D4).
   - (h) Business-date positions: a fill at 19:58 ET on D (business date D+1) counts in `nav_as_of(D+1)` and not in `nav_as_of(D)`.
2. **Owner-DB copy.**
   - Portfolio NAV ties A - L with rounding 0.00.
   - The History NAV series shows 10-21..10-26, equal to `nav_snapshots`.
   - A restatement of 10-21..10-26 with no import (dry run on the copy): import effect 0, interest 0, mark-policy effect 10-23 +3,212.20,
     10-24 +4,855.95, 10-25 +4,855.95, 10-26 -1,487.25 (dated closes / settles minus the 17:45 snapshot marks, computed 13:30 EDT on the
     13:22 copy; e.g. 10-23 GCZ26 settle 4012.1 vs 4010 = +1,050.00). The 10-22 row (snapshot taken late) shows its mark-policy effect
     (+8,042.63 on positions) and its dating line. Residual 0 on every date.
   - The STT drawer lists all 34 fills and all its entries, paged, with `net_cash` filled.
   - The VMRK sector reads "unclassified".
   - Risk and Portfolio exposure definitions agree.
3. **Browser walk:** Portfolio (Positions / Closed / Performance; the row drawer), account Positions (single-click drawer), the History
   hub tabs (a restated date's split), the Accounting cash tab, Risk (the Reg T panel), Activity (the margin calls type); 0 console
   errors, 0 HTTP >= 400.

### P4 - Derivatives and the OTC lifecycle

**Owns**
- Python: `T\products\swaps.py`, `T\products\swaps_trs.py`, `T\products\swaps_cds.py`, `T\products\otc_options.py`, `T\ledger.py`,
  `T\quotes\rates.py`
- Static: `T\static\pages\derivatives.js` (new), `T\static\pages\markets_otc.js`, `T\static\components\swapticket.js`,
  `T\static\components\cdsticket.js`, `T\static\components\otcticket.js`
- Tests:
  - existing: `X\test_terminal_swaps.py`, `X\test_terminal_swaps_trs.py`, `X\test_terminal_cds.py`, `X\test_terminal_integration.py`,
    `X\test_terminal_ledger.py`
  - new: `X\test_terminal_otc_asof.py`, `X\test_terminal_rfq.py`

**Depends on:** P1a. **Sections:** 3 (OTC negotiation and confirmation states), 5 / 17 (OTC as-of), 7, 9, 10, 12 / 13 (financing and
collateral are not P&L), 15 (indicative vs dealer-confirmed), 16 (Derivatives), 18 #16-18.

**Gaps:** OMS-04 (OTC as-of), OMS-10, PORTFOLIO-01 (dated OTC inputs and historical VM), PORTFOLIO-02, PORTFOLIO-06, PORTFOLIO-08,
UI-11.

#### Task

**1. Hooks first: C10-C14.** C11's business-date default, `ledger.ensure_chart_rows` (called from `swaps.bind`) and C12 land in the
first 30 minutes, with `ledger.OTC_COST_GLS`; P2, P3 and P6 depend on them. C10's columns and C14's table and command names are tier 1
too; the as-of booking, the RFQ flow and the credit event are tier 2 (rule 2).

**2. As-of OTC booking (OMS-04).**
- `otc.book_as_of` for TRS, CDX, IRS (and OTC options / forwards through the same delegate when the inputs exist) per C10.
- The owner's dealt terms are booked exactly: TRS `reset_px`, CDX `dealt_spread_bp`, IRS `fixed_rate` (D10). Before commit the stored
  contract is compared with the payload; any difference is 409 ASOF_TERMS_MISMATCH and the transaction rolls back. A CDX whose account
  basis would not deal the payload spread is 409 ASOF_BASIS_NOT_CALIBRATED.
- The TRS underlying's close of the trade date is a valuation input: when it differs from `reset_px`, day-0 VM = (reset_px - close) x
  units with the side's sign, posted on the value date.
- The confirmation status of an as-of contract reads "CONFIRMED - as-of booking at the owner's instruction (no electronic confirmation
  in the simulator)".

**3. Historical inputs (PORTFOLIO-01, OTC side).**
- C12, and SWAP_VM / `dfs_for` / `_model_mark` / the CDX valuation take the dated curve for past dates.
- The `as_of > d -> CAUGHT_UP` IRS rule changes to "price at d when the curve of d exists".
- For a contract with booking `AS_OF`, every lifecycle posting for a date before its entered day carries the business date real time
  would have given it (D7): VM and payments of D on D; the accrual of night D on D+1 (the swap accrual entries today take the posting
  day). TRS and IRS accrual nights start at the value date; a CDX keeps the state `swaps_cds.book` seeds, so its first new night is
  the trade date + 1 (C10).
- No reset, coupon or payment falls due before 2026-11-23 (TRS), 2026-12-21 (CDX) or 2027-04-21 (IRS).

**4. The IRS curve spread (owner Q1 variant A).** `calibrate_curve_spread_to_rate` per C10, shown in terms as "contract curve spread
(SOFR swap vs Treasury par curve) -18.0 bp, calibrated at booking". From the UI, `otc.book` keeps refusing OFF_MARKET.

**5. Intraday marks (PORTFOLIO-02).**
- C13 MV = the live model mark when fresh, labelled "model, intraday"; else the overlay labelled stale.
- The move since the last VM shows in day P&L and in the unrealised explain.
- The 17:45 snapshot is unchanged (VM has run by then).
- Risk reads the same figure through accounting.

**6. Display fields (PORTFOLIO-06).**
- TRS: reference units, reset price, underlying price.
- CDX: notional, entry / current spread, coupon, upfront.
- IRS: notional, fixed rate vs current par, DV01.
- These go in the C13 fields and in the drawer.

**7. GL reclassification (PORTFOLIO-08).**

| Posting | GL |
|---|---|
| TRS financing | 4451 |
| Dividend equivalents | 4452 |
| CDS premium and IRS net coupons | 4453 |
| CDX upfront and OTC option premium received | 2345, at booking (contract cost basis) |
| CDX upfront and OTC option premium paid | 1335, at booking (contract cost basis) |
| Return leg / STM / termination / maturity | 4450 (unchanged) |

- At termination, maturity or expiry the cost basis is released into 4450.
- 1335 / 2345 form `ledger.OTC_COST_GLS`: outside `COST_LONG_GLS` / `COST_SHORT_GLS` (L9 ties those to open lots) and outside the NAV
  asset / liability sets (C9c), so A - L = NAV holds with the contract at MV.
- NAV is unchanged by construction. The live DB has no swap entries, so nothing migrates.
- The new codes reach the owner's chart through P1's sync on open and `ensure_chart_rows` in `swaps.bind` (C11).
- Update the swap tests that pin 4450: about 88 asserts in `test_terminal_swaps` / `swaps_trs` / `cds`.

**8. RFQ and confirmation (OMS-10).**
- The PB OTC desks' preview becomes "Request quote" (REQUESTED, command `otc.rfq.request`); "Accept" and "Decline" are
  `otc.rfq.accept` / `otc.rfq.decline` (C14).
- The simulated dealer is the `RFQ_DEALER` lifecycle product job (C14), registered from `swaps.bind` with
  `lifecycle_eod.add_product_job`. On the next scheduler tick (about 30 s) it answers with the model quote labelled "indicative model
  quote, simulated dealer" (QUOTED, TTL 120 s), and it expires QUOTED RFQs past their TTL. It does not use the matcher.
- "Accept" books within the TTL; after it, the quote is EXPIRED and acceptance is refused; "Decline" is offered.
- New contracts start PENDING_CONFIRM. At 17:05 they become CONFIRMED, labelled "simulated confirmation".
- The execution detail shows the contract timeline: requested, quoted, accepted, booked, IM posted, each VM, resets, termination.

**9. CSA netting (C21).** The VM, swap-payment, CDX-coupon and termination payments call `execution.fund(..., csa_netting=True)`, so
they may use cash that is the same counterparty's collateral.

**10. Derivatives page (UI-11).** `#/derivatives`, with dealing staying on the PB OTC tab (F6) and the tickets:

| Tab | Content |
|---|---|
| OTC contracts | book-wide, with terminate and, on a CDX row, "Simulate credit event" (task 11); open RFQs on top |
| Listed options | positions with greeks from `/api/risk`; strategy grouping, e.g. the QQQ BEAR_PUT_SPREAD |
| Futures | IM, daily VM, open trade equity, the first-notice warning: GC / ZN Dec 26 are physically delivered, first notice about 2026-11-30, roll or close before about 11-27 |
| Servicing | next resets / payments / expiries / coupons from `next_events`, plus the option expiries 2026-11-20 |

**11. Simulated credit event (directive section 9).**
- `cdx.credit_event {contract_id, reference_entity, recovery_pct, event_date}`: owner-triggered from the OTC contracts tab, off by
  default, labelled "simulated credit event - recovery entered by the owner; no ISDA Determinations Committee ruling or auction in the
  simulator". It never fires by itself; one event per contract and reference entity (a repeat is 409 DUPLICATE_EVENT).
- The index has equal weights (IG 125 names, HY 100; assumed and shown). Defaulted notional Dn = original notional / names. The protection
  settlement Dn x (1 - recovery) is received by a protection buyer (paid by a seller), net of the accrued premium on Dn from the period's
  accrual start through the event date, settling T+1 business day (a settlement row, `ref_type` otc); the realised result posts to 4450.
- The contract's notional becomes N - Dn (terms keep the original notional and an index factor); the premium accrual and the IM follow the
  new notional from the next night (released IM returns to cash); the cost basis (1335 / 2345) is released pro rata (Dn / N) into 4450;
  the next SWAP_VM values the reduced contract.
- The event shows in the contract timeline and in Completed Trades (a "credit event" execution of the contract). P4 rewords its own
  Unsupported row (`swaps_cds.unsupported()`, today "Credit events in CDX names ... NOT_OFFERED") to: "CDX credit events: SIMULATED on
  request - the owner can trigger a constituent default at a recovery he enters; no ISDA Determinations Committee rulings or auctions
  are tracked". The row comes from the module, so it changes only when the feature ships.

#### Acceptance

**1. Hand-worked money tests from the owner's contracts** (`test_terminal_otc_asof.py`: temp home, FakeClock, recorded closes, dated
curves and proxies):

*TRS ORCL, short 38,700 @ 129.13 (the payload's reset_px), as of 2026-10-21:*
- notional 4,997,331.00; IM 499,733.10 (10 %);
- VM 10-21 0.00 (close 129.13 = reset); 10-22 +138,933.00 = (129.13 - 125.54) x 38,700; 10-23 -23,994.00; 10-26 +3,870.00; mark 10-23
  114,939.00;
- financing night 10-21: 4,997,331 x (3.88 - 0.60) % / 360 = 455.31, business date 10-22; night 10-22: 458.09 (at 3.90 %), business date
  10-23; total 913.40 to 4451.
- With a recorded 10-21 close of 129.12 instead, the contract still books reset 129.13 and day-0 VM is +387.00 received ((129.13 -
  129.12) x 38,700, the short gains).

*TRS META, 7,200 @ 697.52:*
- notional 5,022,144.00; IM 502,214.40;
- VM +350,424.00 / +44,856.00 / +86,832.00.

*IRS 7.5M, pay 4.720 %:*
- DV01 2,073.36; IM 103,668.00 (DV01 x 50 bp);
- curve spread about -18.0 bp; mark0 -2,073.36 = 1 bp x DV01;
- the VM by date equals the pure functions on the curves of 10-22 / 10-23 / 10-26 (reconciliation: -35,341.95 / +6,253.08 /
  +8,317.53).

*CDX IG 3M @ 64 bp (proxy 88 + basis -24.5 + half-spread 0.5):*
- upfront received 48,492.57 (to 2345); rebate 2,583.33 (31 days); initial payment 51,075.90 at T+1 (2026-10-22);
- IM 60,000.00; mark0 -49,176.13; cost of dealing -683.56;
- realised 0.

*CDX HY 4M @ 352 bp:*
- upfront 241,622.83; rebate 17,222.22; IM 240,000.00; cost of dealing -3,430.68.

*Refusals and bookkeeping:*
- A duplicate `import_key` is 409; actor `ui` is refused; a missing input is 409 ASOF_INPUT_MISSING.
- A CDX with dealt 64 bp on an account whose basis is still 0 (mid 88 bp) is 409 ASOF_BASIS_NOT_CALIBRATED; nothing is written.
- A payload term the booking would change is 409 ASOF_TERMS_MISMATCH; nothing is written.
- Every VM and payment entry carries the business date of its own date, every accrual night D the business date D+1.
- `CurveStore.for_date` sees a UST_PAR curve written to `curve_points` after `build_runtime` (C12).
- A CDX accrues its first new night on the trade date + 1 (10-22: IG 83.34, the rebate having covered 09-21..10-21); night 10-21 is never
  accrued (C10).

*Simulated credit event (task 11):* CDX HY 4,000,000 buy protection (coupon 500 bp, accrual start 2026-09-21, upfront received
241,622.83 in 2345, IM 240,000.00); one constituent defaults on 2026-11-02 at recovery 40 %:
- defaulted notional 40,000.00 (1 of 100 names); protection 24,000.00 received; accrued premium on 40,000.00 for 43 days (09-21..11-02)
  = 238.89 paid; net +23,761.11 settling 2026-11-03;
- notional 3,960,000.00; 2,416.23 of 2345 (1 %) released to 4450; IM 237,600.00 (2,400.00 back to cash); the next night's premium
  accrues on 3,960,000.00 (550.00);
- a second request for the same contract and name is 409 DUPLICATE_EVENT; no event exists without the owner's command.

**2. Chart on an old database.** A database built from P1's `schema_1031.sql` (no chart row for the new codes), opened by the repo code
with `swaps` bound: one journal line on each of 4451, 4452, 4453, 1335 and 2345 is accepted with foreign keys on.

**3. RFQ tests** (`test_terminal_rfq.py`, the matcher never started): request -> the RFQ_DEALER job quotes on the next `tick(at=...)`
-> accept books; a quote past its TTL is EXPIRED by the job and acceptance is refused; Pending (C7) lists REQUESTED / QUOTED.

**4. Intraday test:** a FakeProvider ORCL +1.00 moves the short 38,700 TRS MV by -38,700.00 versus the overlay.

**5. Owner-DB copy** (fresh sqlite3-backup copy, served online with real current inputs: quotes, FRED including BAMLC0A0CM, the
Treasury curve):
- Through the RFQ flow (request -> quote -> accept), book one TRS (short 100 ORCL), one CDX IG (buy protection 1,000,000) and one IRS
  (pay fixed 1,000,000, 3Y): every booking posts (IM to 1330, the CDX upfront to 2345, the contract rows).
- Then, on the same copy through `build_runtime` with a clock moved past the next 00:30 ET: the night's accruals post (TRS financing to
  4451, CDX premium and IRS legs to 4453); the booking date's VM posts when its inputs exist, else CLOSE_PENDING (never an error).
- Integrity full ok; the Derivatives tabs render.

**6. Browser walk:** `#/derivatives`, all tabs; RFQ request -> quote -> accept on a seeded home; 0 console errors, 0 HTTP >= 400.

### P6 - Cash, settlement, collateral and margin

**Owns**
- Python:
  - `T\collateral.py` (new)
  - `T\lifecycle_jobs.py`, `T\lending.py`, `T\lending_accruals.py`, `T\prime.py`, `T\repo.py`, `T\counterparties.py`,
    `T\treasury.py`, `T\products\treasuries.py`
- Static:
  - `T\static\pages\accounts.js` (new), `T\static\pages\cash_collateral.js` (new)
  - `T\static\pages\cash.js`, `T\static\pages\treasury.js`, `T\static\components\pbschedule.js`
- Tests:
  - new: `X\test_terminal_settlement_fails.py`, `X\test_terminal_margin.py`, `X\test_terminal_collateral.py`
  - existing: `X\test_terminal_sweeps.py`, `X\test_terminal_lending.py`, `X\test_terminal_shorts.py`, `X\test_terminal_lifecycle.py`,
    `X\test_terminal_prime.py`, `X\test_terminal_treasury.py`, `X\test_terminal_fx.py`, `X\test_terminal_loans.py`,
    `X\test_terminal_books.py`, `X\test_terminal_futures.py`, `X\test_terminal_persistence.py`

**Depends on:** P1a. **Sections:** 3 (Executed -> Pending Settlement -> Settled / Failed -> Resolved), 5 / 17 (as-of cash), 12, 14, 16
(Accounts, Cash & Collateral), 18 #8, #9, #13, #19, #20, #21.

**Gaps:** OMS-09, PORTFOLIO-09, PORTFOLIO-10, PORTFOLIO-11, PORTFOLIO-12, PORTFOLIO-16, PORTFOLIO-18, UI-09, UI-10, TEST-03, TEST-10
(regression test).

#### Task

**1. Hooks first: C15, C16, C17, C21.** `collateral.py` binds as an optional module: schema, commands, product jobs via
`lifecycle_eod.add_product_job`, routes via C1.

**2. Settlement fail / resolve (OMS-09).**
- An unfunded payable past its due date becomes `fail_state` FAILED with its reason, keeps the existing ERROR attention and keeps
  retrying. When it later settles it becomes RESOLVED, with `settled_ts`.
- `settlement.fail {settle_id, reason}` is an owner-triggered simulation, off by default, labelled "simulated counterparty fail" (Q11).
  While FAILED, SETTLE_DUE skips the row.
- `settlement.resolve {settle_id, action}`:
  - `retry`: back to normal settlement, RESOLVED on settle;
  - `cash_compensation {amount}`: hand-worked postings, labelled;
  - `buy_in {cost}`: settles the obligation and posts the buy-in cost separately (GL 5700).
- The actions are offered in P2's Completed Trades drawer (C15); P6 owns the commands and their data.
- C9g keeps integrity green.

**3. Restricted cash (PORTFOLIO-10), net per CSA (C21, D9).**
- `collateral.restricted_c` = max(0, balance(2330) - balance(1320)) of the account (one CSA per PB relationship) + balance(2520)
  (securities-lending cash collateral, per lending agreement, never netted). With `as_of` = d it reads business-date balances (C21).
- Registered as the C9b adjuster "restricted (collateral received)"; SWEEP_OUT subtracts `accounting.adjustments_c` from its excess.
- With `csa_netting=True` (P4's CSA payments through P2's `fund`) only the 2520 part is subtracted: a VM payment to GS may use GS's
  collateral; nothing else may.
- The PB credit interest base (`pbcredit`) becomes max(0, settled - restricted): the fund earns no credit interest on the counterparty's
  collateral. CSA interest on cash collateral is not simulated (C6 row, Q7).
- A per-CSA "reuse allowed" flag defaults to off (Q7).
- The CashView, account cash page, Cash & Collateral page and Dashboard show "restricted".

**4. Equity margin (PORTFOLIO-11), account level, Reg T (Q6).**
- Requirement: 50 % initial / 30 % maintenance of the physical short market value beyond the proceeds held.
- Fully paid longs count as eligible collateral at a 50 % loan value; free cash counts at 100 %.
- Only a deficit is reserved, through the C9b adjuster "margin requirement"; nothing moves in cash.
- Terms are assumed and editable per account with the light command `margin.terms {account_id, initial_pct, maintenance_pct,
  long_loan_value_pct}` (SETTINGS_CHANGED), kept in its own table.
- Shown on Cash & Collateral and position rows (C16); P3 shows it on Risk.

**5. Margin calls (PORTFOLIO-12).**
- A `margin_calls` table and a MARGIN_CALL product job after the day's VM, COLLATERAL_MARK and margin recompute (17:20 ET).
- Calls cover futures VM, OTC CSA (VM delivery and IM shortfall), stock-borrow collateral top-ups and Reg T deficits.
- Each call records counterparty, amount and due time (T+1 10:00 ET). It is MET when funded (linking the funding transfers); otherwise
  it stays OPEN with an ERROR attention and becomes UNMET after the due time.
- Forced liquidation: never (C6).
- The calls list shows on Cash & Collateral and Accounts; `collateral.margin_calls_rows` feeds P3's Activity type (C16).

**6. T-bills and securities collateral (PORTFOLIO-09).**
- T-bill holdings of any account of the book show as "Treasury securities / cash equivalents" (at market, maturity, accrued discount)
  on Treasury and Cash & Collateral.
- A `collateral_pledges` table (account, instrument, face, CSA haircut, pledged to PB / contract, status) with `collateral.pledge` /
  `collateral.release` commands.
  - Eligibility and haircuts come from the GS CSA terms (`prime` FIELD_SPECS: assumed, editable).
  - `repo.pledged_face` includes CSA pledges, so a pledged face cannot be sold (the existing C27 path in the bond SELL).
  - Pledges move no GL balance. They count as encumbrance and as eligible collateral in the requirement-vs-eligible view.
  - `collateral.pledges_view(services, scope)` lists them (C16; the C6 probe).
- Releasing cash IM against pledged securities is deferred (section 9).

**7. Cash & Collateral page (UI-10, PORTFOLIO-16).** At `#/cash` (Shift+F5):
- cash by account and currency, through C9f;
- available / restricted / reserved split;
- settlement ladder;
- short proceeds vs collateral at the lender, per borrow and in total;
- interest and financing by type and period;
- collateral by counterparty (posted / received, net per CSA, cash vs securities, IM vs VM, requirement vs eligible);
- margin required / posted / excess per account, with the Reg T terms;
- margin calls;
- pledges;
- an assumptions editor (`margin.terms`, the CSA reuse flag).

**8. Accounts page (UI-09, PORTFOLIO-18).**
- `#/accounts` (Shift+F4) lists every account of the book (Treasury, US Exchange, Debt, Credit, Foreign & FX, Private, each PB) with
  NAV, cash, available, reserved, restricted, margin, working, settling, attention and custodian (C16).
  - A row opens its account tab bar; Treasury opens `#/treasury` (F10 stays).
- The header button "Open prime broker account" opens `T.components.sidebar.openPbModal`, the existing onboarding flow; nothing is
  ever created automatically.
- Links to Settings > Accounts / Prime brokers.
- A "positions by custodian" statement. Custodian mapping:

  | Account | Custodian |
  |---|---|
  | US Exchange | PB custody, DVP |
  | Listed derivatives | OCC / CME via the FCM |
  | OTC | bilateral GS under ISDA / CSA |
  | Treasury | bank |

**9. As-of support for P5 (C15, C17).**
- SETTLE_DUE dating of as-of settlements; Treasury transfer stamping from `ctx.asof_date`; `treasury.sweep_in` checks the
  business-date figure under `ctx.asof_date` (C15; P2's `fund` depends on it).
- `cash.asof_sweep` per past business date: at most each account's import cash, restricted as of that date never swept, the owner's
  pre-import idle cash never swept (C15). `cash.asof_transfer`: dated, stored as `transfers.kind` TRANSFER with `linked_type`
  "asof_funding" and the line key, journal entry kind ASOF_FUNDING, memo "as-of funding" (C15). Both refuse actor `ui`.
- `cash.reaccrue` for `cashint`, `pbcredit` and `pb`, every account whose business-date balance changed, original night rates,
  `accrue_segment`'s rounding, through each account's last accrued night, corrections dated night + 1 (C17).
- `lending.lender_label` (C16); `MODELLED_LENDER` becomes "street lender (assumed terms)" for new borrows; the 5 stored borrow rows stay
  untouched.

**10. Regression test (TEST-10).** Port the hotfix proof `prove_short.py` (22 checks) as a test in `test_terminal_shorts.py`:
- preview need 30,032.16;
- collateral 1,530,281.52 posted at the fill;
- +10 % call 153,073.44 / -5 % excess 84,145.10;
- cover: realised -67,576.80 and 1,599,209.86 returned.

#### Acceptance
1. **Hand-worked money tests:**
   - (a) Failed settlement: FAILED -> retry -> RESOLVED, with no cash moving while FAILED; cash_compensation and buy_in postings.
   - (b) Restricted, net per CSA: TRS VM received 600,921.00 and CDX / IRS VM posted 309,331.86 under the GS CSA -> restricted
     291,589.14. Available and SWEEP_OUT exclude it; a VM payment to GS may use it (`csa_netting=True`); a borrow collateral top-up in the
     same account may not. Securities-lending cash collateral stays fully restricted: lending shares out leaves available unchanged (on
     the inventory's owner-DB copy, lending 100 CRWD raised US Exchange available from 7,694.70 to 34,863.42 without this rule). The PB
     credit base excludes the restricted amount.
   - (c) Reg T, excess case: shorts 1,000,000.00, fully paid longs 3,000,000.00, cash 100,000.00 -> requirement 500,000.00, eligible
     1,600,000.00, no reservation.
   - (c') Reg T, deficit case: longs 600,000.00 -> eligible 400,000.00 -> deficit 100,000.00 reserved, and a call 100,000.00 that ends
     MET (funded) or UNMET (after the due time).
   - (d) Pledge 1,000,000 face of a bill at 99.00 with a 2 % haircut -> eligible 970,200.00; the pledged face cannot be sold.
   - (e) `cash.reaccrue`: Treasury base 1,000,000.00 lower on two nights accrued at 3.38 % and 3.40 % -> corrections -93.89 and -94.44
     (each night its own segment: the rates differ; C17's rounding), each with business date night + 1; PB credit base +10,000.00 on a
     night accrued at 3.13 % -> +0.87; each night keeps its original rate; a rerun posts nothing. Two nights at the same base and rate
     follow the segment rule: PB credit base +10,000.00 on two nights at 3.15 % -> +0.88, then +0.87 (cumulative 1.75).
   - (f) An as-of settlement posts on its due date.
   - (g) The `prove_short` regression.
   - (h) As-of sweep: a PB account at the close of D with settled 786,553.40, GL 2330 489,357.00, GL 1320 309,027.55 (restricted
     180,329.45) and buffer 10,000.00 -> `cash.asof_sweep` posts 596,223.95 to Treasury with business date D; a rerun posts nothing; a
     date with settled <= restricted + buffer posts nothing. All of that account's cash came from the import's groups; the restricted
     amount is `restricted_c(as_of=D)` (180,329.45), never today's (291,589.14 would sweep only 484,964.26). Preserved history: an
     account with 509,392.94 of pre-import excess at the close of D and no import cash is not swept.
   - (i) As-of transfer: stored as `transfers.kind` TRANSFER, `linked_type` "asof_funding", `linked_id` the line key, journal entry kind
     ASOF_FUNDING, memo "as-of funding", dated `business_date`; NAV unchanged, internal transfers net to zero (L5); actor `ui` refused; no
     CHECK error on a database built from `schema_1031.sql`.
   - (j) `treasury.sweep_in` under `ctx.asof_date` = D checks the business-date figure: with Treasury at 900,000.00 at the close of D and
     5,000,000.00 now, a 950,000.00 as-of sweep is refused 409 INSUFFICIENT_FUNDS; with 25,000,000.00 at the close of D and 2,000,000.00
     now, a 1,701,918.50 as-of sweep posts with business_date D (today's `sweepable_c` is not consulted). The current-balance floor is
     P2's (C20 test i).
2. **Owner-DB copy:**
   - Accounts lists the 7 F1 accounts.
   - Cash & Collateral shows short proceeds (GL 2600) vs collateral at the lender (GL 1400), the fund cash between them, Treasury interest,
     rebates, borrow fees and futures IM equal to the ledger on the copy (at 11:30 EDT 10-27: 6,434,977.23 vs 6,555,351.10, fund cash
     120,373.87, interest 4,366.23, rebates 3,090.69, fees 409.61, IM 109,400.00).
   - The US Exchange Reg T shows an excess, with no reservation; restricted is 0 (no OTC yet).
   - Integrity is ok.
3. **Browser walk** (P6's pages):
   - `#/accounts`: the open-PB modal opens and is cancelled.
   - `#/cash`: restricted line, margin calls list, Reg T terms, pledges, assumptions editor.
   - Shift+F5 on an account page opens that account's cash page.
   - Treasury shows its "Treasury securities: none" line.
   - 0 console errors, 0 HTTP >= 400.
   - The settlement actions in the Completed Trades drawer (P2), the Activity margin calls and the Risk Reg T panel (P3) read P6's
     commands and functions; P8 walks them on the integrated build.

#### As built (1.0.6)

**Task 4.** The C9b adjuster "margin requirement (Reg T)" holds back the account's own free cash that the requirement needs after the longs' loan value: min(free cash, max(0, requirement - loan value)). The plan said only the deficit. Reason: under 'only a deficit is reserved', the cash the owner moves in to meet a Reg T call becomes excess the moment the call is met. SWEEP_OUT would then return it to Treasury at 17:30 and the call would reopen the next evening. The acceptance figures do not change: (c) holds back 0.00; (c') holds back 100,000.00 and raises a 100,000.00 call. Only fully paid longs count, so the loan value is net of PB financing (GL 2700). Free cash is settled plus receivables, minus payables, restricted cash and the short-option reservation, floored at 0.

**Task 5.** The MARGIN_CALL job records calls and never moves cash. A first version funded Reg T deficits with a Treasury transfer, which broke P8's whole-fund reconciliation test.
- Variation-margin calls (futures, OTC CSA) and borrow-collateral top-ups are MET by their own payment: own cash, one linked Treasury sweep, or PB financing. They stay OPEN, with an ERROR attention, only while that payment's FUNDING_SHORTFALL is open.
- An OTC IM shortfall and a Reg T deficit stay OPEN until they are cured by transfers into the account (linked). They become UNMET after the due time (next business day 10:00 ET) and are cured later when covered.
- Calls start forward: collateral_state.margin_call_start_ts is written at the first bind, and no call is issued for a business day whose 17:20 ET came before it.

**Task 8.** The custodian map also covers: Debt 'custodian bank, DVP'; Credit 'bank'; Foreign & FX 'global custodian'; Private 'issuer register'; listed options 'OCC via the clearing broker'; futures 'CME Clearing (via the futures commission merchant)'; a PB account '\<broker\>: bilateral ISDA / CSA'.

### P5 - Historical import and reconciliation

**Owns**
- Python: `T\importer.py` (new)
- Data (new): `T\data\imports\IMP-F1-20261027-A.jsonl`, a byte-for-byte copy of batch_final.jsonl. The data pack is NOT a repo file: it
  is fetched on the apply day (a pack frozen at build time would be stale by the deploy).
- Static: `T\static\components\imports.js` (new)
- Tests: `X\test_terminal_import.py` (new), `X\fixtures\terminal\import\` (new: the 9-line mini batch and its small fixture pack)
- The batch: `C:\Users\logan\AppData\Local\Temp\claude\C--Users-logan-intelligence-terminal\5e0edd51-72c4-438a-89eb-77c644650257\scratchpad\import\batch_final.jsonl`

**Depends on:** P1b, P2, P3, P4, P6, P7. **Sections:** 5, 6 (validation, differences), 7-11 (booking through P2 / P4), 12-13 (cash and NAV
effects), 17, 18 #10, #11, #22.

**Gaps:** TEST-01, OMS-04 (orchestration), PORTFOLIO-01 (the catch-up and restatement run). It also carries the 35 reconciliation
lines (section 7).

#### Task

**1. Batch format and validation** (format_version 2).
- Records: batch, setup, line, package, catch_up, summary.
- `idem_canonical` = `account_id|product|instrument|side|qty|trade_date[|key terms]`, product = the instrument master kind (EQUITY,
  REIT, ETF, FUTURE, OPTION, BOND - a T-bill is BOND) or OTC_TRS / OTC_CDX / OTC_IRS; trade_date = the app's trade date of the matched
  execution, which for a futures fill is the exchange trade date (lines 22-23: CME 2026-10-22 for fills made on the evening of 10-21 ET;
  the lines carry `key_trade_date`), else the intended trade date; prices, rates and negotiable terms are never in the key.
  `idem_key` = "imp:" + sha256(idem_canonical)[:24], recomputed and checked; the package key = "asofpkg:" + sha256(key32 + "|" +
  key33)[:24].
- decision in {preserve, import, declined, unresolved} (declined = the owner decided not to book it: line 34); command kinds allowed:
  `import.record_inputs`, `cdx.terms`, `trade.book_as_of`,
  `trade.book_as_of_package`, `otc.book_as_of`, `cash.asof_sweep`, `cash.asof_transfer`, `cash.reaccrue`, `nav.restate`.
- Payloads carry machine fields only (C8, C10); evidence and notes stay in the line, outside the command.
- Setup and catch-up commands carry deterministic request ids: `impset:<batch>:<step>[:<part>]` (S2, S2b, S4:IG, S4:HY) and
  `impcu:<batch>:<step>:<part>` (C2 per account and date, C3 per transfer, C4 per account and kind, C5 per lifetime and through
  date). The bus replays a same-id same-payload command, so a resumed apply replays them instead of failing.
- `historical_model` true lines book with `as_of.historical_model` and get the "Historical model (not an independent execution)" source
  label (D1). There are none in this batch; the mini batch has one.

**2. Report mode (read-only reader).** It opens `<home>\terminal.db` through a sqlite3 URI with `mode=ro`, never `Database.open` (which
would back up, migrate and write `meta`), so it writes nothing even beside a running server. Per line, search:
- orders (BTO / STO and cancelled ones included);
- fills (sum of quantity, VWAP, trade dates, with the exchange convention noted, e.g. CME after 18:00 ET = next day);
- lots, borrows, settlements, `futures_settle`;
- `otc_contracts` by kind and terms;
- `import_lines` and the `import_key` columns by `idem_key`.

Result per line: present_match / present_differs / missing_import / already_imported / declined / unresolved (a line whose decision
is declined or unresolved reports that status; its search result stays in `match`). The output is a text table and JSON, with a
`match_hash`: sha256 over the identities of the matched rows and of the F1 rows the import reads (orders, fills, lots, borrows,
settlements, otc_contracts, transfers and nav_snapshots of F1, and the F1 journal's max entry id). It does not use the global
state_version, which every provider-health update moves while a server runs; the dry run and the apply also compare the source's
`meta.state_version` and `MAX(commands.id)`, which nothing may move while the server is stopped (task 5).

**3. Data pack (`--fetch-data-pack`).**
- Run on the apply day, from the installed 1.0.5 release folder (section 7), so the pack is fetched by the code that applies it.
- Window: 2026-10-21 .. L, L = the last business day before today. Read-only fetches through the app's providers, each item with source
  and fetched_ts:
  - Yahoo daily bars: ORCL, META and the 5 OCC contracts (with volume);
  - the Cboe delayed chain rows of the 5 contracts (for `instrument_spec.chain_row`) and their `prev_close` = the official close of L;
  - FRED: BAMLC0A0CM (missing from the DB), BAMLH0A0HYM2, SOFR (NY Fed as fallback);
  - UST_PAR curves from the DB, or Treasury.gov when missing;
  - ORCL / META dividend events.
- It writes `<home>\imports\IMP-F1-20261027-A\pack-<ts>.json` and its sha256; the pack path and sha256 go into `import_batches`.
- Dry run and apply take `--pack <file>` and use ONLY that pack plus the DB: deterministic and network-free.
- S0 tests the inputs with the rules the jobs use and refuses before any write when one is missing: ORCL / META closes for every NYSE
  trading day; option closes per S2; UST_PAR through `CurveStore.for_date`'s 3-business-day lookback (an NYSE business day without a
  Treasury curve, such as Veterans Day 2026-11-11, passes); SOFR within the 10-day RATE_LAG; IG / HY OAS for every trading day except a
  date FRED skipped while it has a later value (that date's CDX VM is CAUGHT_UP and its move posts with the next VM, as live) and L when
  not yet published (its CDX VM stays CLOSE_PENDING for the live scheduler, never CAUGHT_UP).
- S0 also refuses when the apply date is after 2026-11-19, when the ET clock is at or after 16:00, or when an F1 snapshot dated on the
  apply day exists: the closing jobs from 16:20 (marks, expiry, resets, VM, sweep) and the 17:45 snapshot of the apply day must never
  fall inside the catch-up, whose pack ends at L.

**4. Inputs (S2 / S2b).** `import.record_inputs {marks, observations, batch_id}` (light, actor import): INSERT OR IGNORE of the pack's
CLOSE marks and FRED observations. A stored value that differs is listed in the report, never overwritten (the existing
`marks.record` / `rates.record` are INSERT OR REPLACE and are not used). Option marks follow the policy of C9d: the Cboe official close
for L, the Yahoo last trade before it, quality stale on a volume-0 day. A day without a bar gets a model mark (no trade that day),
labelled as such.
- After the S2 / S2b commands commit, the importer reloads `services.rates` (`RateStore.load_persisted`) and `services.curve`
  (`CurveStore.load_persisted`) from `curve_points`. The RateStore is filled once at `build_runtime`, so without the reload the CDX
  proxy (`rates.as_of_le`) would not see the pack's BAMLC0A0CM (absent from the owner's DB): line 26 would be refused 409
  ASOF_INPUT_MISSING and every IG VM date would stay CLOSE_PENDING. Marks need no reload (the TRS VM reads `marks` through the
  command's connection).

**5. Dry run.**
- On the live home the server is stopped first (`python -m finsim stop`; the importer refuses while `terminal.lock` is held) and stays
  stopped until the apply, so nothing moves between the two. Builder runs use a copy home (`--home` under `scratchpad\checks\`), where
  no server runs; they never touch `~/.finsim`.
- Takes a sqlite3-backup copy of the DB into a new work folder. Default `<home>\imports\<batch>\dryrun-<ts>`; builders always pass
  `--home` / `--work-dir` under `scratchpad\checks\`.
- Builds a maintenance runtime on the copy: `build_runtime(...)`, workers never started.
- Before the first write it records, on the source state:
  - the catch-up cutoff `{accounting_day, due_keys_sha256, state_version, max_command_id}`: `due_keys_sha256` = sha256 of the sorted
    due keys of every lifecycle job except INTEGRITY_FULL and HOUSEKEEPING, which post nothing (INTEGRITY_FULL gains a key every hour at
    :05); `state_version` / `max_command_id` = the source DB's `meta.state_version` and `MAX(commands.id)`, read through a `mode=ro`
    connection before `Database.open` or `build_runtime` touches the file (the dry run: when it takes the backup copy; the apply: before
    its pre-import backup), so nothing the runtime's own start writes can differ between the two runs. `now_ts` is recorded for
    information only;
  - the list of F1 `nav_snapshots` rows (account, date) and each account's `accrual_state.accrued_through`.
- Runs, in order:
  - S0 preflight; S1 schema check; S2 / S2b inputs, then the store reload (task 4); S3 chain-row check; S4 CDX basis derived from the
    pack (basis = dealt - half-spread - proxy of 10-21), skipped when the stored basis already equals it (a 400 NOTHING_TO_UPDATE also
    counts as done);
  - lines 24-28, then 29-31, then the package record (lines 32-33);
  - C1: every lifecycle job except SNAPSHOT (`lifecycle.run_now(job)` per job, in registry order) until nothing new; SNAPSHOT is held
    back so no snapshot is written before C2-C4 have posted;
  - C2: `cash.asof_sweep` per business date 2026-10-21 .. L, ascending, for the accounts the import credited (at most each account's
    import cash, C15);
  - C3: the business-date cash check: every F1 account's settled cash >= 0 at the close of every date 2026-10-21 .. the day before the
    apply, and every current balance >= 0 (L18). A business-date negative is moved by `cash.asof_transfer` from the account with the
    most idle business-date cash that date and back on the first later date it is needed, else refuse; a current shortfall refuses with
    the amount to move into Treasury first;
  - C4: `cash.reaccrue` (cashint, pbcredit, pb) through each account's last accrued night (C17);
  - C5: `nav.restate` of every F1 snapshot row recorded before the first write - every account and F1:ALL, 2026-10-21 .. the last
    snapshot date before the apply day, the dates after L (non-business days) at L's marks - with one group per import line plus the
    `funding` group (C9d), and a version row with original 0 where an account with no original row has restated figures that are not
    zero (F1:PB_GS 10-21);
  - C5b: SNAPSHOT once, so a date missed while the server was down (through the day before the apply) is taken late through `nav_as_of`
    on the final ledger;
  - C6: integrity full, trial balance, `PRAGMA wal_checkpoint(TRUNCATE)`.
- Reports: per line, the created ids and money; balances and IM; the business-date cash table per account per date (before and after
  C2) with every funding move; the restated NAV series per account and F1:ALL with its split (original, import effect by line,
  funding moves, interest, mark policy by position, dating line for a late snapshot); the bridge to the workbook; the cutoff, pack
  sha256 and match_hash.

**6. Apply.**
- Requires all of:
  - `--owner-go "<the owner's words>"`;
  - the server stopped: the importer takes `terminal.lock` and `<db>.lock` itself and refuses while they are held;
  - a pre-import backup through the sqlite3 backup API to `<home>\backups\import\terminal-pre-<batch>-<ts>.db` (outside
    `Database.backup`'s rotation, which keeps the 10 newest `terminal-*.db` directly in `<home>\backups`), with its sha256 written and
    re-read;
  - `match_hash`, the pack sha256 and the catch-up cutoff (its four fields, taken at the same point, before the first write) equal to the
    dry run's, else "re-run the dry run"; `now_ts` is not compared;
  - an ET clock before 16:00 and a date on or before 2026-11-19.
- Runs the same steps; each line is its own command with `request_id` = `idem_key` (the package uses its package key; setup and
  catch-up commands their `impset:` / `impcu:` ids, task 1).
- On failure the batch is marked PARTIAL, every committed line is consistent, and a re-run continues idempotently.
- Writes `import_batches` and `import_lines` (`source_json`, `source_label`, `created_refs_json`: order / fill / contract / entry /
  settlement / transfer ids).
- Ends with `PRAGMA wal_checkpoint(TRUNCATE)`, so no WAL content is left behind.

**7. Restore.** The CLI help and the report print the procedure of section 7 (stop; confirm no process and no lock; move the db, -wal and
-shm aside; copy the backup in from `<home>\backups\import\`; verify the sha256; integrity_check; start 1.0.5). A test runs it on a
temp home.

**8. CLI and API.**
- CLI: `python -m finsim import --batch FILE --home DIR --mode report|dry-run|apply [--fetch-data-pack] [--pack FILE] [--work-dir]
  [--owner-go]` through C18, and `python -m finsim.terminal.importer ...` directly. `--home` is required in every mode (there is no
  default home): the owner's procedure passes `--home "%USERPROFILE%\.finsim"` explicitly; builders and tests pass a folder under
  `scratchpad\checks\` or a temp home.
- API: C19, read-only.
- `imports.js` shows the batches, the decisions per line, the report, the business-date cash table and the restated NAV split; its
  hosts mount it only when `T.hasModule("importer")` (C2).

**9. Repo copy.** Copy batch_final.jsonl into `T\data\imports\` byte-for-byte and record both sha256 values. Change a decision only when
the owner answers a question, then re-run report and dry-run.

**10. Tests (TEST-01, section 18 #10, #11, #22).** `test_terminal_import.py`, on a temp home with FakeClock, FakeProvider, the fixture
pack and a 9-line mini batch:
- an existing fill (present_match, untouched);
- a differing fill (discrepancy reported, never corrected);
- an as-of option BTO;
- an as-of short TRS;
- an as-of CDX;
- an as-of IRS;
- an unresolved line;
- a declined line (status and decision `declined`, never booked, its search result kept in `match`);
- a `historical_model` true line (a modeled listed BUY).

Assertions:
- report statuses;
- the dry run leaves the source DB byte-identical (sha256 before = after);
- apply refuses without `--owner-go`, while the lock is held, when the match_hash, the pack sha256 or the cutoff changed (a FakeClock
  moved across 00:30, or a command run on the source in between), at or after 16:00 ET, and after 2026-11-19;
- a pack missing one date's input is refused before any write; an unpublished FRED value of L leaves that CDX VM CLOSE_PENDING;
- `import.record_inputs` never overwrites a stored mark and reports the difference;
- the backup exists in `<home>\backups\import\` (outside the rotation) with a matching sha256 before the first write;
- running apply twice posts nothing new (journal, fills and contract counts equal) and reports every line already_imported: setup and
  catch-up commands replay through their `impset:` / `impcu:` ids, S4 is skipped (or its NOTHING_TO_UPDATE counted as done) and
  `nav.restate` writes no new version;
- a PARTIAL apply resumes;
- a constructed business-date overdraft is resolved by a dated `cash.asof_transfer` pair (TRANSFER / `asof_funding` / entry kind
  ASOF_FUNDING, C15), or refused when no account had the cash;
- restated NAV rows exist for each date, for every account and the lifetime row, each with residual 0 (the `funding` group carries the
  as-of sweeps; an account with no original row gets a version row with original 0);
- Completed Trades rows show the D1 as-of labels with the entered time and batch, and the `historical_model` line shows "Historical model
  (not an independent execution)" and not the as-of label; `import_lines.source_label` matches;
- after apply the -wal file is empty; the restore procedure brings back the pre-import file (sha256 equal, integrity ok);
- historical NAV is reconstructed and every component is explained;
- report mode opens the source `mode=ro` and leaves it byte-identical while another connection holds it; no mode runs without
  `--home`; nothing under `%USERPROFILE%\.finsim` changes during the suite;
- S2b on a database with no BAMLC0A0CM rows: after the store reload, S4 derives the basis from the pack, the IG line deals exactly
  64 bp and its VM dates post (not CLOSE_PENDING);
- a weekend between L and the apply day (apply on a Monday): the Saturday and Sunday snapshots are restated at Friday's marks, their
  cash-interest nights are reaccrued, Monday's TODAY baseline is the restated Sunday, and M4 raises nothing;
- the cutoff ignores INTEGRITY_FULL and HOUSEKEEPING keys (a FakeClock moved across hh:05 keeps it equal);
- an as-of sweep never moves pre-import idle cash (an account with excess before the import and no import cash is not swept);
- a window with an NYSE business day that has no Treasury curve passes S0 through C12's lookback; an OAS date FRED skipped while it
  has a later value is CAUGHT_UP, as live.

#### Acceptance

**1. Unit tests above:** green on py-3.12 and py-3.14.

**2. Real batch on a fresh owner-DB copy** (new folder under `scratchpad\checks\`; pack fetched at build time, window through the
build day's L; the figures below are through 10-26, later dates follow the same rules).

*Report counts:*

| Lines | Status | Decision |
|---|---|---|
| 1-21 | present_differs | preserve |
| 22-23 | present_match | preserve |
| 24-33 | missing_import | import |
| 34 | declined | declined (the owner's answer to Q5, 2026-10-27 15:04 EDT) |
| 35 | unresolved | unresolved |

*Dry-run figures:*
- OTC IM 1,405,615.50: TRS 499,733.10 + 502,214.40, CDX 60,000.00 + 240,000.00, IRS 103,668.00.
- Options: premium paid 593,883.00, received 153,750.00, fees 561.60; net cash -440,694.60, settling 2026-10-23.
- CDX initial payments +309,920.95, settling 2026-10-22.
- TRS VM per date as P4's hand-worked numbers (TRS alone +600,921.00 through 10-26). Net CTM VM of all five contracts through 10-26 =
  +291,589.14 (TRS +600,921.00, CDX -286,487.16, IRS -22,844.70) = the restricted amount at 10-26.
- The CDX IG figures, and so the CDX, net CTM and restricted totals above, are estimates: they rest on IG proxies implied from the
  reconciliation's marks (0.89 / 0.91 / 0.90 % for 10-22 / 10-23 / 10-26; only 10-21's 0.88 % was fetched). The acceptance compares the
  IG VM with `swaps_cds.value_c` on the pack's own values; each 1 bp of difference moves the IG value by CS01 1,366.84.
- Business-date cash: every F1 account closes every date at or above zero. F1:TREASURY at the close of 10-22 is about -234,337.70
  before C2 and about +361,886.25 after the PB as-of sweep of about 596,223.95; no as-of transfer is expected (any one shows in the
  report).
- Interest restatement through night 10-26 (C17's segment rounding): Treasury about -848.07, PB credit about +4.38.
- Integrity full ok; trial balance balanced; the -wal file empty at the end.

*Restated NAV with its split* (estimates; the dry-run report is authoritative):
- 10-23 about 25,682,406.37 (variant A) = original 25,215,528.08 + import +463,928.82 + interest -262.73 + mark policy +3,212.20 (the
  IG part an estimate); about
  -88,294 vs the workbook's 25,770,700.86, explained line by line in the bridge (equity fills vs assumed opens -101,731.44,
  commissions +638.16, futures -691.95, options -10,836.51, TRS financing +228.21, CDX +13,860.86, IRS +5,091.02, carry +2,197.70,
  plus the interest and mark-policy lines).
- 10-26 about 26,011,264 (variant A) when 10-26 is L (options at the Cboe official close of 10-26); about 26,006,111 when the window
  runs past 10-26 (options at the Yahoo last trades, -5,152.50).
- Every date's residual is 0, for every account and for F1:ALL; F1:PB_GS gets a 10-21 version row with original 0; the `funding`
  group carries the 10-22 PB sweep (F1:PB_GS -596,223.95, F1:TREASURY +596,223.95, F1:ALL 0).

*Idempotency:* a second dry run on the result posts 0 entries.

**3. Browser walk on the dry-run copy** (`serve --offline`):
- Completed Trades lists the 10 as-of executions with the AS-OF badge, the D1 labels and the entered time.
- Portfolio shows the 5 options and 5 OTC contracts with marks.
- Derivatives > OTC contracts lists 5 contracts.
- History NAV shows the restated values with the original toggle and each date's split.
- The Imports panel shows the batch, the business-date cash table and the funding moves.
- 0 console errors.

### P7 - Windows startup, launcher and README

**Owns**
- Launcher files: `C:\Users\logan\intelligence-terminal\finsim\__main__.py` (new outer shim),
  `C:\Users\logan\intelligence-terminal\FinSim.cmd` (new), `C:\Users\logan\intelligence-terminal\finsim\FinSim.cmd` (new)
- Python: `T\app.py`
- README: `C:\Users\logan\intelligence-terminal\finsim\README.md` (the FinSim1 parts only; the FinSim2 block is protected)
- Tests:
  - existing: `X\test_terminal_app.py`, `X\test_terminal_ui_ops.py`, `X\test_terminal_bonds.py`
  - new: `X\test_terminal_launch.py`, `X\run_terminal_suite.py`

**Depends on:** P1a. **Sections:** 2 (README), 18 #25, 19.

**Gaps:** STARTUP-01, STARTUP-02, STARTUP-04, STARTUP-05 (`finsim\README.md`), STARTUP-06 (reporting), STARTUP-07 (launcher logging and
status), STARTUP-09, UI-14, UI-19, TEST-05, TEST-08 (the runner), TEST-09.

#### Task

**1. The outer shim (STARTUP-01).**
- `finsim\__main__.py` in the outer folder, which is not a package, so FinSim2 is untouched:
  - put its own folder at `sys.path[0]`;
  - drop `finsim` and every `finsim.*` from `sys.modules`;
  - `from finsim.terminal.app import main; sys.exit(main())`.
- With it, both of these work:
  - `cd C:\Users\logan\intelligence-terminal; python -m finsim serve`
  - `cd finsim; python -m finsim serve` (the inner package, unchanged)
- Never pip-install (pyproject's `finsim*` would also capture finsim2), and never edit `finsim\finsim\app.py`: FinSim2's launcher
  imports it.

**2. Launcher (STARTUP-02).** `FinSim.cmd` at the repo root, and the same file in `finsim\`:
- `@echo off`, `setlocal`, `pushd` to the inner folder;
- `py -3.12` when `where py` succeeds, else `python`;
- no arguments -> `-m finsim open`; otherwise `-m finsim %*`;
- `popd`, `exit /b` with the exit code.
- Document `.\FinSim.cmd serve` for PowerShell.

**3. Two code trees (STARTUP-04).**
- `status` reads `repo = "..."` and `py = "..."` from the installed FinSim Server.vbs and prints "Startup entry -> \<tree\> (VERSION
  from its terminal\__init__.py, SHA from its RELEASE.txt)".
- When that tree differs from the running one, `status` replaces the "restart" advice with the two real choices: open the installed app,
  or `stop` then `serve` here.
- `cmd_serve` prints one line naming the code tree, VERSION and DB. On the default home it adds "the FinSim icon and Windows login start
  \<tree\> instead" when the installed launcher points elsewhere.
- `install` refuses to run from a git working tree unless given `--from-working-tree` (owner rule: no deploy without the owner's flag).

**4. Old shortcuts (STARTUP-06, Q9).** `status` and `install` report any `FinSim*.lnk` whose target is `chrome_proxy.exe` as "a Chrome
app shortcut that does not start the server". They never delete it.

**5. Launcher logging (STARTUP-07).** In `_SERVER_VBS`, append a line to `%USERPROFILE%\.finsim\launcher.log` before each
`WScript.Quit 1`. `status` reports whether the installed VBS's `pythonw` and tree exist. Unit-test the VBS text as data; never execute
registration code (scheduled-task lesson).

**6. No-argument run (STARTUP-09).** `python -m finsim` with no command prints a 3-line hint (serve / open / status) and exits 0.

**7. `legacy` subcommand (UI-19, Q8 default).** Remove it from FinSim1's CLI. Delete nothing: the old code and `finsim.db` stay as they
are.

**8. Import subcommand (C18).** `import` dispatches to `importer.main` (imported lazily).

**9. README (STARTUP-05, UI-14).**
- The FinSim2 block is protected by content, not by line numbers: from the line "### FinSim2 — portfolio & quant analytics" through the
  line "`finsim/quant/catalog.py` holds each equation's LaTeX, a note and its implementation." and the blank line after it - today
  lines 58-292 (line 291 is the catalog line; line 293 is "### As a local app"). Hash the block before editing; its sha256 must be
  unchanged after.
- Rewrite only the FinSim1 parts: today lines 1-57 and 293-799. Cover:
  - what FinSim1 is: paper trading, execution, settlement, custody, prime brokerage and accounting, with no news or briefings;
  - how to start it (the commands above; `python` = 3.12.10, `py -3.12` the same, `py` / `python3` 3.14.5 also work; FinSim.cmd);
  - status / stop / install / uninstall / import, with their exit codes;
  - the installed app: the Startup entry, the single FinSim icon to use, the release folder;
  - data: `%USERPROFILE%\.finsim\terminal.db` and the automatic backups;
  - the pages in the new navigation order, the keys, and market-data sources only;
  - tests: `python tests\run_terminal_suite.py`, or `python -m unittest discover -s tests -t . -p "test_terminal_*.py"`;
  - why the repo-root launch failed.
- The retired game's text is dropped. The CHANGELOG / root README / runbook edits are returned as text for the orchestrator.
- The FinSim2 session works in the same tree, so `README.md` is committed only through the FinSim1 commit path, which first compares the
  block with the FinSim2 branch's committed copy (section 12).

**10. Suite runner (TEST-08 runner).** `tests\run_terminal_suite.py` carries the `run_suite.py` logic and prints "RAN n FAIL n ERR n
SKIP n FILES n".

**11. ResourceWarnings (TEST-09).** Open the fixtures in `test_terminal_bonds.py` (lines 48-50) with `with open(...)`.

#### Acceptance
1. `test_terminal_launch.py` runs subprocesses with `sys.executable`:
   - `-m finsim --help` exits 0 from both the repo root and `finsim\`.
   - `serve --offline --port 0 --home <tmp>` reaches "ready" and `/api/health` carries the terminal marker.
   - A second serve exits 3 with "already running", the URL and the ET time (P1a's wording).
   - POST `/api/shutdown` exits 0 and the lock files are gone.
   - `cmd /c FinSim.cmd --help` works from another cwd, for both copies.
   - A database in the 1.0.3.1 schema, built at test time from P1's `schema_1031.sql` (no `.db` fixture: `finsim\.gitignore` ignores
     `*.db`), starts: migrations applied, chart synced, pre-start backup present, integrity ok.
2. Pure tests: VBS parsing and the status text; the install refusal from a working tree; Chrome-app shortcut detection on a fake
   target; the no-argument hint; `legacy` gone.
3. README: the FinSim1 parts contain none of "Daily News", "briefing", "headlines", "newspaper"; the FinSim2 block's sha256 is
   unchanged.
4. `run_terminal_suite.py` is green on py-3.12 and py-3.14.
5. No ResourceWarnings.
6. P7's own written-file list contains no path under `finsim\finsim2` (the FinSim2 session edits that tree at the same time, so tree
   hashes would prove nothing about P7); the README's FinSim2 block hash is unchanged (item 3).

### P8 - Acceptance: browser smoke, directive map, whole-fund reconciliation, end-to-end proof

**Owns:** `X\test_terminal_browser_smoke.py`, `X\terminal_cdp.py`, `X\test_terminal_directive_map.py`,
`X\test_terminal_fund_reconcile.py` (all new).

**Depends on:** P1-P7. **Sections:** 18 (all 25 areas and the browser check), 20.

**Gaps:** TEST-04, TEST-06, TEST-08 (directive map).

#### Task
1. **`terminal_cdp.py`.** FinSim1's own standard-library CDP client, copied in approach from `tests\_cdp.py`, which stays untouched
   because it is FinSim2's. It is skipped when neither Edge nor Chrome is present.
2. **`test_terminal_browser_smoke.py`** (TEST-06). Start `serve(offline)` with a seeded FakeProvider on a temp home. Open each main
   route: Dashboard, Portfolio, Trading, Pending Trades (each status filter), Completed Trades (a drawer), Accounts, Cash & Collateral,
   Derivatives, History, Settings, Risk, Accounting, Activity. Assert:
   - 0 console errors and 0 HTTP >= 400;
   - a rendered main table or tiles;
   - none of the news feature's strings (P1's list and allowlist), and no "AI Research".
3. **`test_terminal_directive_map.py`** (TEST-08). A DIR_MAP of the 25 section-18 areas to `[(module, test)]`, checked by a
   `_has_test` guard like D_MAP. Every area maps to at least one existing, passing test.
4. **`test_terminal_fund_reconcile.py`** (TEST-04). Hand-worked numbers on a temp home with FakeClock and FakeProvider.
   - Book the mixed fund: equity long, physical short, short TRS, futures long / short, CDX buy protection, pay-fixed IRS, the option
     spread, and a T-bill pledged under the CSA.
   - Run two days of lifecycle jobs.
   - After every step assert:
     - NAV = assets - liabilities;
     - TRS / IRS / CDS notionals are never carried at face;
     - collateral transfers (cash and T-bill) change no P&L;
     - restricted cash = net CSA collateral received + securities-lending cash collateral, and it is never swept;
     - every account's business-date settled cash is >= 0 at the close of every date;
     - PB balances tie to the consolidated balance sheet;
     - futures VM ties to the cash ledger;
     - integrity full is ok.
5. **End-to-end proof.**
   - Run the suite on py-3.12 and py-3.14 and record the exact counts.
   - On a fresh owner-DB copy, fetch a data pack (build-day window), run the dry run of IMP-F1-20261027-A with it, then serve the result.
   - Page walk with screenshots: Dashboard; Portfolio (all positions, marks, P&L); Pending (a working order placed on the copy: modify /
     cancel; the Cancelled filter with O-000008 / O-000018); Completed (historical as-of and new executions, settlement status, a
     simulated fail and its resolution); Accounts; Cash & Collateral; Derivatives; History (restated NAV and its split); Activity (margin
     calls); Risk (Reg T); Settings.
   - Write the section-20 evidence pack: counts, NAV, cash, collateral, derivatives, test results, limitations.
   - Measure `/api/dashboard?book=F` on the served dry-run copy: the whole answer, providers included, within 300 ms of server time;
     a provider over budget is a defect for its owner.
   - Report defects to the owning package. P8 edits nothing outside its own files.

#### Acceptance
1. Both suites are green (skips only with stated reasons).
2. The directive map is complete.
3. The fund reconcile test passes.
4. The browser smoke passes on py-3.12 / py-3.14 where a browser exists.
5. The evidence pack and screenshots are in the scratchpad; the BUILD_LOG line is in the structured result.

## 7. Historical reconciliation and the import batch IMP-F1-20261027-A

### Files
- Batch: `scratchpad\import\batch_final.jsonl`, revision 3.
  - 45 records; sha256 `75690c19327713c6648d20b710495f5ef0b5a924ed263748b3f74aa51bd11d2f` (121,468 bytes).
  - Revision 3 built 15:33 EDT (script `scratchpad\build3\rev3_batch.py`) from revision 2 (13:46 EDT, match note of lines 29-33
    corrected 14:02 EDT; sha256 `2e3b8d2b82fd05d5085921847abf02709bbf5839e525bcd204e640a06f6c695c`, 109,124 bytes, kept as
    `scratchpad\build3\batch_final_rev2_backup.jsonl`), itself built from revision 1 (12:24 EDT, 44 records, sha256
    `9a13f0d4da03c8895ded4e1fa803c7472bcf8fbfbc92ae3a079e34eedba46973`) and the reconciliation's proposal (`batch_proposal.jsonl`,
    11:57). Revision 2 changed no decision; revision 3 changes one, on the owner's answer to Q5: line 34 is declined (its product is now
    BOND, so its idem key was recomputed; no other key changed).
- Matching: read-only copies at 11:27 (state_version 3187), 12:10 (3191), 13:22 (3200) and 14:54 (3208). Only provider-health,
  integrity and FRED rates.record commands ran in between, so every match still holds.

### Decisions

| Line(s) | Product | Decision | Basis |
|---|---|---|---|
| 1-15 | CRWD ZS PANW SNOW NOW WMT PG NKE SPCX LMT NOC KTOS GXO JNJ TMO longs | preserve | Real simulated executions on 10-22 (O-000002..O-000016, O-000020 for PG). Quantity, side and date match. Only the price differs from the workbook's assumed opens; reported, never corrected. GXO keeps its 225 fills. |
| 16-20 | STT C DLR VMRK IBIT physical shorts | preserve | Executed 10-22 with borrows B-000001..5. SPCX (Space Exploration Technologies Corp., CIK 1181412) and VMRK (Yahoo name "AvalonBay Communities, Inc.", the owner's merged residential REIT) are real Yahoo-quoted listings, not synthetic; identity preserved, no substitution. |
| 21 | PDBC | preserve | The app charged 250.25 of commission; the workbook charged none. |
| 22-23 | GCZ26 long 5 @ 4,140.98, ZNZ26 short 19 @ 105.3141 | preserve (match) | Executed 10-21 evening ET, CME trade date 10-22 (reported as a trade-date difference, not corrected). VM through 10-23: -64,440.00 and -14,812.50 (workbook -65,390.00 / -14,814.30: indicative marks vs settles). |
| 24-25 | TRS ORCL short 38,700 @ 129.13; TRS META short 7,200 @ 697.52 | import (as-of 2026-10-21, F1:PB_GS) | Owner clarification. Booked at the owner's reset prices (= the Yahoo 10-21 closes, kept as evidence; the DB holds no ORCL / META close). Notionals 4,997,331.00 / 5,022,144.00. IM 10 % (the GS schedule, the owner's answer to Q3). |
| 26-27 | CDX.NA.IG S47 5Y buy protection 3M @ 64 bp; CDX.NA.HY S47 5Y 4M @ 352 bp | import (as-of 2026-10-21) | Booked at the owner's dealt spreads. The account basis is derived from the pack's 10-21 proxies (10-27 view: IG -24.5 bp / HY +23.0 bp; Q4). Upfronts 48,492.57 / 241,622.83 received (workbook flat-duration estimates 46,440 / 230,880). |
| 28 | USD SOFR IRS pay 4.72 % 7.5M 3Y | import (as-of 2026-10-21, variant A, Q1) | The current UI refuses 4.72 % as OFF_MARKET on the Treasury par curve (4.890 % + 1 bp). Booked through `otc.book_as_of` at exactly 4.720 % with the calibrated contract curve spread. |
| 29-31 | ORCL 140C long 387 @ 3.55; META 660C long 72 @ 23.59; IBIT 46C long 105 @ 2.10 | import (as-of 2026-10-22 16:00 ET) | Simulated fills at the owner's premiums, which equal the 10-22 closing prints. |
| 32-33 | QQQ 700P long 150 @ 17.64; 675P short 150 @ 10.25 | import (one BEAR_PUT_SPREAD package record; request id `asofpkg:88d48966d8ba2bafae94bd16`) | Premiums taken from the workbook (directive: "Retrieve ... do not invent") and confirmed by the 10-22 closes. The long leg books first, so reserved margin stays 0. |
| 34 | US T-bills 5M reserve | declined (the owner's answer to Q5, 2026-10-27 15:04 EDT: no T-bill reserve) | Also not fundable from about 3.71M of cash after the import; a CSA pledge of bills would not release cash IM already posted (9.10). |
| 35 | Residual operating cash 144,903.50 | unresolved (not a transaction) | Cash is the ledger's result; never imported or forced. |

### Cash, margin and funding through 10-26 (variant A; estimates, the dry run is authoritative)
- OTC IM 1,405,615.50.
- Options -440,694.60.
- CDX initial payments +309,920.95.
- Net CTM VM +291,589.14 (TRS received +600,921.00; CDX posted -286,487.16, the IG part an estimate on implied proxies; IRS posted
  -22,844.70). The net is GS's collateral:
  restricted under D9 (291,589.14 at 10-26).
- Business-date funding (D8):
  - 10-21: IM and day-0 VM, 1,701,918.50, swept from Treasury, which held 25,000,000.00 that day.
  - 10-22: before the as-of sweep Treasury would close at about -234,337.70 (the owner's 10-22-dated transfers left 1,467,580.80); the
    as-of PB sweep of about 596,223.95 (CDX initial payments and free VM cash, net of the restricted 180,329.45 and the 10,000.00 buffer)
    brings it to about +361,886.25.
  - 10-23: the option premiums settle (net 440,694.60): US Exchange's own 3,859.38 plus a Treasury sweep of about 436,835.22.
- Fund cash goes from 4,958,770.57 to about 3,713,970.56 (internal sweeps net to zero).
- Interest restatement through night 10-26 (C17's segment rounding): Treasury about -848.07, PB credit about +4.38.
- Restated NAV (each with its split, per account and F1:ALL): 10-23 about 25,682,406; 10-26 about 26,011,264 when 10-26 is L (options
  at the Cboe official close), about 26,006,111 otherwise. Variant B would add about +37,395 (10-23) / +37,382 (10-26).
- The workbook's 25,770,700.86 is never forced. The bridge is printed by the dry run.

### Apply procedure (at the owner-approved 1.0.5 deploy; not by any builder)
1. Install 1.0.5 (section 12).
2. On the apply day, no later than 2026-11-19 and before 16:00 ET (steps 3-5 must finish before 16:00 ET): `python -m finsim stop`.
   The terminal stays stopped until step 6, so nothing moves between the dry run and the apply.
3. From the installed release: `cd C:\Users\logan\.finsim\releases\<1.0.5 tag>\intelligence-terminal\finsim`, then
   `py -3.12 -m finsim import --batch finsim\terminal\data\imports\IMP-F1-20261027-A.jsonl --home "%USERPROFILE%\.finsim"
   --fetch-data-pack`. It prints the pack path and sha256 (window 2026-10-21 .. the last business day before today).
4. `... --mode dry-run --pack <pack>` on a fresh copy; show the owner the report: every line, the business-date cash table, the funding
   moves and the restated NAV with its split and bridge.
5. On his typed go: `... --mode apply --pack <pack> --owner-go "<his words>"`. It writes the backup (to `<home>\backups\import\`) and
   its sha256 first, and refuses if the match_hash, the pack or the catch-up cutoff changed (then repeat step 4).
6. `python -m finsim open`. Check Completed Trades, Portfolio, Derivatives, History (restated rows and their split) and Cash &
   Collateral, and confirm integrity ok.

### Restore (an apply that must be undone)
1. `python -m finsim stop`. Confirm in tasklist that no FinSim `python` / `pythonw` process runs and that `terminal.lock` and
   `terminal.db.lock` are gone.
2. Move `terminal.db`, `terminal.db-wal` and `terminal.db-shm` into `<home>\backups\failed-<ts>\` (kept as evidence). A leftover WAL
   must never be replayed onto the restored file.
3. Copy `<home>\backups\import\terminal-pre-IMP-F1-20261027-A-<ts>.db` to `terminal.db` and verify its sha256 against the recorded
   one.
4. Run `PRAGMA integrity_check` (must be ok), start 1.0.5 and confirm the app's integrity check.
5. After an apply this backup, with the 1.0.5 release, is the only rollback (D2): 1.0.4 / 1.0.3.1 cannot read the imported rows, and
   returning to them needs the pre-1.0.5 install backup.

## 8. Owner questions (Q2, Q3 and Q5 answered by the owner on 2026-10-27 15:04 EDT; the build proceeds on the defaults of the others)

| # | Question | Default (built) |
|---|---|---|
| Q1 | IRS valuation basis and conventions for the 4.72 % pay-fixed swap | A: contract curve spread calibrated at booking (about -18.0 bp), mark0 = -2,073.36 cost of dealing, no day-one gain; fixed semi-annual ACT/360, compounded SOFR in arrears paid annually, effective 2026-10-21, maturity 2029-10-22. B would show a +35,247.08 day-one model gain. |
| Q2 | TRS terms | ANSWERED 2026-10-27 15:04 EDT ("Use Goldman terms"): the GS schedule - 12-month tenor (2027-10-21), monthly resets from 2026-11-23, financing receive SOFR - 0.60 % (not the workbook's SOFR - 1.00 %), CTM daily VM |
| Q3 | OTC initial margin levels | ANSWERED 15:04 EDT: the GS schedule in the app, TRS 10 %, CDX IG 2 % / HY 6 %, IRS DV01 x 50 bp = 1,405,615.50; not the workbook's 15 % / 5 % / 2 % (about 2,002,921) |
| Q4 | CDX basis calibration to the dealt spreads | Accept: the basis is derived from the 10-21 proxy so the deals are exactly 64 / 352 bp (10-27 view: IG -24.5 bp / HY +23.0 bp vs the FRED ICE BofA OAS proxy); marks follow the proxy and stay labelled MODEL |
| Q5 | The 5M T-bill reserve | ANSWERED 15:04 EDT: none. Batch line 34 is declined; cash stays in Treasury at SOFR - 0.50 % |
| Q6 | Equity margin model | Account-level Reg T on: 50 % initial / 30 % maintenance of short value beyond proceeds; fully paid longs count at a 50 % loan value; only a deficit is reserved; never forced liquidation; editable per account |
| Q7 | Collateral received (CSA VM, securities-lending cash) | Restricted, netted per CSA: 2330 - 1320 of the PB account (floored at 0) + 2520; excluded from available, buying power and the sweep; VM payments to the same counterparty may use it; it earns no PB credit interest and CSA interest on cash collateral is not simulated; a per-CSA "reuse allowed" flag, off |
| Q8 | `python -m finsim legacy` (the retired game with its briefings) | Remove the subcommand from FinSim1's CLI; delete no code and no data |
| Q9 | The two old Chrome-app "FinSim" shortcuts that cannot start the server | Keep them untouched; `status` / `install` report them; deleted only on the owner's OK |
| Q10 | Key map for the new navigation | F12 Dashboard, F8 Pending (account-filtered on an account page), Shift+F8 Completed, Shift+F4 Accounts, Shift+F5 Cash & Collateral (the account's cash on an account page), Shift+F6 Derivatives, Shift+F10 History; displaced actions stay bindable in Settings > Keyboard |
| Q11 | Settlement-failure simulation | Off: only the owner can trigger a simulated counterparty fail from the Completed Trades drawer; unfunded payables become FAILED -> RESOLVED automatically |
| Q12 | Applying the import to the live book | At the 1.0.5 deploy: data pack fetched that day, dry run on a fresh copy shown first, apply only after his typed go, backup first |
| Q13 | Apply window | No later than 2026-11-19, with the dry run and the apply before 16:00 ET. The input window runs 2026-10-21 .. L, the last business day before the apply; the restatement, re-accrual and business-date cash check run through the day before the apply (non-business days after L at L's marks); after 2026-11-19 the batch is regenerated, because the options' 2026-11-20 expiry would fall inside the catch-up |
| Q14 | Mark policy of the restated history | Dated official marks for every position (equity CLOSE, futures SETTLE, OTC VM mark, option Cboe official close where it exists else the Yahoo last trade, volume-0 = stale), with the split shown on History: original NAV, import effect by line, interest, mark-policy effect by position (10-23: +3,212.20; 10-26: -1,487.25). The alternative keeps the original 17:45 snapshot marks for the preserved positions |

## 9. Deferred (with reasons)

1. **UI-21 docs, and the KEYBINDS.md part of UI-13** (TERMINAL_DESIGN 17.1 / 17.5 / 17.9 / 19.21, MAP.md, TERMINAL_CHECKLIST,
   TERMINAL_STATUS, KEYBINDS.md). The `docs\` folder is outside the packages' file scope. P1 returns the text, and the orchestrator
   applies it at the 1.0.5 gate.
2. **The CHANGELOG entry of UI-14 and the docs parts of STARTUP-05** (root README line 59, TERMINAL_RUNBOOK.md). Outside the package
   scope (CHANGELOG, root README, docs). P7 returns the text; the orchestrator applies it at the gate.
3. **UI-18, the `site\reports` copy step in make_release.ps1.** The release script is the orchestrator's scratchpad tool. It is removed
   in the gate's tooling update (section 12, step 2); after P1, FinSim1 no longer reads `site\`.
4. **STARTUP-08, moving the release tooling into the repo.** Git and release operations belong to the orchestrator; builders run no git.
   The scripts stay in the scratchpad for this build, but the gate updates them (section 12, step 2: paths, deletions, release from the
   commit SHA, RELEASE.txt) and commits and releases from that SHA.
5. **STARTUP-10, the VERSION bump.** Builders never change VERSION. The gate sets 1.0.4 (finsim-104-complete) and 1.0.5 (this build).
6. **STARTUP-07, the owner-attended icon / login launch check.** It needs port 8765 and the live home. It runs with the owner at the
   1.0.5 deploy; P7 adds the logging and status reporting.
7. **STARTUP-06, deleting the two old Chrome-app shortcuts.** They are the owner's files: deleted only on his OK (Q9). P7 reports them.
8. **OMS-11, extended-hours execution.** The session model and fill rules are built on regular-session bars and delayed quotes; the
   directive asks for extended hours only "where supported". It is disclosed (ticket and Unsupported list); orders entered outside the
   session rest until the open.
9. **OMS-12, resting bond and FX limit / stop orders.**
   - Bonds are priced from a dealer model (curve / FedInvest), so a resting limit would execute on model drift, not on a market.
   - FX resting orders need an FX session group in the matcher, `fx.apply_fill` and FX desk entry; these are unowned FX files, after
     the blotters.
   - Both tickets stay immediate dealer trades and appear in Completed Trades with a ticket parent id.
10. **PORTFOLIO-09, releasing cash IM against pledged securities (collateral substitution).** No position uses it (Q5: no T-bills), and
    it needs a joint change of the OTC IM ledger (GL 1330, P4) and the CSA terms (P6). Pledges, haircuts, encumbrance and the
    requirement-vs-eligible view ship in P6.
11. **PORTFOLIO-18, routing US Exchange borrows through the GS PB.** It would change the lender of the owner's 5 existing borrows, which
    are preserved untouched. Custody attributes and the custody statement ship in P6.
12. **PORTFOLIO-20, deploying the fixed Portfolio page to the live app.** It ships with the 1.0.4 deploy (owner flag), outside this
    build. P3 adds the contract test.
13. **Tracking real CDX credit events and auctions** (directive section 9). There is no free source of ISDA Determinations Committee
    rulings or auction final prices, so the simulator does not detect real events. It supports them as an owner-triggered simulation
    instead (P4 task 11: a constituent default at a recovery the owner enters, with the protection payment, the accrued premium, the
    reduced notional and IM), disclosed by P4's own Unsupported row; marks stay MODEL from the OAS proxy.
14. **TEST-10, recording the post-port suite result.** Already done by PORT-HOTFIX at 12:25 EDT and VERIFIER r1 at 13:08 EDT (552 / 0 /
    0 / 0 on both interpreters). The prove_short regression test is in P6.
15. **Applying IMP-F1-20261027-A to `~/.finsim/terminal.db`.** It needs the 1.0.5 code deployed, the data pack of the apply day and the
    owner's typed go (Q12, Q13); builders never write `~/.finsim`.
16. **CSA interest on cash collateral.** A CSA pays interest on cash collateral at a contractual rate with its own accrual and payment
    dates; nothing in the GS schedule carries those terms yet. Until then the restricted collateral earns no PB credit interest (which
    keeps the fund from earning on the counterparty's cash), collateral posted earns none, and the Unsupported page says so (C6, Q7).
17. **Automatic PB financing draws as Completed Trades rows.** They are funding moves made by the sweep cascade, not trades the owner
    executed; they show in Cash & Collateral and the PB account's transactions (every financing TRADE - repo, loans, private credit,
    securities lending and borrowing - is in Completed Trades, C7).
18. **An as-of import whose catch-up crosses a lifecycle event of an imported position** (the options' 2026-11-20 expiry; the TRS reset
    2026-11-23). The as-of path does not replay expiry or exercise for past dates. The importer refuses after 2026-11-19 and the batch is
    regenerated with the events handled (Q13).

## 10. Coverage check (every gap id)

| Gap ids | Primary package | Other parts |
|---|---|---|
| UI-01, UI-03, UI-05, UI-06, UI-15, UI-16, UI-17, UI-20 | P1 | |
| UI-02 | P1 | `news.py` deleted by P1a |
| UI-04 | P1 | the `api_views.py` import switch made by P1a |
| UI-07, UI-08 | P1 (route, nav, key) | pages by P2 |
| UI-09, UI-10 | P1 (route, nav, key) | pages by P6 |
| UI-11 | P1 (route, nav, key) | page by P4 |
| UI-12 | P1 (route, nav, key) | History hub page by P3 |
| UI-13 | P1 | docs deferred (9.1) |
| UI-14 | P7 | CHANGELOG deferred (9.2) |
| UI-18 | deferred (9.3) | gate tooling update (section 12) |
| UI-19 | P7 (Q8) | |
| UI-21 | deferred (9.1) | |
| OMS-01, OMS-02, OMS-03, OMS-05, OMS-06, OMS-07, OMS-08, OMS-13, OMS-14 | P2 | |
| OMS-04 | P2 (listed as-of) | P4 (OTC as-of), P5 (orchestration) |
| OMS-09 | P6 | drawer actions by P2 |
| OMS-10 | P4 | |
| OMS-11 | P2 | extended-hours execution deferred (9.8) |
| OMS-12 | P2 | resting bond / FX deferred (9.9) |
| OMS-15 | P2 | C5 hook by P1 |
| OMS-16 | P1 | |
| OMS-17 | P2 | P6 settlement-fail tests, P5 import tests |
| PORTFOLIO-01 | P3 | P4 dated inputs, P6 as-of sweeps and `cash.reaccrue`, P2 as-of funding, P5 run |
| PORTFOLIO-02, PORTFOLIO-06, PORTFOLIO-08 | P4 | P3 consumer |
| PORTFOLIO-03, PORTFOLIO-05, PORTFOLIO-13, PORTFOLIO-14, PORTFOLIO-15, PORTFOLIO-17, PORTFOLIO-19 | P3 | |
| PORTFOLIO-04 | P3 | P6 C16 |
| PORTFOLIO-07 | P1 (Dashboard) | P3 returns |
| PORTFOLIO-09 | P6 | substitution deferred (9.10) |
| PORTFOLIO-10 | P6 | P3 C9b hook, P2 funding side of C21, P4 CSA payments |
| PORTFOLIO-11 | P6 | P3 C9b hook, Risk panel by P3 |
| PORTFOLIO-12 | P6 | P1 C6 wording, Activity type by P3 |
| PORTFOLIO-16 | P6 | |
| PORTFOLIO-18 | P6 | borrow routing deferred (9.11) |
| PORTFOLIO-20 | P3 | live deploy deferred (9.12) |
| STARTUP-01, STARTUP-02, STARTUP-04, STARTUP-09 | P7 | |
| STARTUP-03 | P1 | |
| STARTUP-05 | P7 | docs deferred (9.2) |
| STARTUP-06 | P7 | deletion deferred (9.7) |
| STARTUP-07 | P7 | attended check deferred (9.6) |
| STARTUP-08 | deferred (9.4) | gate tooling update (section 12) |
| STARTUP-10 | deferred (9.5) | |
| TEST-01 | P5 | |
| TEST-02 | P2 | |
| TEST-03 | P6 | |
| TEST-04, TEST-06 | P8 | |
| TEST-05, TEST-09 | P7 | |
| TEST-07 | P1 | |
| TEST-08 | P8 (map) | P7 runner |
| TEST-10 | P6 regression | record done (9.14) |

All 78 gap ids are covered. The 35 reconciliation lines sit in the batch (P5).

## 11. Directive section 20: where each delivery item comes from

| Delivery item | Source |
|---|---|
| Application status | P8 evidence pack, plus each package's log line (what was complete, what was repaired) |
| Code changes | The packages' results |
| Historical reconciliation | batch_final.jsonl (preserve / import / declined / unresolved per line) plus P5's dry-run report; after the deploy, the apply report |
| Pending-trades blotter | P2 and P8 walks |
| Completed-trades blotter | P2, P5 and P8 walks (historical as-of and new executions) |
| Portfolio | P3 and P8 |
| Accounting (cash, collateral, derivatives, NAV) | P3, P4, P6 and P8 |
| Testing | P8: both interpreters, the directive map, the fund reconcile |
| Deployment | The P7 command (`cd C:\Users\logan\intelligence-terminal; python -m finsim serve`, or `FinSim.cmd`) and the gate's commit SHA, verified from a clean extract of that commit |
| Remaining limitations | Section 9 and the Unsupported page |

## 12. Gate and deploy (orchestrator; owner flag)

This gate runs for each release:
- for 1.0.5 (section 15.8, which lists that gate's differences);
- for a 1.0.5.1 hotfix, only when a code defect postpones the import (section 15.8 step 8);
- for 1.0.6 (section 15.9).

Under the owner's standing pre-approval (2026-10-27 15:12 EDT), a release that passes it deploys, and the import is applied, without
asking (section 15.8).

1. **After P8.**
   - Append the builders' log lines to `docs\BUILD_LOG.md` in order (the orchestrator is its only writer).
   - VERSION 1.0.5 (1.0.6 at the second gate); CHANGELOG; apply the deferred doc texts (at the 1.0.6 gate; section 15.8 step 1).
   - Run the suite on py-3.12 and py-3.14 in the working tree.
2. **Update the commit and release tooling** (scratchpad `commit_terminal.sh`, `make_release.ps1`, `release_manifest.py`).
   - `commit_terminal.sh`: add to PATHS `finsim/__main__.py`, `FinSim.cmd`, `finsim/FinSim.cmd`, `finsim/README.md` and
     `finsim/tests/run_terminal_suite.py`. Stage with quoted pathspecs so deletions are recorded:
     `git add -A -- finsim/finsim/terminal finsim/finsim/__main__.py finsim/tests/fixtures/terminal 'finsim/tests/test_terminal_*.py'
     'finsim/tests/terminal_*.py' finsim/tests/run_terminal_suite.py finsim/__main__.py FinSim.cmd finsim/FinSim.cmd finsim/README.md
     finsim/docs finsim/CHANGELOG.md`. Today the bash globs expand only to files on disk, so the deleted `tests/test_terminal_news.py`
     (present on `claude/finsim-live-terminal`) would stay in the commit and fail against the deleted `news.py`.
   - README guard: before staging `finsim/README.md`, compare its FinSim2 block (P7's markers) with `git show HEAD:finsim/README.md` (the
     FinSim2 branch). If they differ, the FinSim2 session has uncommitted README work: stop and ask the owner.
   - `make_release.ps1`: build the release from the commit, not the working tree - `git archive <SHA> finsim/finsim finsim/pyproject.toml
     finsim/__main__.py finsim/FinSim.cmd finsim/README.md data/nyse-holidays.json`, extracted into the release layout; no `site\reports`
     step; RELEASE.txt records the SHA, branch `claude/finsim-live-terminal`, VERSION and time (today it records the checked-out FinSim2
     branch's HEAD).
   - `release_manifest.py`: hash the whole release `finsim` folder (the shim included), not only the package.
   - At the 1.0.5 gate the P7 files and the README rewrite do not exist yet: section 15.8 step 3 gives that gate's paths.
3. **Commit** on `claude/finsim-live-terminal` with the updated script (directive item 10); the owner pushes.
4. **Verify the commit, not the tree.** `git archive <SHA>` into a folder outside %TEMP% (Storage Sense), then run
   `tests\run_terminal_suite.py` there on py-3.12 and py-3.14: the counts must equal the working tree's.
5. **Release** from that SHA with the updated `make_release.ps1`; write the manifest.
6. **Deploy.** Under the owner's standing pre-approval, a release that passed steps 1-5 and meets his deploy conditions (section
   15.8 step 7) deploys:
   - run `install_release.ps1 -OwnerSaidDeploy`: it backs up terminal.db, -wal and -shm first, and rolls back automatically if the
     after-install check fails;
   - then report to the owner.
7. **Import** (at the 1.0.5 deploy), per section 7 as section 15.8 step 8 adjusts it:
   - stop; data pack; dry run;
   - check his five conditions with P8-lite's checker (section 15.6 item 3) and send him the report and its plain-English summary;
   - apply with backup; open.

   It runs no later than 2026-11-19 and before 16:00 ET.
8. Attended launcher check with the owner (9.6), at the 1.0.6 deploy.
9. Final report to the owner per section 20 after 1.0.6. After 1.0.5, a short report of the release and the import.

## 13. Review answers (independent review of revision 1: F01-F22)

Every finding was checked against the code and the 13:22 EDT read-only copy of the live database
(`scratchpad\checks\plan-rev-1323`, state_version 3200). All 22 hold; all 22 are fixed in this revision. None was rejected.

**F01 (critical) - the new GLs cannot post on the owner's database.**
- Verdict: accepted, fixed.
- Evidence: `db.py` calls `_seed` (the only chart insert) only when the schema version is 0, and `lending_accruals.ensure_chart_rows`
  inserts the lending codes only. The copy's chart has 52 rows and lacks 1420 / 2420 / 2520 / 4360 / 5350. On an in-memory clone with
  foreign keys on, a journal line on 4450 is accepted and one on 4451 is refused: "FOREIGN KEY constraint failed".
- Fix: C11 adds `ledger.ensure_chart_rows` (P4, called from `swaps.bind`), and P1's `Database.open` syncs every `ledger.CHART` row on
  every open (D2, P1 task 1). P1's `schema_1031.sql` fixture drives a P1 test (every code present, a line on each accepted) and a P4 test
  (a line on each new GL). P4's owner-DB acceptance now books a TRS, a CDX and an IRS with real current inputs through the RFQ flow and
  runs the next night's accruals onto 4451 / 4453 and the upfront onto 2345, instead of only showing the offline 409.

**F02 (major) - the import window is hard-coded to 10-21..10-26.**
- Verdict: accepted, fixed.
- Evidence: the copy has no ORCL / META CLOSE mark (89 CLOSE marks over 23 instruments). `MarksRecord._from_daily` returns "no_symbol"
  for options. `swaps_trs._vm_close` answers CAUGHT_UP (quiet, terminal) when the only price is of a later session, and SWAP_VM runs
  before MARKS_RECORD in the tick. MARKS_RECORD looks back only 10 days.
- Fix: the window is 2026-10-21 .. L, L = the last business day before the apply day. `--fetch-data-pack` runs on the apply day from the
  installed release folder (section 7 step 3); S0, S2, S2b and the catch-up dates derive from the pack; S0 refuses a date without its
  inputs (only L's unpublished FRED value may stay CLOSE_PENDING); the pack path and sha256 go into `import_batches`. The dry run pins
  the catch-up cutoff (accounting day + sha256 of the due lifecycle keys) and the apply refuses when it changed. The apply deadline is
  2026-11-19 (Q13), before the options' expiry. The frozen pack is no longer a repo file. Batch S0 / S2 / S2b / catch_up rewritten.

**F03 (major) - as-of funding creates a business-date overdraft.**
- Verdict: accepted, fixed.
- Evidence: on the copy F1:TREASURY's business-date cash is 25,000,000.00 at the close of 10-21 and 1,467,580.80 at the close of 10-22:
  the owner's transfers X-000004..X-000007 (19:39-19:43 ET on 10-21) are dated 10-22. 1,467,580.80 - 1,701,918.50 = -234,337.70.
  `execution.fund` measures current balances; L18 checks current balances only.
- Fix:
  - D7 states that as-of rows take business date = the owner's value date and override ACCT_DAY (the 19:45 ET booking would otherwise be
    10-22), and gives the real-time business dates of their lifecycle (VM on D, accrual of night D on D+1).
  - D8 / C20 (P2): as-of cash needs are funded on business-date balances at the close of D, the sweep dated D.
  - C15 (P6): `cash.asof_sweep` runs SWEEP_OUT per past business date; `cash.asof_transfer` is the labelled fallback.
  - P5 step C3 prints every account's business-date cash for every date and refuses an unresolved negative.
  - Estimate: the 10-22 as-of PB sweep of about 596,223.95 brings Treasury to about +361,886.25; no internal loan is needed.
- Why not "date the cash legs 10-22": the contract, its IM and its day-0 VM would then sit on different business dates, and the owner's
  instruction is value date 10-21.

**F04 (major) - restricted collateral is gross and the VM path ignores it.**
- Verdict: accepted, fixed.
- Evidence: SWAP_VM pays through `execution.fund(..., immediate=True, overdraft=True)` (`swaps.py` line 1211), and `own_funds_c` with
  `immediate=True` returns the raw balance of 1010. The C9b adjusters change only available, spendable, immediate available and buying
  power.
- Fix: D9 / C21. Restricted = max(0, 2330 - 1320) of the PB account (one CSA per PB relationship) + 2520. P6 defines
  `collateral.restricted_c` and registers it as the adjuster; P2 implements `own_funds_c` / `fund` with `csa_netting`; P4's VM, payment,
  coupon and termination calls pass `csa_netting=True`, so paying GS may use GS's collateral and nothing else may. P6 acceptance (b) now
  expects the net 291,589.14 (600,921.00 received - 309,331.86 posted). CSA interest is not simulated and restricted cash earns no PB
  credit interest (C6 row, Q7, deferred 9.16).

**F05 (major) - the owner's dealt terms are not enforced.**
- Verdict: accepted, fixed.
- Evidence: revision 1's line 24 kept 129.13 only as `expected_reset_px`; C10 took the reset from the recorded close and the CDX spread
  from proxy + basis; S4 hard-coded -24.5 / +23.0 from one reconciliation reading; BAMLC0A0CM has no rows in the DB.
- Fix: D10 and C10. The payloads carry `reset_px` 129.13 / 697.52, `dealt_spread_bp` 64 / 352 and `fixed_rate` 0.04720, booked exactly;
  409 ASOF_TERMS_MISMATCH when the stored contract would differ, 409 ASOF_BASIS_NOT_CALIBRATED when the account basis would not deal the
  spread. S4 derives the basis from the pack's own 10-21 observation (basis = dealt - half-spread - proxy). Closes and proxies are
  evidence and valuation inputs; a TRS close that differs from the reset gives a day-0 VM instead of a different contract. P4 tests both
  refusals and the 129.12-close case (+387.00).

**F06 (major) - cancelled and expired orders become unreachable; no Submit.**
- Verdict: accepted, fixed.
- Evidence: on the copy O-000008 (CANCELLED, "PG 6,400 BUY -> replaced by 4,790") and O-000018 (CANCELLED, "STT calls (owner: not
  intended)"); revision 1's Pending listed open stages only and P2 task 10 replaced the account history.
- Fix: C7 / P2 task 2 add the status filter Open / Filled / Cancelled / Expired / Rejected / All (the order history) and a "New order"
  button; the timeline maps the stored preview to Created and the first event to Submitted; P2 task 11 keeps the account Orders page's
  history section. P2 acceptance 3 lists O-000008 and O-000018 under Cancelled with their reasons and timelines.

**F07 (major) - the commit and release tooling would not capture this build.**
- Verdict: accepted, fixed.
- Evidence: `commit_terminal.sh` PATHS miss the outer shim, both FinSim.cmd files, `finsim/README.md` and
  `tests/run_terminal_suite.py`; its bash globs expand to files on disk only, while `read-tree` of the parent keeps deleted files
  (`git ls-tree claude/finsim-live-terminal` shows `finsim/tests/test_terminal_news.py`). `make_release.ps1` copies the working tree and
  `site\reports` and records the checked-out branch's HEAD; `release_manifest.py` hashes only the package.
- Fix: section 12 step 2 updates the tooling (paths, `git add -A` with quoted pathspecs, the README guard, a release built by `git
  archive` of the SHA without `site\reports`, RELEASE.txt with the SHA, a manifest of the whole release folder); step 4 verifies the
  commit from a clean extract on both interpreters. STARTUP-08 / UI-18 deferral texts updated (9.3, 9.4).

**F08 (major) - unsafe restore and rollback.**
- Verdict: accepted, fixed.
- Fix: section 7 "Restore": stop, confirm no process and no lock, move the db, -wal and -shm aside, copy the backup in, verify the
  sha256, integrity_check, start 1.0.5. The importer ends with `PRAGMA wal_checkpoint(TRUNCATE)` (P5 task 6, tested). D2 now says that
  after the import the only rollback is that backup with the 1.0.5 release.

**F09 (minor) - `cash.reaccrue` covers Treasury only.**
- Verdict: accepted, fixed.
- Evidence: `pbcredit:F1:PB_GS:2026-10-22..26` are DONE with "0.00 on 0.00"; accrual_state shows pbcredit accrued through 10-26.
- Fix: C17 covers `cashint`, `pbcredit` and `pb` on every account whose business-date balance changed, keeping each night's original
  rate. While checking this I found revision 1's correction date off by one: nightly accruals post on night + 1 (on the copy
  `accr:cashint:F1:USD:2026-10-21` is dated 2026-10-22), so corrections now carry night + 1. P6 test (e) adds the PB credit case (+0.87).
  The decision on CSA collateral and PB credit interest is D9 / Q7.

**F10 (minor) - the RFQ dealer depended on P2's matcher.**
- Verdict: accepted, fixed.
- Fix: C14. The dealer is P4's own `RFQ_DEALER` lifecycle product job, registered through the existing `lifecycle_eod.add_product_job`
  hook; it quotes and expires on every scheduler tick. P4 test 3 runs with the matcher never started; acceptance item 5 now uses the RFQ
  flow.

**F11 (minor) - UI promised by P6 in other packages' files.**
- Verdict: accepted, fixed.
- Fix: P2 task 4 owns the fail / resolve drawer actions (over C15); P3 task 13 owns the Activity margin-calls type and the Risk Reg T
  panel (over C16, which gains `margin_calls_rows`). P6 tests the commands and data and walks its own pages; P8 walks the rest.

**F12 (minor) - the restatement re-marks preserved positions without saying so.**
- Verdict: accepted, fixed.
- Evidence: on the copy, dated closes / settles minus the 17:45 snapshot marks of the preserved positions are +8,042.63 (10-22, a late
  snapshot), +3,212.20 (10-23), +4,855.95 (10-24, 10-25) and -1,487.25 (10-26); for the five options on 10-26 the Cboe official close vs
  the Yahoo last trade is +5,152.50 net.
- Fix: C9d stores and shows, for every date, the original NAV, the import effect per line, the interest effect and the mark-policy
  effect per position (plus a dating line for a late snapshot), with residual 0; the dry run prints it and History shows it. Option
  marks use the Cboe official close where it exists (the seam date L, fetched on the apply day), else the Yahoo last trade, stale on a
  volume-0 day. Q14 records the policy and its alternative.

**F13 (minor) - M4 reads the original snapshots; business date of positions unspecified.**
- Verdict: accepted, fixed.
- Evidence: `accounting.py` M4 (about line 1642) selects from `nav_snapshots` directly.
- Fix: C9i moves M4 / M5 and every snapshot reader to the latest-version chain; C9d takes positions and cash from the entries'
  business dates (GCZ26, filled 19:58 ET on 10-21, counts from 10-22). P3 tests (g) and (h).

**F14 (minor) - financing trades missing from Completed Trades.**
- Verdict: accepted, fixed.
- Fix: C7's union adds repo / reverse repo, cash loans, private credit, securities lending and stock borrows (asset class Financing,
  parent `T-<command id>`). Automatic PB financing draws are funding, not trades: deferred with its reason (9.17).

**F15 (minor) - labels and on-screen text.**
- Verdict: accepted, fixed.
- Evidence: `index.html` line 9 says "... fictional capital, no simulation."
- Fix: D1 labels both say "simulated" ("As-of booking - simulated fill at the 2026-10-22 closing print, entered ..." and "As-of booking
  - simulated OTC trade with Goldman Sachs, value date 2026-10-21, entered ..."), in the plan and in the batch's `source_label`s. The
  toast read "Daily News was removed - showing the Dashboard" (revision 3 drops the feature name: "That page was removed - showing the
  Dashboard", R2B-18). P1 rewrites the meta description ("simulated executions").

**F16 (minor) - P1's text search could not pass; fixture-server leftovers.**
- Verdict: accepted, fixed.
- Evidence: `risk.js` line 81 "not in the headline gross"; the private-company master's sources include `anthropic.com/news/...`;
  `terminal_fixture_server.py` line 59 lists `pages/news.js` and line 165 routes `/api/news`.
- Fix: P1 acceptance searches for the news feature's own strings with an allowlist; P1 task 2 removes the fixture route and the
  OPTIONAL_STATIC entry, and a static test asserts it.

**F17 (minor) - README boundary off by one.**
- Verdict: accepted, fixed.
- Evidence: line 291 is "`finsim/quant/catalog.py` holds each equation's LaTeX, a note and its implementation."; line 293 is "### As a
  local app"; the README has FinSim2 commits (e94f990, 39967a1, 4f3aa92).
- Fix: P7 protects the FinSim2 block by its content markers (today lines 58-292) with a hash, edits lines 1-57 and 293-799, and the gate
  commits README.md only through the FinSim1 path after comparing the block with the FinSim2 branch's committed copy (section 12 step 2).

**F18 (minor) - a .db test fixture would be git-ignored.**
- Verdict: accepted, fixed.
- Evidence: `finsim\.gitignore` lists `*.db`.
- Fix: P1 owns `tests\fixtures\terminal\schema_1031.sql` (SQL text); P1, P4 and P7 build the database at test time.

**F19 (minor) - batch inconsistencies.**
- Verdict: accepted, fixed in batch revision 2.
- Evidence: the copy holds BAMLH0A0HYM2 and SOFR through 2026-10-26, written by the live worker's rates.record commands 3192 / 3194 (their
  started_ts reads 12:22 EDT; the finding said 12:42); `rates._write_points` and `h_marks_record` are INSERT OR REPLACE.
- Fix: the batch's contract references now match this plan (C9d, C10, C11, C12, C15, C17, C20) and the idem is
  `reaccrue:<batch>:<account>:<kind>:<night>`; the SWEEP_OUT text says
  restricted collateral is never swept; S2 values are structured (`price`, `quality`, `volume`, `note`); the futures dates are ISO with a
  trade-date diff and its CME note; one canonical key format (account ids; product = the master kind, so DLR is REIT and IBIT / PDBC are
  ETF; prices never in the key); `import.record_inputs` inserts only absent values and reports differences; P5's acceptance line now reads
  "TRS alone +600,921.00; all five contracts +291,589.14".

**F20 (minor) - package and payloads under-specified.**
- Verdict: accepted, fixed.
- Fix: C8 defines both payloads exactly; the batch has one `package` record carrying the single command (lines 32 / 33 only reference
  its key, so no second command reuses the request id); `fee_rule` is an enum (`SCHEDULE`, `NONE`); prose and evidence left the payloads
  and terms; the option instruments are created inside the booking command from `instrument_spec` (S3 is now a read-only check).

**F21 (minor) - no Source column; the historical-model path untested.**
- Verdict: accepted, fixed.
- Fix: `import_lines` gains `source_json` and `source_label` (batch S1, P5); the mini batch has 8 lines (9 in revision 3, with a
  declined line) including a `historical_model` true line whose Completed Trades label is "Historical model (not an independent
  execution)" and never the as-of label (P5 test, P2 test k).

**F22 (minor) - parallel builders appending to BUILD_LOG.**
- Verdict: accepted, fixed.
- Fix: rule 8. Builders return their log line in the structured result; the orchestrator is the only writer (section 5 machine notes,
  section 12 step 1).

## 14. Re-review answers (revision 3: R2A-01..R2A-13, R2B-01..R2B-22)

Two reviewers checked revision 2. Every finding was verified against the plan, the batch, the code and the 14:54 EDT read-only copy of
the live database (`scratchpad\checks\plan-rev3-145422`, state_version 3208). All 35 hold and all 35 are fixed in the text the builders
read; none was rejected. One line per finding: verdict and evidence, then where the fix lives.

- **R2A-01** (major) - holds: `nav_snapshots` keeps a row per account and each account's TODAY baseline is `_last_snapshot(conn,
  account, today)`; the 10-22 PB as-of sweep (596,223.95) belongs to no line; F1:PB_GS has no 10-21 row (opened 19:44:13 ET, after the
  17:45:31 snapshot). Fixed: D3; C9d (every account row and F1:ALL restated, a `funding` group for the C2 sweeps and C3 transfers,
  original 0 where no row exists, residual 0 per account); P3 task 2 and test (a); P5 task 5 (C5) and tests; batch catch_up C5.
- **R2A-02** (major) - holds: `RateStore._obs` is filled once by `load_persisted` in `build_runtime` (`server.py` about line 619);
  `rates.record` / `_write_points` write `curve_points` only; the 14:54 copy has no BAMLC0A0CM row. Fixed: P5 task 4 reloads
  `services.rates` and `services.curve` after S2 / S2b commit; C12 `for_date` reads `curve_points`; the C10 note; a P5 test on a database
  without BAMLC0A0CM; batch S2b `after`.
- **R2A-03** (major) - holds: snapshots and Treasury interest exist for Saturday 10-24 and Sunday 10-25 (entries 698, 711) while the
  catch-up stopped at L; a 17:45 snapshot taken inside C1 would precede C2-C4. Fixed: the inputs stay 2026-10-21 .. L; C3 runs through
  the day before the apply plus current balances; C4 through `accrued_through` (C17); C5 through the last snapshot before the apply day
  at L's marks; C1 holds SNAPSHOT back and C5b takes missed dates late; S0 refuses at or after 16:00 ET or with an apply-day snapshot; a
  Monday test (D8, Q13, P5 tasks 3, 5 and 10; batch header window, S0, catch_up).
- **R2A-04** (major) - holds: the 14:54 copy has 315 fills plus 5 open borrows (`command_id` NOT NULL) and no other financing rows.
  Fixed: P2 acceptance 3 expects 320 rows (315 with Financing filtered out).
- **R2A-05** (minor) - holds: the IG marks of 10-22..10-26 rest on implied proxies (0.89 / 0.91 / 0.90 %); IG night 10-22 = 2,666.67 -
  2,583.33 = 83.34; PB credit under segment rounding is 0.87 / 0.88 / 0.87 / 0.88 / 0.88. Fixed: P5 acceptance 2 marks the IG-dependent
  figures as estimates, checked against `value_c` on the pack's values (CS01 1,366.84 per bp); 3,225.55 / +463,928.82 / 25,682,406.37;
  C17 states the segment rule; interest now Treasury -848.07 and PB credit +4.38 through night 10-26 (recomputed with that rule; the
  finding's -848.05 differs by 0.02, and the dry run decides); batch line 26 and summary.
- **R2A-06** (minor) - holds: the `transfers.kind` CHECK lists six kinds without ASOF_FUNDING, and D2 forbids widening. Fixed: C15
  (kind TRANSFER, `linked_type` "asof_funding", `linked_id` the line key, journal entry kind ASOF_FUNDING - `journal_entries.kind` has
  no CHECK); P6 task 9 and acceptance (i); batch catch_up C3.
- **R2A-07** (minor) - holds: the header observation said HY / SOFR run to 10-23; Q4 quoted constants; line 34 had status
  missing_import, product TBILL (the master's kind is BOND) and "bills cannot be pledged"; lines 22-23 key on the CME date; the database
  holds no ORCL / META close. Fixed in batch revision 3 (the observation; Q4; line 34 product BOND with its key recomputed, status
  `declined`, the pledge wording; `key_trade_date` on lines 22-23; the canonical rule for futures), P5 tasks 1-2, section 7 rows 24-25.
- **R2A-08** (minor) - holds: `IntegrityFull.due` adds a `%Y-%m-%dT%H` key every hour after :05, and `now_ts` never repeats. Fixed: the
  cutoff is {accounting_day, the due-keys sha256 of every job except INTEGRITY_FULL and HOUSEKEEPING, the source state_version, the max
  command id}, taken before the first write in both runs; `now_ts` is information only (P5 tasks 5-6 and tests, batch S0 / catch_up).
- **R2A-09** (minor) - holds: NAV_ASSET / LIAB_GLS exclude the position cost GLs and `_bs_totals` adds the swap MV, so 2345 in the
  liability set double counts 48,492.57. Fixed: C9c and C11 (`ledger.OTC_COST_GLS`, outside the NAV sets and outside COST_LONG / SHORT
  _GLS, which L9 ties to open lots), P3 task 8, P4 task 7.
- **R2A-10** (minor) - holds: `swaps_cds.book` seeds the premium leg from the IMM accrual start with cum = rebate and `accrued_through` =
  the trade date. Fixed: C10, P4 task 3 and acceptance 1, batch catch_up C1.
- **R2A-11** (minor) - holds: `swaps_cds.h_terms` raises 400 NOTHING_TO_UPDATE when nothing differs, and S4 had no request id. Fixed:
  S4 reads the stored basis and skips, NOTHING_TO_UPDATE counts as done, deterministic `impset:` ids (P5 tasks 1 and 5, the apply-twice
  test, batch S4).
- **R2A-12** (minor) - holds: `restricted_c` had no date, and US Exchange held +509,392.94 of pre-import excess at the close of 10-22
  (1010 20,259,070.33, 2100 -19,739,677.39). Fixed: C21 `as_of`; C15 sweeps at most the import's own cash; C20 uses restricted as of D;
  P6 task 9 and acceptance (h); a P5 test; batch catch_up C2.
- **R2A-13** (minor) - holds: `data\nyse-holidays.json` has no 2026-11-11, C12 already looks back 3 business days, and FRED keeps
  bond-holiday OAS (BAMLH0A0HYM2 2026-10-12 = 3.11) while SOFR skips it. Fixed: S0 tests the inputs with the jobs' own rules (P5 task 3
  and a test, batch S0).
- **R2B-01** (major) - holds: `fund` -> `treasury.sweep_in` -> `sweepable_c` (the current balance) raises INSUFFICIENT_FUNDS, and P2
  cannot edit `treasury.py`. Fixed: C15 / C20 (`sweep_in` checks the business-date figure under `ctx.asof_date`; the P2 -> P6 dependency
  in section 5). The proposed test cannot pass as written: L18 still forbids a negative current Treasury at commit, so an as-of need above
  the current Treasury is refused 409 ASOF_CURRENT_FUNDS with the amount to move first (D8); P2 acceptance (i) now has three cases and P6
  has test (j).
- **R2B-02** (major) - holds (the same root as R2A-03); fixed by the same changes.
- **R2B-03** (major) - holds, and wider than stated: `test_terminal_news.py` imports `finsim.terminal.news` and `config.NEWS_*` at module
  level, `test_news_route_with_a_stub_store` imports NewsStore, D12 points at the news tests, and `news.py` itself imports
  `config.NEWS_*` while `server.py` guards its import against ModuleNotFoundError only. Fixed: P1a holds all of C3 (both import switches,
  `news.py` deleted and now P1's) with the test moves and deletions and D12 (P1 split, C3, rule 1, P3 task 1).
- **R2B-04** (major) - holds: only PLANNED_STATIC files get a 200 stub (`api.py` about line 565), and `/api/dashboard` has no route
  without `dashboard.py`. Fixed: P1a lists `pages/dashboard.js` as optional (data-optional, PLANNED_STATIC, OPTIONAL_STATIC), so
  `#/dashboard` shows the not-built panel with no API call; P1b makes it required (C1, P1 split).
- **R2B-05** (major) - holds: P7 acceptance 1 asserts P1 task 6's wording (`server.py` about line 807 still prints "another FinSim
  Terminal ..."). Fixed: task 6 is in P1a.
- **R2B-06** (minor) - holds. Fixed: P1a captures and compares the attention and NAV goldens on one copy; P1b re-checks against its own
  pre-edit capture and walks P1's own pages; the 300 ms budget covers the Dashboard's own work with each provider's time reported, and P8
  measures the whole answer (P1 split, task 4, acceptance 2, P8 task 5).
- **R2B-07** (minor) - holds. Fixed: section 5's pool order P1b, P2, P3, P4, then P6 at the first free slot and P7 next; P1b's
  shared-file edits atomic with the suite green after each; wave-2 requests to P1's files merged after P1b; every builder gets the
  P1a / P1b definition.
- **R2B-08** (minor) - holds (the same as R2A-06); fixed there, and P5's report and test read that representation.
- **R2B-09** (minor) - holds: the two probes named functions no contract defined. Fixed: C7 `orders.gtd_supported(services)` (P2 task
  7), C16 `collateral.pledges_view(services, scope)` (P6 task 6), C6 names both.
- **R2B-10** (minor) - holds. Fixed: rule 2's two hook tiers; section 5's dependency list completed (P2 on C11 and C15, P6 on the C8 /
  C10 `booking` columns and C20, P4 on C15's settlement columns, P3 test (a)'s fallback, P3 acceptance (d) after P4 task 7).
- **R2B-11** (minor) - holds (the same as R2A-04); fixed there (320 rows).
- **R2B-12** (minor) - holds: the match_hash misses marks, curve_points, cdx_terms, accrual_state and jobs_done, and `now_ts` never
  repeats. Fixed: the cutoff adds the source `meta.state_version` and `MAX(commands.id)` (any command - a server started between the dry
  run and the apply - moves them) and compares only its four stable fields, taken before the first write (P5 tasks 2, 5 and 6, batch
  catch_up cutoff).
- **R2B-13** (minor) - holds: `Database.open` backs up, migrates (writes `meta`), widens the OTC kinds and compacts. Fixed: report mode
  opens a sqlite3 `mode=ro` URI; `--home` is required in every mode and section 7 passes it; the tests assert that nothing under
  `%USERPROFILE%\.finsim` changes (P5 tasks 2, 8 and 10, batch modes).
- **R2B-14** (minor) - holds. Fixed: deterministic `impset:` / `impcu:` request ids (the bus replays a same-id, same-payload command),
  NOTHING_TO_UPDATE counted as done, `nav.restate` idempotent per (batch, date) (C9d, P5 task 1, batch S4 / catch_up), all in the
  apply-twice test.
- **R2B-15** (minor) - holds: the borrows store "street lender (modelled)" (`lending.MODELLED_LENDER`), and S0 / S2 said "a modeled
  mark". Fixed: C16 `lending.lender_label` shows "(assumed terms)" with the stored rows untouched (P2, P3 and P6 use it; new borrows
  store the new wording); batch lines 16-20; "model mark (no trade that day)" in S0, S2 and P5 task 4.
- **R2B-16** (minor) - holds: older code reads `CHART[gl].kind` for every `gl_balances` row (`accounting.py` about line 1064), and
  `Database.backup` rotates every `terminal-*.db` in `<home>\backups`. Fixed: D2 reworded; the pre-import backup goes to
  `<home>\backups\import\` (P5 task 6, section 7 Restore, batch modes).
- **R2B-17** (minor) - holds: directive section 9 lists credit events among the CDS functions to support. Fixed: P4 task 11 adds an
  owner-triggered `cdx.credit_event` with a hand-worked test (HY, one name, recovery 40 %) and rewords its own Unsupported row; section 9
  item 13 now defers only tracking real events.
- **R2B-18** (minor) - holds. Fixed: the toast reads "That page was removed - showing the Dashboard" (C1, P1 acceptance 3).
- **R2B-19** (minor) - holds (R2A-07's first point): the observation is corrected in batch revision 3, re-hashed in the header and
  section 7; P5 copies the new file.
- **R2B-20** (minor) - holds. Fixed: C2 `T.hasModule(name)`, rule 5, C19, P2 tasks 3-4 and P3 task 13 gate the optional-module routes;
  no request goes out before the module is bound.
- **R2B-21** (minor) - holds. Fixed: C14 names `otc.rfq.request` / `otc.rfq.accept` / `otc.rfq.decline`; P2 task 8 records their
  refusals.
- **R2B-22** (minor) - holds. Fixed: P7 acceptance 6 asserts that P7's own written files contain no `finsim\finsim2` path; the README
  FinSim2-block hash check stays.

Also in revision 3 (not a finding): the owner's decision of 2026-10-27 15:04 EDT ("Use Goldman terms") answers Q2, Q3 and Q5 (section 8,
batch header `owner_answers`); batch line 34 is declined (section 7, P5 acceptance 2).

## 15. Two-phase delivery (1.0.5 positions, 1.0.6 directive completion)

The orchestrator decided this at 15:40 EDT on 2026-10-27, and it was written into the plan from 16:16 EDT. An independent critic then
checked this section and raised 16 findings (S-01..S-16: 4 major, 12 minor). Each was verified against the plan, the batch and the
code, and this section was revised from 17:18 EDT; 15.12 answers each one.

Revision 3 needs 18-22 h of wall clock before a single gate. The importer refuses at or after 16:00 ET (S0), so one release landing
after 16:00 ET on 2026-10-28 would move the owner's positions to Thursday.

His priority is to have these in his portfolio as soon as possible ("i need them now"; "Do not just model them, actually have them in my
portfolio"):
- his Goldman TRS ORCL / META, CDX IG / HY and USD SOFR IRS, booked as of 2026-10-21 with their lifecycle caught up;
- his five options as backdated listed fills.

The rest of his directive follows right after. The build therefore ships twice from the same working tree, strictly in sequence:

| Release | Phase | Holds |
|---|---|---|
| 1.0.5 | 1 | P1a with the interim items of 15.7; Part A of P2, P3, P4 and P6 (15.3); P5's phase-1 scope (15.5); P8-lite (15.6); fixes; gate, deploy and the import (15.8) |
| 1.0.6 | 2 | P1b; Part B of P2, P3, P4 and P6; P7; the rest of P5; the full P8; fixes; gate and deploy (15.9) |

### 15.1 Rules

1. **Precedence.**
   - This section decides the phase of every item. The package sections are not rewritten: a builder builds the tasks and acceptance
     items of its package that this section gives to its phase, by number, and leaves the rest.
   - Where any other section (0, 1, 5, 7, 8, 9 and 12 included), a "Depends on" line, an acceptance item or the batch header
     disagrees with this section on order, dependency, timing or approval, this section wins. The statements it supersedes outside
     the package sections (critic S-16):

     | Where | Old statement | Now |
     |---|---|---|
     | Section 1, the live app | 1.0.3.1 runs; the Portfolio fix "ships with the 1.0.4 deploy" | 1.0.3.2, with the Portfolio fix, went live at 15:19 EDT, and 1.0.4 at 16:54 EDT (pid 9448; BUILD_LOG 16:55) |
     | 9.1 | the doc texts are applied "at the 1.0.5 gate" | at the 1.0.6 gate, except P1a's KEYBINDS line for F12 (15.8 step 1) |
     | 9.5 | the gate sets "1.0.5 (this build)" | this build ships 1.0.5, then 1.0.6, and 1.0.5.1 only if a code defect postpones the import (15.8 step 8) |
     | 9.6 | the attended launcher check runs "at the 1.0.5 deploy" | at the 1.0.6 deploy, because P7 is phase 2 (section 12 step 8) |
     | 9.12 | PORTFOLIO-20 "ships with the 1.0.4 deploy" | the live fix shipped in 1.0.3.2 at 15:19 EDT; P3 adds the contract test in 1.0.5 (15.10) |
     | 9.15, Q12, section 7 step 5 | the apply needs "the owner's typed go" | his standing pre-approval (2026-10-27 15:12 EDT), on the conditions of 15.8 step 8 |
     | Batch header `status` and `modes.apply` | "Apply needs FinSim1 1.0.5 (... packages P1a / P1b - P7) ... and the owner's typed go" | written before the two-phase split and the standing pre-approval; the file stays byte-identical, because its sha256 is pinned, and the owner's summary says so (15.6 item 3) |
2. **What builders read.** Every builder in either phase gets this whole section with its package section and sections 2-5, 13 and 14
   (section 5, machine notes).
3. **Strict sequence.**
   - No phase-2 edit touches the tree before the 1.0.5 gate has committed it and verified that commit from its archive (15.8 steps 4-5).
   - The dry run and the apply run the owner's database in a maintenance runtime on a 7.6 GB machine, so they never run beside four
     builders. Phase 2's pool launches after the import has been applied, or postponed for a data or clock reason. A postponement
     caused by a code defect holds phase 2 until a 1.0.5.1 hotfix has passed its gate and the import has run (15.8 step 8).
   - A postponed import runs while at most two other agents are working; the orchestrator plans phase-2 launches to keep that window
     (15.8 step 8).
4. **The import wins.** A Part A builder who finds that an import step (15.4) needs a Part B item builds it in its own files and says so
   in its notes. It never leaves an import step to phase 2.
5. **1.0.5 never claims more than it does.**
   - A function named by a C6 probe exists only in the release that ships its feature (15.3, E1).
   - No Part B item lands as a stub that a 1.0.5 page or the Unsupported page would read as built.
6. **Section 3 applies unchanged:**
   - ownership, hooks first, both suites green, hand-worked money tests;
   - honest labels and the UI rules;
   - never the live app, `%USERPROFILE%\.finsim`, ports 8765 / 8865, git or VERSION.
7. **Every real-batch dry run runs between 00:00 and 16:00 ET** (critic S-12).
   - S0 refuses at or after 16:00 ET, and when the copy holds an F1 snapshot dated on the run's day (P5 task 3); every dry run starts
     with S0 (P5 task 5). A copy taken before 17:45 ET of the run's day, or on an earlier day, holds no such snapshot, so the clock is
     the rule that binds.
   - It covers every dry run of IMP-F1-20261027-A: P5's acceptance 2 and its phase-2 remainder, P8-lite item 3 and its re-run in each
     fix round, P8 task 5, a correction's (15.9 item 8) and the apply day's. Report mode is read-only, has no clock rule and runs at
     any time.
   - Work that is ready between 16:00 and 00:00 ET does everything else first (suites, walks, fixes of other findings) and runs its
     dry run after 00:00 ET.
   - S0's rule is not relaxed for dry runs: after 16:00 the catch-up would run the day's closing jobs without their inputs in the
     pack, so the dry run would no longer predict the apply.
   - Option closes: the pack takes L's official option close from the Cboe chain's previous close (P5 task 3), which is certainly L's
     only while the chain quotes the session after L. The apply day's pack is therefore fetched after 09:45 ET. A pack fetched earlier,
     as a night run's is, records the chain's own timestamp with each close, and P5 uses L's Yahoo last trade, labelled, when it cannot
     tie the chain's close to L; that run's option figures are estimates.

### 15.2 Phase 1 (release 1.0.5), step by step

1. **Precondition.** Section 5 step 1 holds: finsim-104-complete, now its remainder wf_fe749d86-2ef, has finished with both suites
   green. Done: 1.0.4 was gated, committed (6222093) and deployed at 16:54 EDT (BUILD_LOG 16:55).
   - **Durable copies** (critic S-04). Batch revision 3 and its build script lived only under %TEMP%, where Storage Sense deleted
     release staging on this machine on 10-09. At 16:56 EDT the orchestrator copied the batch byte for byte to
     `finsim\docs\imports\IMP-F1-20261027-A.jsonl` (committed with `finsim/docs` at the gate) and mirrored the scratchpad, the batch
     and `build3\rev3_batch.py` included, to `C:\Users\logan\finsim-orchestrator\scratchpad-mirror\`.
   - At 17:18 EDT both batch copies had sha256 `75690c19327713c6648d20b710495f5ef0b5a924ed263748b3f74aa51bd11d2f` (121,468 bytes),
     and the mirrored `rev3_batch.py` had sha256 `23c4743df5947127da2acb64bb7539645ab892c7f001a5b6a3016b8ccf5ee43b`, each equal to
     the scratchpad file's.
   - Before P1a starts, the orchestrator checks these hashes again. P5 task 9 copies the batch from
     `docs\imports\IMP-F1-20261027-A.jsonl` (from the mirror if that is missing) and verifies its sha256 against this plan's. A
     scratchpad file that vanishes is restored from the mirror.
2. **P1a alone,** as the P1 section's split defines it, plus the interim items of 15.7.
3. **Part A pool.** P2, P3, P4 and P6 start together on P1a's result (4 builders). These hook tiers replace rule 2's lists for phase 1.

   | Package | Tier 1 (first ~45 min) |
   |---|---|
   | P2 | the path and answer shape of `GET /api/blotter/executions` (15.3 E3); `blotters.source_label` (15.7 item 7); C8's columns; the C20 / C21 signatures |
   | P3 | C9 (a), (b), (c), (g), (h) |
   | P4 | C10's columns; C11 with `ledger.OTC_COST_GLS` and `ensure_chart_rows`; C12; a C13 stub that returns None, so C9c keeps today's overlay |
   | P6 | C15's columns; Treasury stamping and `sweep_in` under `ctx.asof_date`; `lending.lender_label`, C16's only Part A function (15.3 E4); C21 |

   - Tier 2:
     - C8's bookings; C9 (d) and (i); C10's booking;
     - C13 for real (P4 tasks 5-6) and P4 task 7 (P3's test (d) waits for it);
     - C15's as-of dating, sweeps and transfers; settlement fail / resolve; C17; C20.
   - Section 5's in-wave dependencies hold, minus the Part B hooks: C14, `orders.gtd_supported`, C7's pending and execution-detail
     routes with `blotters.counts` and the stage vocabulary, C9e, C9f, and C16 apart from `lender_label`.
   - **Order inside each Part A package** (critic S-15): its import items first (its rows of 15.4), then its 1.0.5 display items
     (15.7), then the rest of its Part A.
     - P4 builds tasks 5-6 (C13 for real: intraday marks and display fields) last. If P4 is still working 4.5 h into the pool, the
       orchestrator may move them to Part B: C13 stays the stub, C9c keeps today's overlay (labelled "as of the last VM"), and
       PORTFOLIO-02 moves to 1.0.6 (15.10).
     - A moved item is left out whole, never half-built (rule 5).
   - **New optional modules stay importable** (critic S-14).
     - `server._bind_optional` imports every OPTIONAL_MODULES entry. A ModuleNotFoundError that names another module, or a failing
       `bind`, marks the module "error"; a `register` error other than TypeError stops `build_runtime`; and `test_terminal_api` fails
       when any module errored. One half-built module would turn every builder's suite red.
     - So every new optional module of this build (`blotters`, `navhistory`, `collateral`, `importer`) imports another package's new
       module lazily, inside the function that needs it, and keeps its import, `register` and `bind` exception-free at every save.
4. **Integration** (the orchestrator's integrator):
   - apply every Part A request; run the Part A remainders; get both suites green;
   - walk every sidebar page and every route P1a registered, in the three books (the six new routes show NOT YET BUILT, 15.7).
5. **P5, phase-1 scope** (15.5).
   - Recommended: P5 takes the first free Part A slot (normally P2's, now the shortest Part A) for its hook-independent work: batch
     validation, report mode, data-pack fetch, CLI, locks / backup / cutoff / refusal guards, restore and the mini batch.
   - While the pool runs, `importer.py` follows step 3's optional-module rule: it imports `navhistory`, `collateral` and `blotters`
     lazily inside its functions and stays importable, with `register` and `bind` exception-free, at every save.
   - Until step 4 has landed, its unfinished tests live in `X\wip_import_tests.py`, which the suite does not collect (the name does not
     match `test_terminal_*.py`). P5's owns list gains that file. The tests move to `test_terminal_import.py` once they pass, and P5
     deletes the file before the gate, so no other builder's suite goes red and nothing stray is committed.
   - Its real-batch dry run waits for step 4 and runs between 00:00 and 16:00 ET (rule 7).
6. **P8-lite** (15.6). Recommended: write its fund reconcile test and its condition checker (15.6 item 3) while P5 works (two
   builders). Its dry-run check and its walk wait for P5, and its dry run for 00:00 ET (rule 7).
7. **Fixes:** at most two rounds, each re-verified by P8-lite (items 1-4 of 15.6, the checker included; the directive map and the
   browser-smoke file are phase 2). The two rounds are budgeted at 2-3 h together (15.11).
8. **Gate, deploy and import** (15.8).

### 15.3 Part A and Part B of P2, P3, P4 and P6

**Part A** has three parts:
- every contract hook the package owns, both tiers, except as E1-E4 below say;
- every task, test and acceptance item that the import of IMP-F1-20261027-A and its dry run need;
- the small display items that let 1.0.5 show the owner's positions honestly (15.7).

**Part B** is the rest. Four exceptions to "every contract hook in Part A":
- **E1 - C6 probes ship with their features.**
  - The five probe functions:
    - `orders.gtd_supported` (P2 task 7);
    - `blotters.execution_detail` (P2 task 4): the function behind `GET /api/executions/{id}`, whose drawer carries the C15 fail /
      resolve actions; 15.7 item 6 makes it the settlement-failure probe;
    - `swaps.rfq_rows` (P4 task 8);
    - `collateral.pledges_view` (P6 task 6);
    - `collateral.margin_calls_rows` (P6 task 5; 15.7 item 6 makes it the margin-calls probe).
  - None of them exists in 1.0.5, not even as a stub. A callable probe removes its PLANNED row from the Unsupported page, so 1.0.5 would
    claim GTD orders, the owner-triggered settlement fail, dealer quotes, CSA securities collateral and margin calls that it does not
    have.
- **E2 - all of C14 is Part B.**
  - The import never uses RFQs (C14).
  - `rfq_rows` is an E1 probe.
  - The RFQ_DEALER job runs on every scheduler tick and would join the lifecycle registry whose due keys the import's catch-up cutoff
    hashes (15.9 item 8).
  - In 1.0.5 the PB OTC desk keeps today's direct `otc.book`.
  - The `confirm_status` / `confirmed_ts` columns belong to C10 and are Part A.
- **E3 - C7's routes ship with their pages, except the executions route** (critic S-15).
  - Part A keeps only `GET /api/blotter/executions`, with `blotters.source_label`: P5's tests read the as-of labels through it, and P2's
    acceptance counts the owner's 320 executions with it.
  - Part B, because only 1.0.6 pages call them:
    - `GET /api/blotter/pending` with its stages, status filters and package grouping, `blotters.counts` and the stage vocabulary (the
      Pending page and P1b's Dashboard);
    - `GET /api/executions/{id}` (`blotters.execution_detail`, an E1 probe) and its drawer (P2 task 4);
    - PATCH `/api/orders/{id}` (amend), POST `/api/orders/{id}/replace` and the `GET /api/orders/{id}` takeover (settlements,
      `?review=1`): P2 tasks 5-6.
- **E4 - other hooks with no 1.0.5 consumer move to Part B** (critic S-15).
  - C9e `performance.returns_summary` (P1b's Dashboard, P3's return tiles).
  - C9f `T.pages.accounting.renderCashTab` (P6's Cash & Collateral page).
  - C16's `summary`, `position_requirements` and `custodian_of` (P1b's Dashboard, P3 tasks 5 and 13, P6's Accounts page).
  - C16's `lending.lender_label` stays Part A (15.7 item 8).

**P2**

| | Part A (1.0.5) | Part B (1.0.6) |
|---|---|---|
| Tasks | 1 except E1 / E3<br>3, server side only: `GET /api/blotter/executions` with the union, columns, statuses, sources through `blotters.source_label`, filters, CSV and paging<br>9; 10<br>from 11, only the as-of badge and label on the account Orders page's order-history and fills rows (15.7 item 7)<br>from 12, the tests of these | 2 whole (E3): `GET /api/blotter/pending` with every row field, the derived stages, the status filters, package grouping and the derived package state (PK-000001 never open), RFQ rows once `swaps.rfq_rows` exists, `blotters.counts`, the stage vocabulary, and `pending.js` (New order, row actions, polling)<br>the page of 3 (`completed.js`: filters, the import-batch filter)<br>4 whole (E3): `GET /api/executions/{id}` (`blotters.execution_detail`) and the drawer with the C15 fail / resolve actions<br>5; 6; 7 (with `orders.gtd_supported`); 8<br>the rest of 11<br>from 12, the tests of these |
| Acceptance | 1 (a), (d), (g), (h), (i), (j), (k)<br>2 for the executions route: every 3B field, the union of every source, its status and source filters, paging, and the CSV equal to the rows<br>3 through the API: 320 executions = 315 fills + 5 Financing rows; CSV 320 / 315 | 1 (b), (c), (e), (f)<br>2 for the pending route (status filters, stages), the rejection recorders and the GTD wording<br>3: Pending open 0 rows; the Cancelled filter with O-000008 / O-000018, their reasons and drawer timelines<br>4 |

**P3**

| | Part A (1.0.5) | Part B (1.0.6) |
|---|---|---|
| Tasks | 1<br>2, with the readers labelled "restated on \<date\> (batch X)" where 1.0.5 shows them (Performance, the TODAY / MTD / YTD baselines); the toggle to the original belongs to task 9<br>3<br>from 5, only borrow lenders through `lending.lender_label` in every payload P3 serves (position detail, Accounting borrows, counterparties, and `accounting.obligations()`, which feeds the account Cash & financing page's borrows table) and the as-of label fields of 15.7 item 7<br>8<br>14: the portfolio contract test, because C9c changes the rows `portfolio.js` reads | 4<br>the rest of 5, including the derivative rows rendering the C13 fields (PORTFOLIO-06)<br>6 (the Portfolio row drawer)<br>7 whole: C9e `performance.returns_summary` (E4) and the return tiles<br>C9f `renderCashTab` (E4)<br>9: NAV history API, portfolio as of a date, the History hub with its toggle and split view<br>10; 11; 12; 13 |
| Acceptance | 1 (a), (d), (g), (h)<br>2: Portfolio NAV ties A - L; the restatement of 10-21..10-26 with no import (mark-policy effects, the 10-22 dating line, residual 0)<br>3, limited to: Portfolio (the option and OTC rows with marks; Enter on each opens its instrument page with no HTTP >= 400), account Positions (an option row's drawer on US Exchange, an OTC contract's drawer on Goldman (PB)), the US Exchange Cash & financing borrows reading "street lender (assumed terms)", Accounting (balance sheet, trial balance with the new GLs, the cash tab) and Risk rendering, with 0 console errors and 0 HTTP >= 400 | 1 (b), (c), (e), (f)<br>2: the History series, the STT drawer, VMRK sector, exposure definitions<br>the rest of 3, with the Portfolio row drawer |

**P4**

| | Part A (1.0.5) | Part B (1.0.6) |
|---|---|---|
| Tasks | 1: C10-C13 (C14 is E2)<br>2, with the contracts table and contract drawer (`swapticket.js`) showing, for each as-of contract, the as-of badge, the D1 OTC label and "CONFIRMED - as-of booking at the owner's instruction (no electronic confirmation in the simulator)"; CDX rows render<br>3<br>4, with the curve spread shown in the terms<br>5; 6<br>7, including the ~88 swap-test asserts<br>9 | 8: RFQ, the RFQ_DEALER job, `rfq_rows`, the 17:05 confirmation, the desk's Request quote / Accept / Decline<br>10: the Derivatives page<br>11: the credit event, with its Unsupported row |
| Acceptance | 1, except the simulated credit event block<br>2; 4<br>5 booked through `otc.book` (the 1.0.5 desk path) instead of the RFQ flow; then the night's accruals (4451, 4453), the booking date's VM and integrity ok<br>walk: the PB OTC tab's contracts table with as-of contracts, 0 console errors | 1's credit event block<br>3<br>5 again, through the RFQ flow, with "the Derivatives tabs render"<br>6 |

**P6**

| | Part A (1.0.5) | Part B (1.0.6) |
|---|---|---|
| Tasks | 1: C15 whole; C17; C21; from C16 only `lending.lender_label` (E4: `summary`, `position_requirements` and `custodian_of` are Part B); no `margin_calls_rows` and no `pledges_view` (E1)<br>2 whole (S0 requires `settlement.fail` / `settlement.resolve`)<br>3, except the Cash & Collateral page, the Dashboard and the reuse-flag editor: the flag is stored, default off, and CashView and the account cash page show "restricted"<br>9 whole | 4<br>5, with `margin_calls_rows`<br>6, with `pledges_view`<br>7; 8; 10<br>task 3's displays on the Cash & Collateral page and the Dashboard, and the reuse-flag editor<br>C16's `summary`, `position_requirements` and `custodian_of` (E4), with their margin parts |
| Acceptance | 1 (a), (b), (e), (f), (h), (i), (j)<br>2: restricted 0 before any OTC; integrity ok<br>walk: the account cash page's restricted line on a seeded home with OTC VM, 0 console errors | 1 (c), (c'), (d), (g)<br>2: Accounts, the Cash & Collateral figures, Reg T excess<br>3 |

### 15.4 Every import step is in phase 1

Checked against every setup, line, package and catch-up record of batch revision 3 (sha256 `75690c19...`) and against P5's calls:

| Import step | Covered by (all phase 1) |
|---|---|
| S0: the commands it lists | `trade.book_as_of`, `trade.book_as_of_package` (P2 C8)<br>`otc.book_as_of` (P4 C10)<br>`cdx.terms` (existing)<br>`cash.asof_sweep`, `cash.asof_transfer` (P6 C15)<br>`cash.reaccrue` (P6 C17)<br>`nav.restate` (P3 C9d)<br>`settlement.fail`, `settlement.resolve` (P6 task 2)<br>`import.*` (P5) |
| S0: the inputs | `CurveStore.for_date`'s lookback (P4 C12)<br>SWAP_VM's CAUGHT_UP / CLOSE_PENDING rules for OAS dates (P4 task 3)<br>RATE_LAG and the dividend records (existing)<br>integrity and hold (P3 C9i) |
| S0: clock, apply-day snapshot, deadline, pack and cutoff | P5 tasks 3, 5 and 6 |
| S1 | orders / fills columns (P2 C8)<br>`otc_contracts` columns and the settlements' `ref_type` / `ref_id` (P4 C10)<br>settlement columns (P6 C15)<br>`nav_snapshot_versions` (P3 C9d)<br>import tables (P5)<br>chart rows (P1a's sync, P4 C11) |
| S2 / S2b | `import.record_inputs` (P5 task 4)<br>the store reload: `load_persisted`, and `for_date` reading `curve_points` (P4 C12) |
| S3 | the pack's chain rows (P5)<br>`InstrumentMaster.ensure_option` inside C8 (P2; `instruments.py` unchanged) |
| S4 | `cdx.terms` and `swaps_cds.terms_for` (existing)<br>the skip rule (P5) |
| Lines 24-28 | `otc.book_as_of` at the dealt terms, with:<br>- the refusals and the dated inputs;<br>- IM and day-0 VM on the value date;<br>- the CDX upfront to 2345 and its initial payment due T+1;<br>- the IRS curve spread;<br>- CONFIRMED as-of.<br>P4 tasks 2, 3, 4 and 7; funded through P2 C20 / C21 and P6 C15 (`sweep_in`, stamping) |
| Lines 29-31 and the package (32-33) | `trade.book_as_of` / `trade.book_as_of_package`, fees, T+1 settlement and one `hedge_packages` row (P2 task 9) |
| C1: SETTLE_DUE | as-of dating on the due date: the 10-22 CDX receipts and the 10-23 premiums (P6 C15); their funding (P2 C20) |
| C1: ACCRUE_DAILY | TRS / IRS nights from the value date, CDX from 10-22, night D posted on D+1, 4451 / 4453 (P4 tasks 3 and 7) |
| C1: SWAP_VM | each date's inputs, `ctx.asof_date` = d, CSA netting (P4 tasks 3 and 9; P2 C20 / C21; P6 C21) |
| C1: OPTION_MARGIN_MARK | existing |
| C1 holds SNAPSHOT back; C5b late snapshot | P5; `nav_as_of` in the Snapshot job (P3 task 2) |
| C2 `cash.asof_sweep` | restricted as of d; business-date cash (P6 tasks 3 and 9; P3 C9h) |
| C3 cash table, `cash.asof_transfer` | business-date balances and L18 (P3 C9h; P6 task 9; P2 C20) |
| C4 `cash.reaccrue` | cashint, pbcredit (base without restricted) and pb, with segment rounding (P6 tasks 3 and 9) |
| C5 `nav.restate` | groups, the funding group, original-0 rows, dated marks, the dating line, residual 0, idempotent (P3 tasks 2 and 3) |
| C6 integrity, trial balance | L5 / L9 / L18; M4 / M5 on the latest chain; FAILED is not an error (P3 C9g, C9i; P4 C11 `OTC_COST_GLS`) |
| Report | business-date cash table, the restated split and the bridge (P5 task 5; P3 C9d / C9h) |
| What the owner sees after the apply | 15.7 items 1, 7 and 8, and the P3 / P4 / P6 Part A displays |

Nothing the import touches is in Part B.

### 15.5 P5 in phase 1

**Phase 1:**
- tasks 1-7, 9 and 10 whole; every test runs, including the Completed Trades source labels through C7's executions route, which is
  Part A;
- task 9 copies the batch from `docs\imports\IMP-F1-20261027-A.jsonl` (15.2 step 1) and verifies its sha256;
- from task 8: the CLI as `python -m finsim.terminal.importer ...` (and `importer.main(argv)`), and the read-only C19 routes;
- acceptance 1 and 2 whole: the real batch's report and dry run on a fresh owner-DB copy, with a pack fetched for the build-day window
  (the dry run between 00:00 and 16:00 ET, rule 7);
- its "Depends on" becomes P1a and Part A (not P1b or P7); its owns list gains `X\wip_import_tests.py` until the gate (15.2 step 5).

**Waits for phase 2, with the reason:**
- `components\imports.js` (task 8). Its hosts are the History hub's Imports tab (P3 task 9) and Completed Trades' batch filter (P2 task
  3), both Part B, so 1.0.5 has no page to mount it. In 1.0.5 the owner gets the dry-run report instead (15.8).
- `python -m finsim import` (C18). It is P7's `app.py` dispatch (P7 task 8, phase 2); 1.0.5 uses `-m finsim.terminal.importer`.
- Acceptance 3 (the walk of Completed Trades, Derivatives, History NAV and the Imports panel). Those pages are Part B; the phase-1 walk of
  the dry-run copy is P8-lite item 4.
- **Phase-2 additions:**
  - `imports.js`, with its tests, and the walk on a fresh copy of the live database that holds the applied import;
  - the C18 path: `python -m finsim import --help` exits 0, and the mini batch runs through it;
  - report mode and a dry run on that copy: lines 24-33 report `already_imported` and 0 entries are posted (report mode at any time,
    the dry run between 00:00 and 16:00 ET, rule 7).

### 15.6 P8-lite (acceptance for 1.0.5)

P8 builds it in its own files; its owns list gains `X\terminal_import_check.py` (item 3). The directive map, the browser-smoke test and
`terminal_cdp.py` are phase 2.

1. **Suites.** Both green on py-3.12 and py-3.14 through `scratchpad\run_suite.py` (P7's runner is phase 2), with exact counts and the
   reason for every skip.
2. **`test_terminal_fund_reconcile.py`** (TEST-04), as P8 task 4 says, except two parts that phase 2 adds because pledges are P6 Part B:
   the T-bill pledged under the CSA, and the T-bill half of "collateral transfers change no P&L".
3. **The real batch, independently of P5.**
   - Take a fresh sqlite3-backup copy of `~/.finsim/terminal.db` (source opened `mode=ro`) into a new folder under `scratchpad\checks\`.
   - Fetch a pack for the build-day window; run the report, then the dry run (between 00:00 and 16:00 ET, rule 7).
   - **The checker** (critic S-02) is a deliverable, not throwaway evidence: the orchestrator reruns it on the apply day (15.8 step 8).
     - It is `X\terminal_import_check.py`, P8's. The suite does not collect it (its name does not match `test_terminal_*.py`); the gate
       commits it through the `terminal_*.py` pathspec.
     - Call: `py -3.12 tests\terminal_import_check.py --batch <batch> --source <the database the dry run copied> --dryrun <its work
       folder> [--second <a second dry run's work folder>] --summary <file>`.
     - It opens every database `mode=ro` and writes only the summary file, so on the apply day it can read the stopped live database.
     - It prints PASS or FAIL with the evidence for each check below (idempotency only with `--second`), and it exits 0 only when the
       first five pass.
     - It computes the row hashes, the business-date cash and the trial balance itself, from the databases. It checks the terms
       against both the batch's payloads (lines 24-33 and the package record) and the table below. It reads the integrity result and
       the restatement residuals from the result database.
     - Its `--summary` writes the owner's plain-English report (below) from the dry run's report JSON and its own results.
     - P8-lite runs it on the build-day dry run and after every fix round's re-run.
   - The checks. The first five are the owner's import conditions.
     - **Preserved lines unchanged:** lines 1-21 report present_differs and 22-23 present_match. Every F1 order, fill, lot, borrow,
       settlement, futures VM, transfer and `nav_snapshots` row that existed before the dry run is identical after it (row hashes; the
       import only adds rows).
     - **Imports at his exact terms** (critic S-06: the OTC contracts and the options carry different dates):

       | Product | Side | Terms | Dates |
       |---|---|---|---|
       | TRS ORCL / META | short (pay total return) | reset 129.13 / 697.52 on 38,700 / 7,200 units | trade, value and start date 2026-10-21; entered as executed 19:45 ET |
       | CDX IG / HY | buy protection | 64 / 352 bp on 3,000,000 / 4,000,000 | as the TRS |
       | IRS | pay fixed | 4.720 % on 7,500,000 | as the TRS |
       | Options | BTO ORCL 140C, META 660C, IBIT 46C and QQQ 700P; STO QQQ 675P (all expiring 2026-11-20) | premiums 3.55 / 23.59 / 2.10 / 17.64 / 10.25 on 387 / 72 / 105 / 150 / 150 | trade date 2026-10-22 at 16:00 ET; settlement 2026-10-23 |

     - **No account below zero:** every account closes every business date at or above zero through the day before the run, and every
       current balance is >= 0.
     - **Integrity:** integrity full ok and the trial balance balanced.
     - **Residual 0:** the restatement residual is 0 for every account and F1:ALL on every date.
     - **Idempotent:** a second dry run on the result posts 0 entries, and the -wal file is empty. It runs on the build day and in every
       fix round; on the apply day the five conditions decide (15.8 step 8).
   - The owner's report, in plain English (the checker's `--summary`):
     - every line's decision and status;
     - the business-date cash table and the funding moves;
     - the restated NAV with its split;
     - the bridge to the workbook's 25,770,700.86;
     - where the figures differ from this plan's estimates;
     - that the batch file's header was written before the two-phase split and the standing pre-approval, so it still names
       "packages P1a / P1b - P7" and "the owner's typed go"; the file is unchanged on purpose, because its sha256 is pinned (15.1
       rule 1).
4. **Browser walk** of the dry-run result served `--offline` (Playwright py-3.14, 1440x900 and 1100 px; screenshots under
   `scratchpad\sweep\finsim1-105-p8lite\`).

   | Page | What it must show |
   |---|---|
   | Dashboard | the 1.0.5 attention tiles; every 1.0.5 sidebar entry of the Fund book renders; none of P1's news strings |
   | Portfolio | the 5 options and 5 OTC contracts with marks; Enter on one option row and on one OTC row opens its instrument page with no HTTP >= 400 (the Portfolio row drawer is P3 task 6, 1.0.6; critic S-01) |
   | US Exchange | Positions: one option row's drawer; the 5 shorts' drawers say "street lender (assumed terms)"<br>Orders: the as-of option orders and fills with the AS-OF badge, the D1 label, the entered time and the batch<br>Cash & financing: the 5 borrows read "street lender (assumed terms)" (critic S-07) |
   | Goldman (PB) | Markets > OTC: the contracts table with 5 contracts at his dealt terms, CONFIRMED as-of, MODEL marks, and a contract drawer<br>Positions: one OTC contract's drawer, with its swap marks<br>Cash & financing: the restricted line |
   | Risk | the contracts in exposure |
   | Accounting | A - L = NAV; 4451, 4453 and 2345 in the trial balance (1335 and 4452 carry no line in this batch: both CDX receive their upfront, the TRS and the IRS have none, the options are listed, and no ORCL / META ex-date falls in the window; critic S-05); borrows with "(assumed terms)"; the cash tab |
   | Settings | Data Status has no news section; none of P1's news strings (critic S-09) |

   - **Keys:** the 1.0.5 key map (15.7 item 3) on F1-F12 and Shift+F1-F12; the "?" help and Settings > Keyboard list none of the five
     page actions whose pages are not built (critic S-11).
   - **No errors:** 0 console errors, 0 HTTP >= 400, and no "undefined", "NaN" or "[object Object]".
   - **Wording check.**
     - Scope: the text of every position row and drawer, order and fill row, contract row and drawer, Risk position row, the account
       Cash & financing borrows table, and the Accounting position, borrow and margin sections.
     - It contains none of "modeled", "modelled", "hypothetical" or "estimated", in any case.
     - Allowed, and listed in the evidence:
       - the valuation-quality labels "MODEL", "model, intraday" and "as of the last VM" on OTC marks (D4);
       - terms disclosures that are not position rows, such as the PB fee schedule's "modelled assumptions" note;
       - memos of journal entries written before 1.0.5 (records are never altered, D3).
5. **Defects** go to their owning package (P1a, P2, P3, P4, P6 or P5; the checker's to P8). A Part B, P1b or P7 item is minor and
   marked "phase 2".

### 15.7 What 1.0.5 looks like between the phases

P1a ships what the P1 section gives it: C1-C6, the chart sync, the news-module retirement, the schema fixture and the
duplicate-instance message. It ships them with the changes below. Rule 1 makes these override C1, C6, P1 task 2, the P1 split and P1
acceptance 3 where they differ.

1. **A 1.0.5 Dashboard.**
   - P1a writes `pages\dashboard.js`. It is required: not `data-optional`, and not in PLANNED_STATIC / OPTIONAL_STATIC.
   - P1a adds `GET /api/attention/summary?book=` (critic S-08): `api.py` serves it from `attention.attention_summary`, with its test in
     `test_terminal_attention.py`. C3 names the module but no route; today the summary rides inside `/api/news`, which P1a removes in
     the same step, and P1 task 8's attention test and acceptance 2 already call the route.
   - It shows the "Requires your attention" tiles from `GET /api/attention/summary?book=` (C3), ported from the 1.0.4 news page's strip
     with its links:

     | Tile | Opens |
     |---|---|
     | working orders, pending settlements | `#/activity?type=orders` / `#/activity?type=settlements` |
     | collateral calls and recalls | Risk's financing section |
     | compliance | Risk compliance |
     | integrity | Accounting > Integrity |
     | provider pauses | Settings > Data Status |
     | open items | `T.attention.expand()` |

   - Below the tiles, one `T.nib` line names what the next release adds.
   - It calls no `/api/dashboard`: that route does not exist until `dashboard.py` lands (R2B-04).
   - `/` and unknown routes land on it, and `#/news` redirects to it with C1's toasts, so the owner keeps his daily attention summary.
   - P1b rewrites the page in 1.0.6 (task 4) and re-links the tiles.
2. **Sidebar.** P1a replaces the "Daily News" row with "Dashboard" (F12) at the top. The rest of the 1.0.4 sidebar stays until P1b
   (task 3).
3. **Keys.** The registry has its 74 actions, but the 1.0.5 default map keeps every 1.0.4 key except F12:

   | Key | 1.0.5 action |
   |---|---|
   | F12 | `page.dashboard` (`page.news` is gone) |
   | F8 | `nav.orders` |
   | Shift+F4 | `nav.positions` |
   | Shift+F5 | `nav.cash` |
   | Shift+F6 | `nav.transactions` |
   | Shift+F8 | `page.activity` |
   | Shift+F10 | `page.history` (the last past lifetime) |

   - `page.pending`, `page.completed`, `page.accounts`, `page.cash_collateral`, `page.derivatives` and `page.trading` have no default
     key in 1.0.5.
   - The "?" help and Settings > Keyboard list only actions that work (critic S-11): an action whose page is not in the build
     (`T.pages.<name>` undefined) is hidden. So the five page actions above other than `page.trading` appear only in 1.0.6, when their
     pages load. The registry still holds 74 actions.
   - In 1.0.6 P1b switches on the Q10 map, the hub re-target and C1's account-page F8 / Shift+F8 keys, together with the pages they open.
     Otherwise the owner's F8, Shift+F4, Shift+F6, Shift+F8 and Shift+F10 would open NOT YET BUILT panels in the meantime.
   - P1a's static test asserts this map and the hiding rule; P1b's asserts Q10.
4. **New routes.**
   - `#/pending`, `#/completed`, `#/accounts`, `#/cash`, `#/derivatives` and `#/history(/tab)` show the NOT YET BUILT panel and make no
     API call: optional stubs, and for the hub until `T.pages.history.hub` exists.
   - No sidebar entry and no default key points at them in 1.0.5. `#/trading` works.
5. **News in 1.0.5.**
   - No news job runs (`news.py` is gone, so there is no NewsStore and no worker slot), `/api/news` answers 404, and nothing shows news.
   - P1a also makes three of P1 task 2's edits:
     - `server.py` skips the retired providers' `provider_health` rows on load, with its test, so a stale news failure never becomes a
       permanent WARN in the strip;
     - `index.html` gets its new meta description and header comment, since P1a edits that file anyway;
     - `settings.js` skips the Data Status news block when the health answer has no news (critic S-09). Once `news.py` is gone,
       `/api/health` sends `news` null, and the block would otherwise still show a "news" heading and "news is off in this runtime".
   - Left for P1b, and inert in 1.0.5 (none of it shows):
     - the block's code and the `news_enabled` setting;
     - `T.PROVIDER_LABEL`'s news keys and `attention.js`'s injection row;
     - the news code in worker / config / server / db / tables.
6. **Unsupported page.**
   - The margin-calls PLANNED row probes `callable(collateral.margin_calls_rows)` instead of the `collateral` module (E1).
   - Until that probe passes, the forced-liquidation row reads "Forced liquidation: never - positions are never sold for you; a margin
     shortfall raises a warning and an attention item". After that it uses C6's wording.
   - The settlement-failure PLANNED row probes `callable(blotters.execution_detail)` instead of the `collateral` module (E1; critic
     S-10): the owner's fail / resolve actions live in that function's drawer (P2 task 4, 1.0.6). Until then the row reads "Simulated
     counterparty settlement fails: NOT_YET - unfunded payables are already marked failed and retried automatically; the fail you
     trigger yourself, and its resolution, are not in this version yet".
   - In 1.0.5 the historical import is supported (the importer module). Margin calls, the owner-triggered settlement fail, OTC dealer
     quotes, GTD / next-session orders and CSA securities collateral stay PLANNED.
7. **As-of labels wherever 1.0.5 lists his rows.**
   - P2 adds `blotters.source_label(conn, kind, row) -> {source, label, asof, entered_ts, batch_id}` (tier 1), where kind is `order`,
     `fill` or `otc_contract`. It returns:
     - for booking `AS_OF`: the D1 labels, with the batch id taken from the booking command's `as_of.batch_id`;
     - for `HISTORICAL_MODEL`: "Historical model (not an independent execution)";
     - for system fills: "System (...)";
     - otherwise: "Simulated fill".
   - These payloads carry `booking` and `source_label`: P3's account orders / fills payloads, position drawer and Activity rows, and P4's
     contracts payload.
   - `orders.js` (P2), `positions.js` / `activity.js` (P3) and `swapticket.js` (P4) show `.badge-asof` with the label.
   - C7's executions route uses the same function.
8. **Lender wording.** Every payload that returns a stored borrow lender passes it through `lending.lender_label`:
   - P3 for its payloads: position detail, Accounting borrows, counterparties, and `accounting.obligations()`, whose `borrows` feed the
     account Cash & financing page's borrows table (`cash.js` line 172 shows `r.lender` as it arrives; critic S-07);
   - P6 for its own: the credit desk's stock borrows and the counterparty exposures in `lending.py`.
   - His 5 shorts then read "street lender (assumed terms)" everywhere.
9. **Nothing the owner uses breaks.**
   - Every 1.0.4 page, account tab, ticket and key works as before.
   - New in 1.0.5:
     - news is gone, and the Dashboard shows the attention tiles;
     - his imported positions appear on Portfolio, the account pages, the contracts table, Risk and Accounting;
     - restricted cash shows on the PB cash page.
   - Arriving in 1.0.6: the Pending, Completed, Accounts, Cash & Collateral, Derivatives and History pages, the full Dashboard, the new
     sidebar and the new keys.
   - P1a acceptance 3 checks this 1.0.5 behaviour: the 1.0.5 Dashboard on `/`, the sidebar's first row, the 1.0.5 key map with the
     hidden page actions, NOT YET BUILT on the six new routes, Settings > Data Status without a news section, and P1's news-string
     search on the Dashboard and Settings (critic S-09). P1b's acceptance checks the Q10 behaviour. The integrator's walk (15.2 step 4)
     and P8-lite item 4 check the whole.

### 15.8 The 1.0.5 gate, deploy and import (orchestrator)

1. **After P8-lite and the fixes:**
   - append the builders' log lines to BUILD_LOG in order;
   - set VERSION to 1.0.5;
   - add a CHANGELOG entry for 1.0.5 that also says what 1.0.6 brings;
   - hold the deferred doc texts for 1.0.6, except P1a's KEYBINDS line for F12.
2. **Suites** on py-3.12 and py-3.14 in the working tree (`scratchpad\run_suite.py`).
3. **Tooling** (section 12 step 2), adjusted for 1.0.5:
   - The P7 files do not exist yet and the README is unchanged, so `finsim/__main__.py`, `FinSim.cmd`, `finsim/FinSim.cmd`,
     `finsim/README.md` and `finsim/tests/run_terminal_suite.py` stay out of PATHS, out of the `git add -A` pathspecs and out of the
     archive. The archive is `git archive <SHA> finsim/finsim finsim/pyproject.toml data/nyse-holidays.json`.
   - The rest of step 2 applies:
     - quoted pathspecs, so deletions such as `test_terminal_news.py` are recorded;
     - no `site\reports`;
     - RELEASE.txt with the SHA, branch, VERSION and time;
     - a manifest of the whole release folder.
4. **Commit** on `claude/finsim-live-terminal`. This is the snapshot that rule 3 waits for.
5. **Verify the commit** from a `git archive` extract outside %TEMP%, running `scratchpad\run_suite.py` on both interpreters. The counts
   must equal the tree's.
6. **Release** from that SHA, with its manifest.
7. **Deploy**, on the owner's conditions (standing pre-approval, 2026-10-27 15:12 EDT):
   - both suites green;
   - a fresh read-only owner-DB copy, served from the release, shows his 23 positions unchanged and integrity ok;
   - the manifest verified.

   Then run `install_release.ps1 -OwnerSaidDeploy`. It backs up terminal.db, -wal and -shm first, and rolls back automatically if the
   after-install check fails. Report to the owner.
8. **Import** on 2026-10-28, the apply day.
   - Start no later than 14:30 ET: it takes about an hour, and S0 refuses at 16:00 ET. Fetch the pack after 09:45 ET, so that the Cboe
     chain's previous close is L's official close (rule 7).
   - Follow section 7's apply procedure, with three 1.0.5 changes:
     - The commands are `py -3.12 -m finsim.terminal.importer ...`, run from the installed release's `finsim` folder (C18 ships in
       1.0.6).
     - Step 4: run P8-lite's checker (15.6 item 3) on the apply-day dry run, with `--source` the stopped live database (opened
       `mode=ro`) and `--dryrun` the dry run's work folder. Run it from the 1.0.5 commit's verified extract (step 5), so that no
       phase-2 edit can change it. Send him the dry-run report and the summary the checker writes.
     - Step 5: "his typed go" becomes his standing pre-approval. The apply runs only when the checker prints PASS for all five
       conditions, with `--owner-go "standing pre-approval 2026-10-27 15:12 EDT: All FinSim releases"`, which is stored in
       `import_batches.owner_go`. Idempotency was proven on the build-day and fix-round runs; the apply day does not repeat the second
       dry run.
   - Tell the owner before stopping the terminal. Afterwards restart it with `python -m finsim open`, check Portfolio, the PB contracts
     table, restricted cash and integrity, and report.
   - If, after a complete apply, 1.0.5 does not start on the imported file or integrity full is not ok (critic S-13): stop, restore per
     section 7, restart 1.0.5 on the restored file, confirm integrity, and report. The restore is allowed only while no owner command
     has run since the apply (15.9 item 8). The cause is then handled as below.
   - If it starts with integrity ok but a page check fails, keep the import: his positions are in place, and the checker verified them.
     Report, and fix the page as 1.0.5.1 when it hides or misstates his positions, else in 1.0.6.
   - If a condition fails or any step refuses: do not apply; restart the terminal unchanged and tell him why. Then, by cause:
     - **A code defect** (a refusal or a failed condition that a code change fixes): phase 2 does not start. The owning package fixes
       it, P8-lite re-verifies (items 1-4, the checker included), and the fix ships as 1.0.5.1 through steps 1-7 of this list
       (VERSION 1.0.5.1, its own commit, archive check, release and deploy). The import retries the same day if it can still start by
       14:30 ET, else on the next business day. Phase 2 starts after it (rule 3).
     - **A data or clock cause** (his transfer into Treasury for ASOF_CURRENT_FUNDS, which only he can decide; a missing input; 1.0.5
       not installed by 14:30 ET): phase 2 may start. On the retry day the orchestrator plans phase-2 launches so that at most two other
       agents run from 13:00 ET (or from an earlier retry start it sets, never before 09:45 ET) until the apply has ended, and the
       import starts by 14:30 ET.
     - Every retry runs before 16:00 ET on a business day (its window then runs through that day's L), and no later than 2026-11-19.
   - If an apply ends PARTIAL: re-run the apply the same day before 16:00 ET (it resumes idempotently); otherwise restore per section 7
     and report.
   - If 1.0.5 is not installed by 14:30 ET on 2026-10-28, the import moves to 2026-10-29 before 16:00 ET. 1.0.5 still deploys when its
     gate passes.
9. **Gap ids closed at this gate:** UI-18 (a release without `site\reports`) and STARTUP-10 (VERSION 1.0.5).

### 15.9 Phase 2 (release 1.0.6)

1. **Start** after 15.8 step 5 (the commit verified) and once the import has been applied, or postponed for a data or clock reason. A
   postponement caused by a code defect first ships 1.0.5.1 and runs the import (15.8 step 8, rule 3).
2. **Pool of 4.**
   - First batch: P1b, P2 Part B, P3 Part B and P6 Part B. P4 Part B takes the first free slot (normally P1b's) and P7 the next.
   - Why this order:
     - P1b still rewrites the shared files early (section 5).
     - P6's real `summary`, `position_requirements`, `margin_calls_rows` and `pledges_view` feed P1b's Dashboard and P3's Risk and
       Activity.
     - C14 has a single consumer, P2's Pending rows and rejection recorder, which codes against its names.
     - P7 consumes nothing.
   - Hooks first:
     - P2: `orders.gtd_supported` and the E3 routes: the pending route with `blotters.counts` and the stage vocabulary, the
       execution-detail route with `blotters.execution_detail`, amend and replace;
     - P3: C9e and C9f (E4);
     - P4: the C14 table, its command names and `rfq_rows`;
     - P6: C16's `summary`, `position_requirements` and `custodian_of` (E4) with their margin parts, `margin_calls_rows` and
       `pledges_view`;
     - P1b: `dashboard.py`.
3. **Integration** and remainders.
4. **P5's phase-2 remainder** (15.5), in a pool slot once P2 and P3 Part B have landed its hosts; otherwise after step 3. Its report
   mode runs at any time, its dry run between 00:00 and 16:00 ET (rule 7).
5. **The full P8** (its section).
   - Its owner-DB items use a fresh copy of the live database, which holds the applied import (if the import is still unapplied, the
     newest dry-run copy).
   - P8 task 5's dry run of the real batch then reports lines 24-33 `already_imported` and posts nothing. Before the apply, it is the full
     dry run on 1.0.6. It runs between 00:00 and 16:00 ET (rule 7), so P8 does its other work (suites, smoke, directive map, fund
     reconcile, the page walk) first.
   - The fund reconcile test gains the T-bill pledge leg.
6. **Fixes:** at most two rounds.
7. **The 1.0.6 gate.**
   - Section 12 in full: VERSION 1.0.6; CHANGELOG; the deferred doc texts of section 9 items 1-2; the P7 paths and the README guard; P7's
     `run_terminal_suite.py` on the archive.
   - The owner's deploy conditions as in 15.8 step 7, on a post-import copy (critic S-03):
     - his 33 positions (quantities and cost), the restated NAV versions and the ledger balances served by 1.0.6 equal 1.0.5's on the
       same copy, except the differences the phase-2 packages list as intended in their results, each with its reason and the test
       that pins it (for example a Reg T deficit reservation lowering available cash, or a correction under item 8);
     - any other difference blocks the deploy;
     - integrity is ok.
   - Deploy; then the attended launcher check (9.6) when the owner is available; then the final report per section 20.
8. **Rules for phase 2.**
   - **Part A behaviour is final.** The imported rows are never re-booked or altered. P5's tests and the Part A money tests stay green
     unchanged, unless a Part B task changes a behaviour on purpose or a Part A defect is fixed (next point); then that test's owner
     says why.
   - **A Part A defect in posted entries is fixed forward** (critic S-03). If phase 2 (the History split, the Derivatives tabs, the full
     P8, the fund reconcile) finds a defect in entries the import already posted on the live database - a GL, a VM, an accrual, a
     restated split:
     - the owning package fixes the code and adds a correction command in its own files: actor import, deterministic request ids
       `corr:<batch>:<n>:<part>` (a re-run replays), and no UPDATE or DELETE of any stored row. It posts dated, labelled entries, each
       on the business date of the entry it corrects, with the memo "correction of \<entry id\>: \<reason\> (batch
       IMP-F1-20261027-A)", and never a plug to a target figure (D3);
     - `nav.restate` then writes new versions for the dates it moves (reason "correction", a `correction` group, residual 0);
     - it runs like the import: a dry run on a fresh copy between 00:00 and 16:00 ET, the checker, a backup first, under the standing
       pre-approval, shipped in a 1.0.5.x release or in 1.0.6;
     - the owner gets a plain-English report of what changed and why.
   - **The pre-import restore** (section 7) discards everything after the apply. It is used only while no owner command (actor `ui`)
     and no fill of his orders (actor `matcher`) has run since the apply's last command; the server's own provider and lifecycle
     commands do not count, because the scheduler derives them again. After that a restore needs his decision, and a correction is
     the default.
   - **New lifecycle jobs start forward.** A job that first ships in 1.0.6 (MARGIN_CALL, RFQ_DEALER, the 17:05 OTC confirmation) starts
     on the first accounting day it runs on an existing database. It creates no call, quote or confirmation for a date before 1.0.6 was
     installed.
   - **RFQ_DEALER is quiet when idle.** It adds no due key while no RFQ is REQUESTED or QUOTED, so an import cutoff never moves with its
     ticks.
   - Each of these jobs is tested on a copy that holds an applied import (the mini batch).
   - If the import is still unapplied when 1.0.6 passes its gate, it is applied from 1.0.6 by the procedure of 15.8 step 8.

### 15.10 Gap ids by phase

| Phase | Gap ids | Count |
|---|---|---|
| 1.0.5 | P1a: UI-01, UI-03, UI-17, STARTUP-03, TEST-07<br>P2, P4 and P5: OMS-04<br>P3, P4 and P5, with P2 / P6 parts: PORTFOLIO-01<br>P4 with P3: PORTFOLIO-02, PORTFOLIO-08<br>P3: PORTFOLIO-14, PORTFOLIO-20 (the live Portfolio fix shipped in 1.0.3.2 at 15:19, plus the contract test)<br>P6: PORTFOLIO-10, TEST-03<br>P5: TEST-01<br>the gate: UI-18, STARTUP-10 | 16 |
| 1.0.6 | UI: 02, 04, 05, 06, 07, 08, 09, 10, 11, 12, 13, 14, 15, 16, 19, 20, 21<br>OMS: 01, 02, 03, 05, 06, 07, 08, 09, 10, 11, 12, 13, 14, 15, 16, 17<br>PORTFOLIO: 03, 04, 05, 06, 07, 09, 11, 12, 13, 15, 16, 17, 18, 19<br>STARTUP: 01, 02, 04, 05, 06, 07, 09<br>TEST: 02, 04, 05, 06, 08, 09, 10 | 61 |
| Deferred | STARTUP-08 (9.4; each gate updates the scratchpad tooling) | 1 |

If P4's tasks 5-6 slip (15.2 step 3), PORTFOLIO-02 moves from the 1.0.5 row to the 1.0.6 row (15 and 62), and PORTFOLIO-06's C13
fields move with it.

**Started in 1.0.5 and completed in 1.0.6:**

| Gap ids | Done in 1.0.5 | Done in 1.0.6 |
|---|---|---|
| UI-02 | no news job runs | the remaining news code is removed |
| UI-04, UI-05 | the attention module and the 1.0.5 Dashboard's tiles | the re-linked tiles and the full Dashboard |
| UI-07..UI-12 | the routes | the pages, sidebar entries and keys |
| UI-13 | the 74-action registry | the Q10 map |
| UI-15 | retired providers hidden | the rest |
| OMS-02, OMS-07, OMS-13 | the executions route | the Completed Trades page |
| OMS-09 | the fail state and commands | the drawer actions |
| OMS-17, TEST-02 | the API tests | the page tests |
| PORTFOLIO-06 | the C13 fields | the rows that render them |
| TEST-04 | the test without the T-bill pledge leg | the pledge leg |

**Phase-1 gap ids per builder:**

| Builder | Gap ids |
|---|---|
| P1a | UI-01, UI-03, UI-17, STARTUP-03, TEST-07 |
| P2 | OMS-04 |
| P3 | PORTFOLIO-01, PORTFOLIO-02, PORTFOLIO-08, PORTFOLIO-14, PORTFOLIO-20 |
| P4 | OMS-04, PORTFOLIO-01, PORTFOLIO-02, PORTFOLIO-08 |
| P6 | PORTFOLIO-10, TEST-03 |
| P5 | TEST-01, OMS-04, PORTFOLIO-01 |
| P8-lite | none closed (TEST-04 partial) |

### 15.11 Hours

Wall clock. Build 2's packages took 1.5-4 h each with four builders. P4's Part A sits above that range, and the first end-to-end dry
run, the two fix rounds and rule 7's dry-run window are budgeted at their slow end (critic S-15).

**Phase 1 (1.0.5)**

| Step | Hours |
|---|---|
| 0. finsim-104 is done: 1.0.4 went live at 16:54 EDT (BUILD_LOG 16:55) | sets the start S, when P1a starts: about 17:30 EDT on 10-27 |
| 1. P1a alone, with 15.7 | 2-2.75 |
| 2. Part A pool (P2 2.5-3.5; P3 3.5-4.5; P4 4-5, or 3.5-4.5 when its tasks 5-6 slip; P6 3.5-4.5) | 4-5 |
| 3. Integration and Part A remainders | 1-1.5 |
| 4. P5, phase 1 | 3.5-4.5 after step 3 if it started early in P2's slot; 4.5-5.5 otherwise |
| 5. P8-lite | 1-1.5 after P5 if the fund test and the checker were written alongside; 2-2.5 otherwise |
| 6. Fixes (at most 2 rounds, each re-verified by P8-lite items 1-4) | 2-3 |
| 7. Gate | 1-1.5 if the tooling is prepared during step 4; 1.5-2 otherwise |
| 8. Deploy | 0.5 |
| 9. Import | 1-1.5; the pack after 09:45 ET, the start by 14:30 ET |
| Total | 18.5-24.25 run one after another; 16-21.75 with the three overlaps |

- With S = 17:30 EDT, integration (step 3) ends at about 00:30-02:45 EDT on 10-28, so every phase-1 dry run falls inside rule 7's
  window. On the overlapped path the import ends at about 10:45-15:15 EDT: at the fast end it waits for the 09:45 ET pack, and at the
  slow end it starts at about 13:45 EDT.
- Run one after another, the steps end at about 12:00-17:45 EDT and can miss 16:00 ET.
- Steps 4-8 take 8-11 h after step 3, so the import starts by 14:30 ET only if step 3 ends by about 03:30 EDT (06:30 at the fast end).
  The hourly progress lines say so: 10-28 holds while step 3 is on course to end by about 03:30 EDT; once it ends later, they tell
  the owner that 10-29 is the likely apply day (15.8 step 8).

**Phase 2 (1.0.6)**

| Step | Hours |
|---|---|
| 1. Pool (P1b 2-2.5, P2B 5.5-7, P3B 5-6, P6B 4.5-5.5, P4B 3-4, P7 2-3: about 25 builder-hours with the E3 / E4 hooks; P4B starts when P1b ends, P7 when the next builder ends, so the P6B-then-P7 slot finishes last) | 6.5-8.5 |
| 2. Integration and remainders | 1-1.5 |
| 3. P5's phase-2 remainder | 1-1.5 (inside step 1 when its hosts land early; its dry run between 00:00 and 16:00 ET) |
| 4. Full P8 | 3-4 (its real-batch dry run between 00:00 and 16:00 ET) |
| 5. Fixes (at most 2 rounds) | 1.5-2.5 |
| 6. Gate | 2-2.5 |
| 7. Deploy and report; the attended launcher check when the owner is available | 0.5-1 |
| Total | 15.5-21.5 (14.5-20 with P5 inside the pool) |

Starting after the import (about 10:45-15:15 EDT on 10-28), 1.0.6 passes its gate at about 03:00-12:00 EDT on 10-29. At the fast end
P8's dry run waits for 00:00 ET (rule 7) while the first fix round runs.

### 15.12 Critic answers (S-01..S-16)

An independent critic checked this section as written from 16:16 EDT and raised 16 findings: 4 major (S-01..S-04) and 12 minor. Each
was verified against the plan, the batch (sha256 `75690c19...`), the code and BUILD_LOG. All 16 hold and all 16 are fixed; none was
rejected. Where the evidence called for it, the fix differs from the critic's proposal, and the line says how. One line per finding:
verdict and evidence, then where the fix lives.

- **S-01** (major) - fixed. Holds: `portfolio.js` (lines 107-117) sends Enter to the instrument page (a desk forward to the FX desk)
  and has no drawer; the only position drawer is `positions.js` (lines 240-275), and P3 task 6 is Part B. P3's Part A acceptance 3 and
  P8-lite item 4 now check the Portfolio rows' marks and that Enter opens each row's instrument page (`instrument.js` renders OTC kinds;
  a CDX contract is kind SWAP in the master), and walk the option drawer on US Exchange > Positions and the OTC drawer on Goldman (PB) >
  Positions (15.3, 15.6). Moving task 6 into Part A was not chosen: it would lengthen the path to the import.
- **S-02** (major) - fixed. Holds: P8 owned four test files only, and the five conditions and the owner's summary existed only as
  build-day evidence. P8-lite now delivers `X\terminal_import_check.py`: read-only, PASS or FAIL with evidence per condition,
  idempotency with `--second`, and the plain-English summary from the importer's report JSON. It runs on the build day, after every fix
  round and on the apply day, where the apply runs only on five PASS (15.6 item 3, 15.8 step 8, sections 0 and 12). It lives in the repo
  rather than `scratchpad\checks\`, because the scratchpad is under %TEMP% (S-04).
- **S-03** (major) - fixed. Holds: nothing allowed a forward fix of entries the import posted, the 1.0.6 condition "equal 1.0.5's" left
  no room for one, and a restore discards everything after the apply. 15.9 item 8 adds a labelled, idempotent correction command
  (dated entries, no row edits, no plug) followed by `nav.restate` and a report to the owner, and allows the pre-import restore only
  while no `ui` or `matcher` command has run since the apply (MAX(commands.id) alone would also move with the server's own provider and
  lifecycle commands). 15.9 item 7 compares 1.0.6 with 1.0.5 except listed, tested, intended differences.
- **S-04** (major) - fixed. Holds for the plan's text: the batch and `rev3_batch.py` were only in %TEMP% until the orchestrator acted on
  this finding at 16:56 EDT (BUILD_LOG), copying the batch to `docs\imports\IMP-F1-20261027-A.jsonl` and mirroring both to
  `C:\Users\logan\finsim-orchestrator\scratchpad-mirror\`. At 17:18 EDT both batch copies hashed `75690c19...` (121,468 bytes) and the
  mirrored script `23c4743d...`, equal to the scratchpad's. 15.2 step 1 records them and has the orchestrator check again before P1a;
  P5 task 9 copies from `docs\imports` (15.5).
- **S-05** (minor) - fixed. Holds: `api_views.trial_balance` (line 769) lists only the (gl, currency) groups present in `gl_balances`.
  Both CDX are bought below their coupon, so their upfronts are received (2345); the TRS and the IRS have none; the options are listed
  (1700); the batch records no ORCL / META ex-date in the window (the last were 10-09 and 09-21). 15.6 item 4 now expects 4451, 4453
  and 2345 and says why 1335 and 4452 are absent.
- **S-06** (minor) - fixed. Holds: batch lines 24-28 carry trade and value date 2026-10-21 with exec 19:45 ET; lines 29-33 trade
  2026-10-22 at 16:00 ET and settle 2026-10-23. 15.6 item 3's terms table now gives each product's side and dates.
- **S-07** (minor) - fixed. Holds: `cash.js` line 172 shows `r.lender` from `accounting.obligations()` (line 1550) through
  `api_views.account_cash` (line 1339), both P3's files. 15.7 item 8 and P3's Part A task-5 list now give `obligations()` to P3, and
  P8-lite walks US Exchange > Cash & financing, whose borrows table joins the wording scope (15.3, 15.6).
- **S-08** (minor) - fixed. Holds: `api.py` serves only `/api/attention` (line 1453), and the summary rides inside `/api/news`
  (`api_products.py` lines 895-911); P1 task 8's test and acceptance 2 call the route, but no task builds it. 15.7 item 1 makes it
  P1a's, with its test.
- **S-09** (minor) - fixed. Holds: `settings.js` lines 582-584 always add the "news" heading and, with `h.news` null (`api.py` line 1260
  once `server.py` lines 629-638 find no `news.py`), "news is off in this runtime". P1a skips the block (15.7 item 5); P1a acceptance 3
  and P8-lite item 4 check Settings and run P1's news-string search on the Dashboard and Settings (15.7 item 9, 15.6).
- **S-10** (minor) - fixed. Holds: C6 probes the `collateral` module, bound in 1.0.5, while the owner's fail / resolve actions live in
  the Part B drawer. The row now probes `blotters.execution_detail` (E1: it ships with the drawer), and in 1.0.5 it says that unfunded
  payables are marked failed and retried while the fail he triggers himself is not in this version yet (15.3 E1, 15.7 item 6).
- **S-11** (minor) - fixed. Holds: the "?" help and Settings > Keyboard follow the registry (P1 task 5). They now hide an action whose
  page is not in the build, so the five new page actions appear only in 1.0.6; P1a's static test and P8-lite's keys check assert it
  (15.7 item 3, 15.6 item 4).
- **S-12** (minor) - fixed. Holds: S0 refuses from 16:00 ET and on an apply-day snapshot, and every dry run starts with S0. New rule 7:
  every real-batch dry run runs between 00:00 and 16:00 ET (report mode at any time), and the steps and hours are scheduled around it
  (15.2, 15.5, 15.9, 15.11, section 5). The alternative, relaxing S0 for dry runs, is not taken: after 16:00 the catch-up would run the
  day's closing jobs without their inputs, so the dry run would no longer predict the apply. Night dry runs raised one more point, also
  in rule 7: L's option close comes from the Cboe chain's previous close, so the apply day's pack is fetched after 09:45 ET, and a
  night pack's option figures are labelled estimates.
- **S-13** (minor) - fixed. Holds. A code-defect postponement now holds phase 2 and ships 1.0.5.1 through the gate first; a data or
  clock postponement lets phase 2 run but keeps at most two other agents from 13:00 ET on the retry day; after a complete apply, a
  failed start or integrity restores the backup (while no owner command has run), and a page defect keeps the import and is fixed as
  1.0.5.1 or in 1.0.6. The critic proposed restoring on any failed check; a page defect is not a reason to take his positions out
  again (rule 3, 15.8 step 8, 15.9 item 1, section 12).
- **S-14** (minor) - fixed. Holds: `server._bind_optional` (lines 490-526) marks a module "error" when a ModuleNotFoundError names
  another module or `bind` raises, a `register` error other than TypeError propagates, and `test_terminal_api` (line 104) asserts that
  none errored. Every new optional module, the importer included, now imports other new modules lazily and keeps its import,
  `register` and `bind` exception-free at every save; `X\wip_import_tests.py` joins P5's owns list and is deleted before the gate
  (15.2 steps 3 and 5, 15.5).
- **S-15** (minor) - fixed. Holds: those hooks have no 1.0.5 consumer, and the fix rounds and P5's first end-to-end run were budgeted at
  the fast end. E3 / E4 move the pending and execution-detail routes, `counts`, the stage vocabulary, C9e, C9f and C16's `summary` /
  `position_requirements` / `custodian_of` to Part B; each Part A package builds its import items first, and P4's tasks 5-6 may slip;
  the fix rounds get 2-3 h; the hours are recomputed from S = 17:30 EDT, and the hourly lines tell the owner that 10-29 is likely once
  step 3 ends after about 03:30 EDT (15.2, 15.3, 15.9, 15.10, 15.11).
- **S-16** (minor) - fixed. Holds: 9.1, 9.6, 9.12, 9.15, Q12 and the batch header contradict this section, and so do 9.5 and section 1's
  live-app facts (same cause, not in the finding). Rule 1 now covers every section and the batch header and lists each superseded
  statement; section 0's line points to it. The batch stays byte-identical, and the owner's summary explains its header (15.1, 15.6
  item 3).

### 15.13 Post-close findings folded into phase 1 (orchestrator, 2026-10-27 17:50 EDT)

A verifier ran the deployed 1.0.4 release after today's close on a copy of the owner's database with his five OTC trades booked at
16:06 ET (Goldman terms). Full report and evidence: `C:\Users\logan\AppData\Local\Temp\claude\C--Users-logan-intelligence-terminal\5e0edd51-72c4-438a-89eb-77c644650257\scratchpad\checks\postclose-1027\` (report in BUILD_LOG 17:47 EDT, "VERIFIER POST-CLOSE OTC"). Integrity
full ok and trial balances balanced throughout; the IRS VM (-2,076.23), the CDX marks and upfronts and the 17:10 collateral marks
recompute by hand. These items are now part of phase 1 because the import makes the owner's TRS / CDX / IRS live from 10-28:

| Id | Severity | What | Owner (phase 1) |
|---|---|---|---|
| POSTCLOSE-01 | major | MARKS_RECORD accepts any print with quote_time >= close - 60 s as the day's CLOSE and never revisits it. On 10-27 it recorded ORCL 130.55 (the 15:59:59 print, fetched 16:16) while the official close is 130.53 (Yahoo regularMarketPrice at 16:04, daily bar 130.52999878, 16:00 minute bar 130.53). SWAP_VM for SWP-000001 then posted NO_CHANGE instead of +774.00 received (-38,700 x (130.53 - 130.55)), the drawer (+774.00) and the positions (0.00) disagree, and TRS resets would settle on the same wrong close (first reset 2026-11-27). 25 open CLOSE_REVISED items 10-22..10-26 show it is routine. Fix: the recorded CLOSE of an equity is the official closing print (the regular-session close: Yahoo regularMarketPrice / the daily bar once the session is closed and the print is at or after 16:00, never a pre-16:00 print), recorded only after it is available (retry, not record early); a CLOSE_REVISED found before the day's 17:05 VM re-records the mark; TRS VM / resets read only the official close. Hand-worked money test with the ORCL numbers. | P3 Part A (lifecycle_eod.py MarksRecord / _revision) with P4 Part A (swaps_trs.py close_on / _vm_close / mark_trs; swaps.py h_vm) |
| POSTCLOSE-02 | minor | Same root cause: B-000002 (C) collateral marked on 122.68 (16:00:00 print) instead of the official 122.69 (difference 122.40), and a one-tick revision is never disclosed (_revision skips diff <= tick). Fixed by POSTCLOSE-01's rule. | P3 Part A |
| POSTCLOSE-03 | minor | CDX CLOSE_PENDING raises 2 WARNs every business evening (the ':r1' retry in pass 2 of the same tick is not quiet) and retries every 5 minutes all night; the wording says "close / intraday price" for a FRED observation and shows raw series codes. Make the first pending evening quiet as the docstring says, retry at a sane cadence, and word it for a daily FRED observation ("ICE BofA IG OAS for 10-27 not published yet"). | P4 Part A (swaps.py _vm_pending / _skip) |
| POSTCLOSE-06 | minor | One command, two business dates: the 17:05 SWAP_VM entry is dated 10-27 while its funding sweep (X-000020) and the 17:10 collateral marks "of 10-27" are dated 10-28 (the 17:00 ET ACCT_DAY cut-off), so the PB pays VM from 0.00 settled cash on 10-27 and the funding shows on 10-28. A lifecycle job of business date D stamps its entries and its funding sweeps with D. | P6 Part A (Treasury stamping, C15) with P4 |
| POSTCLOSE-07 | minor | The CDX upfront shows as today's realised P&L (+288,810.81) against unrealised -295,032.55 on trades opened today, and the portfolio footer's gross short (6,786,915.05) does not tie to the NAV block's mv_short (6,491,882.50). The upfront reclassification is already P4 Part A task 7 (GL 2345); the footer must use the same short definition as NAV. | P4 Part A (task 7) with P3 Part A (portfolio_view footer) |
| POSTCLOSE-04 | minor | GET /api/accounts/F1:PB_GS positions_count counts v_positions only: the PB header says 0 positions while it holds 5 OTC contracts. | P3 Part A if trivial (api_views.account_detail), else phase 2 |
| POSTCLOSE-05 | minor | Cash page sweep "for" column reads "swapvm swapvm (SWP-000003 2026-10-27)" and links to Transactions; CDX- contracts never get the OTC-tab link; the stored memo is raw. | phase 2 (P6 Part B cash.js) unless trivial |
| POSTCLOSE-08 | minor | Wording: "unchanged vs the last VM" with no VM yet; "the mark at the last variation margin" on never-margined contracts; "reset every 1 m"; the VM memo ends with the job code "(SWAP_VM; ...)". | P4 Part A if trivial, else phase 2 |

### 15.14 Phase-2 timing: no midnight wait (orchestrator, 2026-10-28 10:10 EDT; binding for P8, its re-verify rounds and the fixers)

The owner's import was APPLIED to his live database on 2026-10-28 09:57 ET (BUILD_LOG). On 1.0.6 the real batch is therefore a no-op, and
rule 7's 00:00-16:00 ET window must not idle the build:
- P8 task 5 and every phase-2 re-verify check the real batch with the importer's REPORT mode (read-only, allowed at any time) on a fresh
  post-import copy of the live database: lines 1-23 preserved, lines 24-33 already_imported, 34 declined, 35 unresolved, nothing posted,
  integrity ok. That is the 1.0.6 acceptance of task 5.
- A real-batch DRY RUN is run in addition only if the builder reaches it before 16:00 ET; never wait for the 00:00 window.
- Everything else of 15.9 stands (the item 7 comparison of the owner's 33 positions, restated NAV versions and ledger between 1.0.5 and 1.0.6
  on a post-import copy; the fix-forward rule; new jobs start forward).

### 15.15 Owner report: the OTC forwards and cross-currency swaps are bad (orchestrator, 2026-10-28 17:35 EDT; binding for P8, its re-verify rounds and the fixers)

The owner, using the live 1.0.5 prime-broker OTC tab, verbatim: "OTC market is bad for forwards and cross currecny stuff". Phase 2 treats this
as an acceptance area of its own:
- P8 walks, in a real browser on a fresh post-import owner-DB copy served with REAL delayed data (not --offline), every forward and
  cross-currency flow of the PB OTC tab as the owner would use them, in 1.0.6: OTC FX forwards (every quoted currency against USD and the
  crosses, tenors ON..1Y and a broken date), OTC equity forwards, and cross-currency swaps (each currency the desk offers, both notionals,
  fixed / floating legs, the deal FX rate): the ticket's fields and defaults, the preview (deal rate or rate vs spot and forward points by
  covered interest parity with each rate's source and date; the XCCY legs, notional exchanges, basis, accruals), the RFQ quote / accept,
  the booking, the contracts table and drawer, the positions, Risk and the 17:05 jobs where they can be checked now.
- Every problem is a defect in P8's list (critical = a trade impossible or money wrong; major = a feature broken, missing, misleading or
  unusable; minor = polish), with evidence and screenshots; the fixer fixes them in 1.0.6. A price that cannot be produced from free data is
  shown as unavailable with its reason, never invented.

#### 15.15b The orchestrator's OTC audit (2026-10-28 18:20 EDT) and who fixes what

An independent auditor walked the PB OTC forwards and cross-currency swaps on copies of the owner's database with real data, in 1.0.5 and in the
1.0.6 tree: 23 findings (4 critical, 14 major, 5 minor), complete with evidence in `docs\OTC_FX_XCCY_AUDIT_2026-10-28.json` (read it in full).
- The 1.0.6 fixers fix the LOCAL ones now, each with a test: OTC-01 (refuse to price a forward or XCCY leg off a rate older than a few business
  days - CHF is on a 2024-03-01 rate - and show each rate's age on the ticket), OTC-02 (open OTC FX forwards count in currency exposure and the
  FX scenarios until delivery), OTC-03 (the XCCY start / initial exchange is on the spot date, never T+0 or the previous business day after the
  17:00 roll; no false overdraft), OTC-07 (no 2W in the tenor list unless the server prices it), OTC-18 (positions and the drawer show a forward's
  direction and both legs), OTC-23 (the RFQ row and Accept dialog state the notional, the bought / sold currencies and the value date).
- The product redesign belongs to the FX / cross-currency build right after 1.0.6 (`docs\FX_CCS_PLAN.md`, release 1.0.6.1 / 1.0.7): OTC-04 and
  OTC-05 (per-currency term curves and basis instead of one flat overnight rate), OTC-06 (per-currency settlement calendars), OTC-08 (the PB spread
  by tenor and size), OTC-09 (two-currency rate risk), OTC-10 / OTC-11 (exposure and IM treatment of forwards), OTC-12 / OTC-13 (equity forward
  financing and discrete dividends), OTC-14 / OTC-15 / OTC-16 / OTC-19 (float / float XCCY, conventions, FX-move VM on the notionals), OTC-17,
  OTC-20, OTC-21, OTC-22. 1.0.6 must not start that redesign (it would be redone).

#### 15.15c The OTC audit after the FX / cross-currency design (amends 15.15b; FX_CCS_PLAN rev 2 section 1.5)

Release 1.0.6.1 (`docs\FX_CCS_PLAN.md` rev 2, plus `docs\INSTITUTIONAL_V3_PLAN.md` 6.1 R-A..R-E) fixes, for the USD / EUR / GBP pairs and the
EUR/USD ESTR / SOFR float / float swap: OTC-01 (curve and overnight-print age limits), OTC-02 (forwards counted once in currency exposure),
OTC-03 (float / float on spot dates), OTC-04 / OTC-05 (one term curve per currency, basis levels), OTC-06 (TARGET2, New York, London and SIFMA
calendars; Tokyo via R-A), OTC-07 (2W priced), OTC-08 in part (the ticket shows spot, spot date, points and the outright), OTC-09 (rate risk per
currency), OTC-10 (PB OTC FX forwards leave the headline), OTC-14 (EUR / USD float / float), OTC-15 in part (EURUSD shown in market convention),
OTC-16 (VM on the CSA exposure), OTC-17 (delivery at 00:30 ET on the value date), OTC-19 in part, OTC-20 in part and OTC-22 in part.

Deferred to 1.0.6.2, each with the reason the owner reads in his FX report (FX_CCS_PLAN 10.7):
- the FX desk's own forwards move onto the same curves (17.1 pips apart from the PB desk at 1Y EURUSD in the example);
- OTC-08: the PB spread by tenor and size (a dealer-terms redesign for every pair);
- OTC-11: netting and risk-based IM (they change the IM of every open OTC contract and need their own acceptance; the GS percentages stay);
- OTC-12 (first item: equity forwards are financed at SOFR + 1.25 % both ways, a money defect; no equity forward is open in the owner's books)
  and OTC-13 (discrete dividends need a dividend schedule source);
- OTC-14 / OTC-19: GBP SONIA legs, JPY / CHF / CAD / AUD legs, fixed / float, per-currency fixed-leg conventions, non-USD pairs, 7Y-30Y in the UI;
- OTC-15: other pairs' display and whole-unit JPY; OTC-20: pruning the pair list and NZD / SEK / NOK rates; OTC-21: ACT/365 for JPY / CAD /
  AUD (it moves the legacy prices of three products); OTC-22: the dealt-currency selector, FX swaps and NDFs.
- Pairs with a currency outside USD / EUR / GBP (and JPY once R-A lands) keep the pre-1.0.6.1 method on the PB desk, labelled "LEGACY METHOD -
  LIMITED", up to 1 year, under 1.0.6's rate-age limit.
