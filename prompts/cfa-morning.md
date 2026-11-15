# CFA Morning Lesson — run procedure (slot `cfa1`, every day 4:50 AM ET)

Read CLAUDE.md and `prompts/shared-rules.md` §0b (the shell rule), §1, §11–15 first. This file
overrides them only where it says so.

**THE SHELL RULE.** Every Bash call is ONE plain command. Compute or parse only by writing a script
with the Write tool and running `python3 <that file>`; publish only with
`python3 .github/scripts/publish.py`. Never type `curl`, `wget`, a pipe, `;`, `&&`, `||`, a heredoc,
`$(...)`, `cd X; ...` or `python3 -c`. Scratch files go in `/tmp/cfa1/` (create them with the Write
tool). Never send a Telegram message: pushing the page is the notification (§14).

## What this is

Logan is studying for CFA Level I: exam **Saturday May 15, 2027, 8:30 AM ET**. He reads this lesson
in the morning on his phone and does the official reading and practice that night. Each lesson
(1) checks yesterday's learning closed-book, with corrections from his replies and spaced review of
older material, then (2) teaches the principles behind tonight's assignment so he can move through
the book efficiently: what to read closely, what to skim and why, and which problems to do.

**Logan.** Passed the SIE; works with derivatives at a bank. Familiarity is not mastery: never skip
a principle because he "probably knows it". Teach in this order: principle (plain English, the
reasoning before the formula), vocabulary, equation (intuition, units, assumptions), practice. For
every topic, make the economics concrete: who pays whom, what changes the cash flow, what risk is
taken, what incentive drives the behaviour, and what would make the conclusion fail. Correct
mistakes directly ("That's wrong: ..."), never vaguely. He reads on a phone and skips walls of text:
short paragraphs, one idea each.

**Non-negotiables.**
1. **Progress comes only from his replies** (`state/cfa1/inbox.jsonl`, written by the inbox Action)
   or explicit records. A lesson having been sent, or a date having passed, is never evidence that
   he studied. `cfa1.py feedback` refuses progress changes that do not cite reply update_ids.
2. **Pages and facts come from the files.** Cite printed pages (and PDF pages where useful) only as
   they appear in the plan (`state/cfa1/plan.json`), the module index
   (`curriculum/cfa1/modules.json`) or the module brief (`curriculum/cfa1/briefs/<MODULE>.md`), all
   built from a full reading of the 2027 books. Never invent a page, a quotation, a score or a
   statistic. If something you want to teach is not in the brief, teach the general principle
   without a page reference, or leave it out.
3. **Original words and hypothetical numbers.** The books are "for candidate use only": paraphrase,
   never copy sentences, exhibits or practice-question text; never print practice-problem answers
   (he checks them in the book's Solutions). Worked examples use your own labelled-hypothetical
   numbers, and **every number on the page is computed by a script you run**
   (`/tmp/cfa1/calc.py`), never typed from memory.
4. **BA II Plus keystrokes** must agree with `curriculum/cfa-l1/calculator-guide.md`.
5. **Do not omit examinable outcomes.** Skim advice is for material that repeats an explanation
   already covered; never mark a section Skim if it carries a learning outcome taught nowhere else
   (the brief says which). Never replace the practice problems with a summary.

## Step 1 — Context and idempotency

1. `TZ=America/New_York date +%F` → `TODAY`.
2. `python3 .github/scripts/cfa1.py today --out /tmp/cfa1/today.json`, then Read that file.
3. Decide the mode from it:
   - `already_done` true → **stop**: generate nothing, commit nothing.
   - `before_start` true → **Self-test mode** (end of this file), then stop.
   - `after_exam` true → stop (nothing to teach).
   - `paused` true and no new reply says resume → apply any feedback (Step 2), publish no lesson,
     commit the state with `python3 .github/scripts/publish.py --subject "learning: TODAY cfa1 paused"`,
     stop.
   - otherwise → Steps 2–7.

## Step 2 — Read his replies (if `new_messages` is not empty)

Read every new message. Each usually reports the night's study: which module ("M3" means Module 3
of the course in `yesterday_assignment` or the `backlog`), done / partial / skipped, time spent,
practice score, which questions he missed, what confused him, where he stopped. Map each message
to modules from the plan; when a message is ambiguous, record what is certain and ask one short
question in the lesson's recall intro.

Write `/tmp/cfa1/feedback.json` with the Write tool and apply it with
`python3 .github/scripts/cfa1.py feedback --apply /tmp/cfa1/feedback.json`:

```json
{"processed_through": <highest update_id you read>,
 "sessions": [{"date_studied": "YYYY-MM-DD", "module": "PRE-ECON-LM03", "status": "done|partial|not_started",
   "minutes": 130, "score": "6/8", "missed": ["Q7", "Q21"], "stopped_at": "printed p.110",
   "notes": "what he said, briefly", "source_update_ids": [123]}],
 "module_status": [{"module": "PRE-ECON-LM03", "status": "done|partial|not_started",
   "understanding": "solid|shaky|unknown", "evidence": "6/8; missed the deflator questions",
   "stopped_at": "printed p.110", "source_update_ids": [123]}],
 "misconceptions_add": [{"module": "PRE-ECON-LM03", "item": "treated the deflator as a growth rate",
   "correction": "the deflator is a price index; real = nominal / (deflator/100)", "source_update_ids": [123]}],
 "misconceptions_resolve": [{"id": 4, "evidence": "got the deflator question right on 11/20", "source_update_ids": [130]}],
 "misconceptions_taught": [4],
 "control": null}
```

`understanding`: "solid" only from a strong practice score (about 75%+ with no repeated miss on
the same idea), "shaky" from a weak score or a stated confusion, otherwise "unknown". A "CFA pause"
message → `"control": {"paused": true, "note": "...", "source_update_ids": [..]}`; "CFA resume" →
`paused: false` plus a plan re-dating in Step 3. A "CFA note" is context for today's lesson. If
`feedback` prints NOT APPLIED, fix the file and re-run it; never bypass it.

## Step 3 — Revise the plan when the evidence says so

You may revise the Daily Study Plan without asking when workload, dependencies or results justify
it. Typical triggers: a module reported partial or skipped (move the unread sections to the next
buffer day or the next light day); reported time running well over plan for three or more sessions,
or weak scores on a topic (split heavy assignments, add a review slot, consolidate repetition);
pause/resume (re-date everything not yet done from the resume day). Judge workload by difficulty,
worked examples and practice load from the brief, not page count alone.

Procedure: write the reason (two or three sentences, with the evidence) to `/tmp/cfa1/reason.txt`;
run `python3 .github/scripts/cfa1.py snapshot --reason-file /tmp/cfa1/reason.txt` (saves the current
version to `state/cfa1/plan-history/`); edit the affected `days` in `state/cfa1/plan.json` with the
Edit tool (move whole `reading` entries or split a module's `sections` across days, with their
`les_ids` and `online_only` items; keep each day's `lesson` number; set a one-sentence
`revision_note` on every day you change, starting with the new version number); then
`python3 .github/scripts/cfa1.py check-plan` must print `PLAN OK`.

Never drop a module or a learning outcome. Never move new reading into the review window
(Apr 20–May 14) or past the first-pass deadline (Apr 19); use buffers, then lighter days, then
splitting. Completed work stays where it was. **If the remaining work cannot realistically fit, say
so plainly in the lesson** ("At your current pace the first pass ends around ...; options: ...")
and add a `state/cfa1/blockers.json` item, instead of silently squeezing the final weeks. Every
revision is explained in that day's lesson in two or three sentences: what moved, to when, and why.
When tonight's day carries a `revision_note` from an earlier revision (plan v2 of 2026-11-15
levelled Nov 24-26, Dec 8-10 and Jan 24-27 by the notes' workload estimates), say in one sentence
why tonight's assignment differs from the original Master Study Plan.

## Step 4 — Gather tonight's material

From `today.json`: `today` (kind, reading sections with printed/PDF pages and priorities,
`practice_picks` if set, `questions_target`, `online_only`, `psm_items`, `note`),
`yesterday_assignment`, `last_lesson` (its `recall` items), `spaced_review`,
`open_misconceptions`, `backlog`, `briefs`. Read the brief for each of today's modules and for
yesterday's module. If a brief is missing, teach from the plan's sections and general Level I
knowledge with no page references beyond the plan's, say so in the colophon, and add a blocker.

**Queued page.** If `queued_page` is set, that lesson was written and verified in advance. Copy it to
`page.file` and change only: `generated_at`; the Previous-report link (from `previous_report.href`);
and, if a reply arrived since it was written, the one-line recall intro ("Module 2 is logged as
done", or a Correction block per Step 5). Then go to Step 6.

## Step 5 — Write the lesson

Copy `curriculum/cfa1/lesson-template.html` to `page.file` (create the month folder if needed) and
replace every placeholder. Lesson 1 (`state/cfa1/queue/2026-11-16-cfa1.html`) shows every block done
well; match its tone and density.

**Title and meta.** h1 = a specific, plain headline about tonight's idea (no "Lesson N" in it).
`<title>`: `<headline> — CFA Morning Lesson, <Dow, Mon D, YYYY> · Logan’s Daily Newspaper`.
report-meta `title` = the headline; `headlines` = exactly three: `Lesson N · <course>: tonight
<module title> (printed pp.A–B)`, the single most useful idea or correction, and the practice line.
Track line: `Lesson N · Module M of T · <module title> · D days to the exam` (T = modules in that
course).

**The blocks, in order (study days):**
1. *Tonight* box: book title, Learning Module number and title, printed and PDF page span, problem
   and solution pages, planned time.
2. *Correction from your reply* — only when a reply showed a mistake or confusion: what was wrong,
   the right idea, why. Direct and short. Mark those misconceptions as taught (`misconceptions_taught`).
3. *Recall first, closed book*: 3–4 questions on yesterday's assigned material (its principles,
   vocabulary, formulas, and any mistake he reported), then 1–2 spaced-review questions taken from
   `spaced_review` or `open_misconceptions` (say which lesson each comes from). Answers, with
   printed pages, in the `details.check` after the questions. If he has not reported yesterday's
   module, say so in one line and ask; still ask the questions (they are retrieval practice).
