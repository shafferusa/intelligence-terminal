# CFA Morning Lesson — run procedure (slot `cfa1`, every day 4:50 AM ET)

Read CLAUDE.md and `prompts/shared-rules.md` §0b (the shell rule), §1, §11–15 first. This file
overrides them only where it says so. Of `docs/SPEC.md`, only §0f (this slot) applies; you need not
read the rest. Skip every `date` command those files mention (shared-rules §1.4's `RUN_START`
included): `today.json` carries the date and `now_et`, and `finish` records the times.

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
   built from a full reading of the 2027 books (when they disagree, Step 4 says which wins). Never
   invent a page, a quotation, a score or a statistic, and never invent what he did wrong: his
   replies say what he got wrong only when they say how. If something you want to teach is not in
   the brief, teach the general principle without a page reference, or leave it out.
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

1. `python3 .github/scripts/cfa1.py today --out /tmp/cfa1/today.json`, then Read that file. Its
   `date` is `TODAY` (America/New_York); no `date` command is needed.
2. Decide the mode from it:
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

**Record only what he said.** A missed question number tells you which topic he missed (the
brief's question list labels every question), not what he did wrong. Log it as that topic plus
"working not reported", never as a guessed error; in the lesson, re-teach the idea the question
tests and ask for his working ("CFA note: Q7, I ..."). Only a reply that says how he went wrong
("I divided by the CPI") gives you a mistake to name and correct as his. A score with no misses
named ("5/7") is recorded as it is: no misconception, and the recall intro asks which ones he
missed. A reply that repeats, word for word, the sample reply printed in a lesson's checklist is
probably the sample pasted unchanged: record nothing from it (list it under `noted`) and ask him.

Write `/tmp/cfa1/feedback.json` with the Write tool and apply it with
`python3 .github/scripts/cfa1.py feedback --apply /tmp/cfa1/feedback.json`:

```json
{"processed_through": <highest update_id you read>,
 "sessions": [{"date_studied": "YYYY-MM-DD", "module": "PRE-ECON-LM03", "status": "done|partial|not_started",
   "minutes": 130, "score": "6/8", "missed": ["Q7", "Q21"], "stopped_at": "printed p.110",
   "notes": "what he said, briefly", "source_update_ids": [123]}],
 "module_status": [{"module": "PRE-ECON-LM03", "status": "done|partial|not_started",
   "understanding": "solid|shaky|unknown", "evidence": "6/8 (75%); missed Q7 and Q21",
   "stopped_at": "printed p.110", "source_update_ids": [123]}],
 "misconceptions_add": [{"module": "PRE-ECON-LM03",
   "item": "missed Q7 (real growth from nominal growth and the deflator); working not reported",
   "correction": "the idea Q7 tests, in a sentence, from the brief", "taught": true,
   "source_update_ids": [123]}],
 "misconceptions_resolve": [{"id": 4, "evidence": "got a deflator question right on 11/20", "source_update_ids": [130]}],
 "misconceptions_taught": [4],
 "noted": [{"update_id": 124, "why": "CFA note: context for today, no progress change"}],
 "control": null}
```

Every reply up to `processed_through` must be cited by a change or listed in `noted` with the
reason it changes nothing; `feedback` refuses a file that silently skips one.

- `date_studied`: the date of the plan day that assigned the module (`yesterday_assignment`, or
  the `backlog` item), unless he says when he studied it.
- `understanding`: "solid" when his score is at least 75% (6/8 counts) and no miss repeats a topic
  already in `open_misconceptions`; "shaky" when it is below 75% or he says something confused
  him; "unknown" when he gave no score.
- `misconceptions_add`: one item per missed question or stated confusion, with
  `"taught": true` when today's lesson teaches its correction (the usual case).
  `misconceptions_taught` is only for earlier items (ids from `open_misconceptions`) that today's
  lesson re-teaches. `misconceptions_resolve` needs evidence from a reply: a later right answer on
  the same idea, or his saying it is clear now.

A "CFA pause" message → `"control": {"paused": true, "note": "...", "source_update_ids": [..]}`;
"CFA resume" → `paused: false` plus a plan re-dating in Step 3. A "CFA note" is context for today's
lesson. If `feedback` prints NOT APPLIED, fix the file and re-run it; never bypass it. **Then run
Step 1's `today` command again and re-read `/tmp/cfa1/today.json`**: progress, the backlog and the
open misconceptions have changed.

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

**When the files disagree.** For pages and for Close / Standard / Skim, the brief wins (it was
checked against the full reading notes), then `modules.json`, then the plan. A section whose plan
priority is null takes the brief's. Where only a PDF page is known, cite it as "PDF n".

**Queued page.** If `queued_page` is set, that lesson was written and verified in advance. Run
`python3 .github/scripts/cfa1.py start-page`: it copies the page to `page.file` and its lesson
record (`queued_record`) to `/tmp/cfa1/record.json`. If a reply arrived since it was written,
change only the one-line recall intro ("Module 2 is logged as done") and add a Correction block
per Step 5, and say in the record's `run_note` what was applied. `finish` sets the timestamp, the
reading time and the Previous link. Then go to Step 6.

## Step 5 — Write the lesson

Run `python3 .github/scripts/cfa1.py start-page` (it creates the month folder and copies
`curriculum/cfa1/lesson-template.html` to `page.file`), then replace every placeholder: `finish`
refuses a page with any template text left. Use only the template's classes, plus the `calc`
keystroke classes Lesson 1 uses for BA II Plus steps. Lesson 1
(`state/cfa1/queue/2026-11-16-cfa1.html`) shows every block done well: match its tone and density.
It runs to about 1,800 words only because of the one-time calculator setup; other study days stay
within the length below.

**Title and meta.** h1 = a specific, plain headline about tonight's idea (no "Lesson N" in it);
report-meta `title` = the same headline. `finish` builds the `<title>` element and the dateline
from them. `headlines` = exactly three: `Lesson N · <course>: tonight <module title> (printed
pp.A–B)`, the single most useful idea or correction, and the practice line. Track line:
`Lesson N · Module M of T · <module title> · D days to the exam`. Take `<course>`, M and T from
`modules_info` in today.json. pp.A–B in the headline is the whole module, from its first page to
its last solutions page, as in Lesson 1; the Tonight box splits it into reading, problems and
solutions.

**The blocks, in order (study days):**
1. *Tonight* box: book title, Learning Module number and title, printed and PDF page span, problem
   and solution pages, planned time.
2. *Correction from your reply* — only when a reply reported a miss or a confusion. When he said
   how he went wrong: what was wrong, the right idea, why, directly ("That's wrong: ..."). When he
   only listed missed questions: the idea each one tests and the right reasoning, in a sentence or
   two, then ask for his working; never guess what he did. Short. Record these per Step 2
   (`"taught": true`, or `misconceptions_taught` for earlier items).
3. *Recall first, closed book*: 3–4 questions on yesterday's assigned material (its principles,
   vocabulary, formulas, and any mistake he reported), then 1–2 spaced-review questions on OLDER
   material (say which lesson or module each comes from). Take them from the first source that has
   something: `spaced_review` (lessons 3, 7, 14, 28 and 56 days back), `older_misconceptions`,
   `shaky_modules` (from the module's brief), a module logged done in `progress_modules`, Lesson 1's
   calculator checks. Today's new misconceptions belong to the Correction block, not here. Answers,
   with printed pages, in the `details.check` after the questions. If yesterday's module is not
   logged, say so in one line ("No reply logged yet for Module 3"), never that he did not reply:
   replies can reach the repo hours late, and the next lesson applies them. Still ask the questions
   (they are retrieval practice).
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
   outcomes, weighted to the Close sections; if the module has fewer, assign them all), checking
   against the Solutions with a one-line rule for each miss, and the reply line. Make the sample
   reply in the reply line differ from any reply he has actually sent.

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
`python3 .github/scripts/reading_minutes.py <page>`, which prints minutes then words). Every block
above fits in that only if each stays inside its budget, so draft to it from the start instead of
trimming afterwards: Tonight box 50 words; Correction 80; Recall questions and answers 220 (one
line per question, one or two per answer); principles with the worked example 330; formula box,
key terms, Memorize and Exam traps together 220; reading plan 140; checklist 60. Cut repetition
before outcomes. `finish` refuses a study page over 1,250 words (other days: 900) unless you pass
`--long-ok "why"`, which is only for a page where cutting would drop an examinable outcome.
Lesson 1 is the only longer one.

