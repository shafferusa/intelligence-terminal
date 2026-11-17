# CFA Morning Lesson — run procedure (slot `cfa1`, every day 4:50 AM ET)

Read CLAUDE.md and `prompts/shared-rules.md` §0b (the shell rule), §1, §11–15 first. This file
overrides them only where it says so. Of `docs/SPEC.md`, only §0f (this slot) applies. Skip every
`date` command those files mention: `today.json` carries the date and `now_et`, and `finish`
records the times.

**THE SHELL RULE.** Every Bash call is ONE plain command. Compute or parse only by writing a script
with the Write tool and running `python3 <that file>`; publish only with
`python3 .github/scripts/publish.py`. Never type `curl`, `wget`, a pipe, `;`, `&&`, `||`, a heredoc,
`$(...)`, `cd X; ...` or `python3 -c`. Scratch files go in `/tmp/cfa1/`. Never send a Telegram
message: pushing the page is the notification (§14).

## What this is

Logan is studying for CFA Level I: exam **Saturday May 15, 2027, 8:30 AM ET**. He reads this page
in the morning on his phone and does the official study that night. The page is an actual reading
in two parts: (1) a refresher on his most recent completed studying, (2) a self-contained teaching
of today's essential material from the assigned official sections, then the references. No
quizzes, no problem sets, no homework, no substantial worked calculations.

**Logan.** Passed the SIE; works with derivatives. Explain unfamiliar ideas plainly, and never skip a
principle because he "probably knows it". Teach the principle first (the reasoning, why it works),
then the terms, then the equation (each variable and its units). Make it concrete: who pays whom,
what changes the cash flow, what incentive drives behaviour, which assumption it rests on, and
what would make it fail. Short paragraphs, one idea each.

**His study workbook is the progress record.** He keeps `CFA_Level_I_Master_Study_Plan.xlsx` on
his PC and writes free-form nightly notes in it. A task on his PC sends it here encrypted
(`state/cfa1/workbook.enc`), and `cfa1.py` opens it with the key that the routine's prompt has you
write to `/tmp/cfa1/workbook.key` first (or `CFA1_WORKBOOK_KEY`, if set). Never print the key.

**Non-negotiables.**
1. **Progress only from the workbook's status fields**, never from a lesson having been sent or a
   date having passed. His notes tell you what he did and what confused him, but mentioning a topic
   is not mastery, and finding it confusing does not mean he left it unfinished. Separate what the
   workbook confirms from what you infer, and label inferences as such. If notes and statuses
   disagree, say that it is unclear; never silently pick one.
2. **His notes are private.** They are in `/tmp/cfa1/notes.json` (his wording, untouched) and
   nowhere else. Reason from his exact words. Never quote them, paraphrase them closely or describe
   anything personal on the page, in the record or in any commit; publish only the learning points
   the lesson needs (a confusing idea becomes a "Clearing up" explanation). `finish` refuses a page
   or record that repeats eight words in a row from his notes. Never copy `/tmp/cfa1/` files into
   the repository, and never print or write the workbook key.
3. **Pages and facts come from the files.** Cite printed and PDF pages only as they appear in
   `state/cfa1/plan.json`, `curriculum/cfa1/modules.json`, the module brief
   (`curriculum/cfa1/briefs/<MODULE>.md`) or `curriculum/cfa1/exhibits.json`. Never invent a page,
   an exhibit, a quotation or a claim. If something you want to teach is not in the brief, teach the
   general principle without a page reference, or leave it out. `finish` refuses page numbers outside
   the modules the record names and exhibits that do not exist.
4. **Original words.** The books are "for candidate use only": paraphrase, never copy sentences,
   exhibit data or practice-question text.
5. **BA II Plus keystrokes**, if any, must agree with `curriculum/cfa-l1/calculator-guide.md`. Only
   Lesson 1 carries the calculator setup.

## Step 1 — Context

1. `python3 .github/scripts/cfa1.py today --out /tmp/cfa1/today.json`, then Read that file. Its
   `date` is `TODAY` (America/New_York).
2. Decide the mode:
   - `already_done` true → **stop**: generate nothing, commit nothing.
   - `before_start` true → **Self-test mode** (end of this file), then stop.
   - `after_exam` true → stop.
   - `paused` true → publish nothing; stop.
   - otherwise → Steps 2–6.

## Step 2 — Read the workbook