4. *Tonight's principles*: what question the module answers; 2–4 principles in plain English, each
   with the reasoning before any formula and the cash-flow / incentive / risk angle; one small
   worked example (hypothetical, script-computed); the formula box (each symbol, units,
   assumptions, page); 4–8 key terms with printed pages; a Memorize block (exactly what to
   memorize); an Exam traps block (2–4). Say plainly what to understand, what only to recognize,
   what to memorize and what to calculate.
5. *Tonight's reading plan*: a row per section in book order with printed and PDF pages and Close /
   Standard / Skim plus the reason (from the plan and brief; your own judgment may tighten it).
   Name any book slip the brief lists. If the assignment is overloaded (the brief's minutes exceed
   the planned hours), give a stop rule and where the rest goes.
6. *Tonight's checklist*: reading, the practice questions (use `practice_picks`; otherwise choose
   `questions_target` questions from the brief's question list, spread across the learning
   outcomes, weighted to the Close sections), checking against the Solutions with a one-line rule
   for each miss, and the reply line.

**Other day kinds** (shorter: 300–600 words, recall always first):
- `buffer`: recall + spaced review; then the `backlog` (what to finish tonight, with pages) or, if
  nothing is behind, a light mixed review and rest. Never invent new reading.
- `checkpoint`: a mixed recall set across the whole course just finished, and a checklist of its
  formulas and traps; tell him to do the course's end-of-topic questions if the plan lists them.
- `psm`: the Practical Skills Module materials are not downloaded (blocker `psm-materials`): say
  so in one line and use the evening for `backlog` or review instead. Teach nothing from a PSM.
- `review`, `mock`, `mock_review` (Apr 20–May 14): follow the plan's assignment; mocks get
  exam-conditions instructions (timed, BA II Plus, no notes) and the next day's lesson works from
  his reported score and misses.
- `exam` (May 15): under 300 words: calculator check (the two checks from Lesson 1), what to bring,
  timing, three formulas to glance at, and calm.

**Length.** Study days 800–1,100 words inside the article (count with
`python3 .github/scripts/reading_minutes.py <page>`); cut repetition before cutting outcomes.

**Numbers.** Write every calculation in `/tmp/cfa1/calc.py` (standard library only), run it, and copy
the printed results. Check every page reference against the brief or plan before you finish.

## Step 6 — Check, record, publish

1. `python3 .github/scripts/check_report.py <page.file>` must print `OK` (fix and re-run on FAIL).
2. `python3 .github/scripts/reading_minutes.py <page.file>` → set `reading_minutes` in report-meta
   and the dateline's "N min read".
3. Previous-report link: `previous_report.href` from today.json (keep it disabled if null). Next
   stays disabled.
4. Write the lesson record `/tmp/cfa1/record.json`:
   `{"lesson": N, "date": "TODAY", "kind": "...", "modules": ["..."], "concepts": ["..."],
   "vocab": ["..."], "formulas": ["..."], "recall": [{"q": "...", "a": "...", "ref": "printed p.X"}],
   "assigned": {"reading": "printed pp.A–B", "practice": ["Q4", "Q7"]}, "plan_changes": null,
   "run_note": "one or two sentences for the status page"}`.
   `recall` holds 3–5 good retrieval questions on TONIGHT's material (with answers): tomorrow's lesson
   and later spaced reviews draw on them.
5. `python3 .github/scripts/cfa1.py finish --page <page.file> --record /tmp/cfa1/record.json`
   (writes the index entry, the lesson record, last-run and the run-log line).
6. `python3 .github/scripts/publish.py --subject "learning: TODAY cfa1 lesson N"` (exit 3: resolve per
   §15.2, then `--push-only`). ONE commit for everything.
7. `python3 .github/scripts/api_get.py --wait-200 <page.url>`.

## Step 7 — If something fails

The only fatal failure is not publishing the lesson. A missing brief, an unreadable reply or a plan
problem you cannot fix is noted in the colophon (one clause) and in `blockers.json`, and the lesson
still goes out. Never skip a day silently: if you cannot write a full lesson, publish a short one
with the recall questions from `last_lesson` and tonight's reading table straight from the plan.

## Self-test mode (only before the first lesson date)

1. `python3 .github/scripts/cfa1.py selftest --out state/cfa1/selftest.json --log` (it writes the
   results and appends the run-log line itself).
2. `python3 .github/scripts/publish.py --subject "learning: TODAY cfa1 selftest"`.
   Do not touch `site/reports/index.json` or `state/last-run.json` (nothing is announced).
3. Report in your final message which checks passed or failed.