**Numbers.** Write every calculation in `/tmp/cfa1/calc.py` (standard library only), run it, and copy
the printed results. Check every page reference against the brief or plan before you finish.

## Step 6 — Record, check, publish

1. The lesson record `/tmp/cfa1/record.json` (for a queued page, `start-page` already copied it:
   update `run_note`, and `plan_changes` if Step 3 changed the plan):
   `{"lesson": N, "date": "TODAY", "kind": "...", "modules": ["..."], "concepts": ["..."],
   "vocab": ["..."], "formulas": ["..."], "recall": [{"q": "...", "a": "...", "ref": "printed p.X"}],
   "assigned": {"reading": "printed pp.A–B", "practice": ["Q4", "Q7"]}, "plan_changes": null,
   "run_note": "one or two sentences for the status page"}`.
   On study days `recall` holds 3–5 good retrieval questions on TONIGHT's material (with answers
   and printed pages): tomorrow's lesson and later spaced reviews draw on them. Other days may
   leave it empty.
2. `python3 .github/scripts/cfa1.py finish --page <page.file> --record /tmp/cfa1/record.json`.
   It first sets what a script knows exactly: the dateline (date and "N min read"), the `<title>`
   element, `reading_minutes`, `generated_at` and the Previous link (the last CFA lesson; Next
   stays disabled). Then it runs `check_report.py` and the lesson checks (no template text left,
   h1 = report-meta title, lesson number, Module M of T, days to the exam, a Correction block
   naming every miss logged today as taught, the length, a complete record with a `ref` on every
   recall item), and only then writes the index entry, the lesson record, last-run and the run-log
   line. On `PAGE FAIL`, fix what it lists and run it again.
3. `python3 .github/scripts/publish.py --subject "learning: TODAY cfa1 lesson N"` (exit 3: resolve per
   §15.2, then `--push-only`). ONE commit for everything.
4. `python3 .github/scripts/api_get.py --wait-200 <page.url>`.

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