`today.json` → `workbook`. If `available` is false, the page says so in one line ("Your workbook
could not be read this morning (reason); progress below is as last confirmed on DATE.") and uses
`workbook.last_confirmed` (the statuses copied at the last lesson); invent no completion.

If available, Read `/tmp/cfa1/notes.json` (his recent notes and marks, last 14 days: the Daily
Plan's nightly notes and the Lesson Checklist's "Your notes", where he also writes). From the
facts in `today.json` (`sessions_recent`, `last_done_session`, `refresher`, `behind`, `flags`,
`modules_started`, `changed_since_last_lesson`) and his notes, work out:
- what he explicitly finished (a session marked Done, checklist items Done);
- where he stopped, if his notes say;
- what confused him (his notes; "Needs work" or "Needs review" marks);
- whether the workload is too much (notes saying he ran out of time, actual hours well above the
  plan, or sessions left In progress).

If `changed_since_last_lesson` is false, nothing new was synced since the last lesson: say so in one
line ("No workbook update since yesterday's lesson; last saved DATE TIME") and use the latest
confirmed progress. Telegram is optional and carries no notes: the inbox keeps only the ids of
"CFA" messages (the repository is public), so ignore `new_telegram_messages`.

## Step 3 — Adjust the plan only when the evidence says so

You may move work without asking when his notes or statuses show an assignment was too much or left
unfinished. Use the buffers first (Friday buffer days), then lighter days; never move new reading
into Apr 20–May 14 or past Apr 19; never drop a module; completed work stays where it was.
Procedure: write the reason with its evidence to `/tmp/cfa1/reason.txt`; run
`python3 .github/scripts/cfa1.py snapshot --reason-file /tmp/cfa1/reason.txt` (keeps the prior
plan in `state/cfa1/plan-history/`); edit the affected `days` in `state/cfa1/plan.json` with the
Edit tool (move `reading` entries or split a module's `sections`, with their `les_ids`; keep each
day's `lesson` number; set a one-sentence `revision_note` on every changed day); then
`python3 .github/scripts/cfa1.py check-plan` must print `PLAN OK`. Explain the change in one or
two sentences in "Where you are" (what moved, to when, why), and note that his workbook's Daily
Plan rows still show the original assignment, which he may edit. Record it in the record's
`plan_changes`. Do not rebalance the plan without evidence from his workbook.

## Step 4 — Write the reading

`python3 .github/scripts/cfa1.py start-page` creates the page (`page.file`) and, for a queued
lesson, copies its record to `/tmp/cfa1/record.json`.

**Queued lesson** (`queued_page` set, Lesson 1). It was written and checked in advance. Change only:
- the `WORKBOOK FACTS` line in "Where you are": one to three short lines on what the workbook
  confirms (and any interpretation or conflict, labelled), per Step 2;
- if the workbook shows Module 2 finished (Nov 15 session Done, or its checklist complete), run
  `python3 .github/scripts/cfa1.py use-refresher --page <page.file> --record /tmp/cfa1/record.json --module PRE-ECON-LM02`
  so Part 1 refreshes Module 2 instead of Module 1;
- if his notes show confusion about the refreshed module, a short "Clearing up" box at the end of
  Part 1 (the template shows it);
- the record's `workbook` (`updated` / `unchanged` / `unavailable`) and `run_note`.
Then go to Step 5.

**Any other day.** Replace every placeholder in the template (`finish` refuses template text). Lesson 1
(`state/cfa1/queue/2026-11-16-cfa1.html`) shows the blocks done well: match its tone and density.
Read the brief for today's modules and for the refresher module; consult `modules.json` and
`exhibits.json` for pages. Do not re-read whole books.

- **Title and meta.** h1 = a plain headline about today's idea; report-meta `title` = the same.
  `headlines`: `Lesson N · <course>: <module title> (printed pp.A–B)`, `Refresher: <topic>`, and the
  single most useful idea. Track line: `Lesson N · Module M of T · <module title> · D days to the
  exam` (course, M and T from `modules_info`).
- **Today's plan notes.** `today.revision_note` (set when the plan moved this day's work) goes in
  "Where you are" in one sentence: what moved, from when, why. `today.note` is guidance for this
  lesson (a stop rule for a heavy day, what an earlier lesson already covered): follow it. When an
  earlier lesson (`last_lesson`, or `lessons.json`) already taught today's module or refresher
  topic, build on it: link that lesson once and teach what it left out instead of repeating it.
- **Where you are** (box): the workbook facts per Step 2; tonight's assignment in one line (module
  and pages); any plan change. If the assignment looks heavy, one sentence on where to stop and
  where the rest can go.
- **1 · Refresher: <topic>** — the most recent completed studying: `workbook.refresher` (yesterday
  if marked Done, else the latest confirmed); when it lists `sections`, refresh only those. Never
  refresh material the workbook does not show finished; if nothing is confirmed yet, refresh the
  prerequisite ideas today builds on and say so.
  Key principles, vocabulary and formulas, with a short Memorize list. If his notes show confusion,
  add "Clearing up": the idea explained another way, in your own words.
- **2 · Today: <topic>** — the essential content of today's assigned sections, self-contained: he
  should learn the ideas from your explanation without opening the book to understand you. Not an
  outline, not a list of topics, not page instructions. Principles before terms before equations;
  why each relationship holds; the relevant cash flows, incentives, assumptions and exceptions;
  each formula in words with every variable and its units. One brief everyday picture if it helps,
  with no calculation. Cut repetition, keep every learning outcome (the brief lists them).
- **References** — the book, module, sections with printed and PDF pages (a table), then "Worth
  opening separately": exhibits (number, caption, printed and PDF page from `exhibits.json`) and
  detailed passages worth reading in the book, and any book slip the brief lists. Say "printed"
  and "PDF" explicitly; printed pages are the book's own numbers, PDF pages the file's.

**Other day kinds** (300–600 words): `buffer` — refresher, then teach whatever `workbook.behind`
shows unfinished (from its brief) or a light review; `checkpoint`, `review`, `mock`, `mock_review`
— refresher, then the plan's assignment explained (what the review covers and the key ideas to
have ready), no question sets of our own; `psm` — the Practical Skills Module materials are not
downloaded: say so in one line in "Where you are", keep the slot, teach nothing from the PSM, and
use Part 2 for anything `behind` or a short review; `exam` (May 15) — under 300 words: the two
calculator checks from Lesson 1, what to bring, timing, calm.

**Length.** The reading (Where you are, Part 1 and Part 2; References not counted) aims for
500–800 words. Keep an essential explanation rather than cut it to fit: `finish` refuses a study
page over 1,000 words unless you pass `--long-ok "why"` (up to 1,300).

## Step 5 — Record, check, publish

1. The record `/tmp/cfa1/record.json` (a queued lesson's is already there):
   `{"lesson": N, "date": "TODAY", "kind": "...", "modules": ["today's modules"],
   "refresher_modules": ["..."], "cited_modules": [], "workbook": "updated|unchanged|unavailable",
   "topics": ["..."], "refresher_topics": ["..."], "references": {"book": "...", "module": M,
   "printed": "A–B", "pdf": "C–D"}, "plan_changes": null, "run_note": "one or two sentences"}`.
   `cited_modules` lists any other module whose pages you cite. `modules` must equal the plan's
   reading for a study day unless `plan_changes` says why. The record is public: no note text.
2. `python3 .github/scripts/cfa1.py finish --page <page.file> --record /tmp/cfa1/record.json`.
   It stamps the dateline, title, reading time and Previous link, checks the page (template text,
   the two parts, the references, every page number and exhibit, no copied note text, the length,
   the record and its `workbook` value against the sync), and only then records the index entry,
   the lesson, progress (statuses only) and the run log. On `PAGE FAIL`, fix what it lists and run
   it again.
3. `python3 .github/scripts/publish.py --subject "learning: TODAY cfa1 lesson N"` (exit 3: resolve
   per §15.2, then `--push-only`). ONE commit for everything.
4. `python3 .github/scripts/api_get.py --wait-200 <page.url>`.
5. Your final message (it stays in this private session, never in the repository): each note you
   read, quoted exactly, with what you concluded from it and what you left uncertain.

## Step 6 — If something fails

The only fatal failure is not publishing a lesson. A missing brief or an unreadable workbook is
stated in one clause on the page and in `state/cfa1/blockers.json`, and the lesson still goes out.
If you cannot write a full lesson, publish a short one: the refresher from the last lesson's
topics and today's references straight from the plan.

## Self-test mode (only before the first lesson date)

1. `python3 .github/scripts/cfa1.py selftest --out state/cfa1/selftest.json --log`.
2. Rehearse Lesson 1 without publishing it:
   `python3 .github/scripts/cfa1.py today --date 2026-11-16 --out /tmp/cfa1/today.json`, Read it
   and `/tmp/cfa1/notes.json`, then
   `python3 .github/scripts/cfa1.py start-page --date 2026-11-16 --page-out /tmp/cfa1/selftest/2026-11-16-cfa1.html --record-out /tmp/cfa1/record.json`,
   do Step 4's queued-lesson changes on that copy, and run
   `python3 .github/scripts/cfa1.py finish --dry-run --date 2026-11-16 --page /tmp/cfa1/selftest/2026-11-16-cfa1.html --record /tmp/cfa1/record.json`.
   It must print `DRY RUN OK`.
3. `python3 .github/scripts/publish.py --subject "learning: TODAY cfa1 selftest"` (only
   `selftest.json` and the run-log line change; nothing is announced).
4. Report in your final message which checks passed, what the workbook showed (counts only), and
   the rehearsal's result.
