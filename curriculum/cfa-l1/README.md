# CFA Math (Level I) — authoring spec for the 90 lessons (SPEC §0e)

**Reader.** Logan, sitting CFA Level I in the May 2027 window (11–17 May 2027). His words: *"lots of
math i need to understand, know/memorize, and by understand i mean totally. Use stories and confirm
I understand… have me walk through a problem on each with the calculator."* He reads on his phone
from a Telegram link, early in the morning, and does not read long walls of text ("Lowkey not going
to read these"). So: short paragraphs, the story kept tight, and most of the 20 minutes spent
*doing* — the calculator walkthrough and the graded questions.

**Files.** Plan: `curriculum/cfa-l1.json` (one entry per day). Page skeleton:
`curriculum/cfa-l1/template.html` (copy it; keep everything outside the content exactly). Keystrokes:
`curriculum/cfa-l1/calculator-guide.md` (every keystroke on a page must agree with it). Output:
`curriculum/cfa-l1/day-NN.html` (two digits). Number check: `curriculum/cfa-l1/verify/day-NN.py`.

## 1. Non-negotiables

1. **Every number is computed, never typed from memory.** Write `verify/day-NN.py` (plain Python 3,
   standard library only) that computes every number that appears on the page — story arithmetic,
   worked example, every calculator display, every question's answer and every multiple-choice
   distractor — and prints them. Copy the numbers from its output. The verifier re-runs it.
2. **CFA conventions.** Use the 2027 Level I curriculum's terms, symbols and formulas (e.g. sample
   variance divides by n − 1; the money-weighted return is the IRR; the BA II Plus is the exam
   calculator). Where prep providers and the curriculum differ, follow the curriculum. Name the
   module in the track line and the colophon.
3. **The story is true.** Real people, dates, places and numbers, as verified in the plan's
   `story.sources`. Do not add facts the plan does not support; if a detail is uncertain, leave it
   out or hedge it ("about", "reportedly"). A traditional tale is told as one ("the story goes").
4. **Understand totally.** "Why it works" builds the formula from something simpler (usually an
   earlier day's result), one step per line, so nothing has to be taken on trust. Say what each
   symbol means in words. Then say what the formula *does* in a sentence a 12-year-old could follow.
5. **Both calculators, every walkthrough.** Each step has a BA II Plus line and a TI-84 Plus line
   (template markup). When the TI-84 has a function the BA II Plus lacks (normal and t
   probabilities, hypothesis tests), show the TI-84 function and give the exam method for the BA
   II Plus (table value or memorized critical value) on the BA line.
6. **No answers before the questions.** The check questions use different numbers from the worked
   example and walkthrough, so working them proves he can do it, not that he can copy it.
7. **Plain HTML, the template's classes only.** No external links except to the course page and the
   status page already in the template; no images; no inline styles; no new scripts.

## 2. The page, in order (about 1,400–1,900 words of reading)

| Part | What it does | Budget |
|---|---|---|
| Header | h1 = the headline (story energy, under ~110 characters); dateline `__DATELINE__ · 20 min`; one-sentence standfirst | — |
| Track line | subject = the CFA topic area; progress `Day N of 90 · <Module> · <concept>` | — |
| Warm-up | Days 2–90: two graded questions (`w1`, `w2`) on the plan's `warmup_from` days, each labelled "From Day X". Short: one should need the calculator or a formula from memory. Day 1 has no warm-up. | 2 questions |
| Story | `h3` with the story title, then the story ending on the puzzle (from the plan) | 150–280 words |
| The idea | the concept in plain words, intuition first, an everyday picture if it helps | 120–220 words |
| Why it works | the derivation, step by step, then the `.formula` plate with every symbol in its `dl` | 150–350 words |
| Memorize | `.sie-block.memorize`: the plan's `memorize` cards as a list (formula or fact, exactly as on the cards) | 2–6 lines |
| Worked example | `.worked` box, by hand, each step on its own line | 80–180 words |
| On your calculator | `.calc`: the problem, then 3–7 steps, each with a `calc-what`, a BA II Plus line, a TI-84 Plus line, and a `calc-see` display line where a number appears; ends with `calc-answer` | — |
| Back to the story | how the idea resolves the puzzle, with the story's own numbers where they work | 80–160 words |
| Exam traps | `.sie-block.traps`: 2–4 mistakes the exam is built to catch | 2–4 bullets |
| Check your understanding | five graded questions, below | 5 questions |
| Score box | the template's `.cfa-score` div, unchanged | — |
| Colophon | Curriculum (2027 Level I, module name), Sources (the story's), Method (template text) | — |
| Nav | Previous = `./day-(N−1).html` (Day 1: disabled); Course page; Next stays disabled (the release links it) | — |

## 3. The five check questions

- **c1 — Your turn, on the calculator** (`num`): the walkthrough's task with different numbers.
  Hint = which inputs change. Solution = the keystrokes on **both** calculators and the display.
- **c2 — Why** (`mc`): a conceptual question that only someone who understood "Why it works" gets
  right (e.g. "What happens to the PV if the rate rises, and why?").
- **c3 — Twist** (`num`): the concept applied one step differently (solve for a different unknown,
  a different frequency, two steps chained). Hint and full solution.
- **c4 — Exam style** (`mc`): a CFA-style item with three options (A, B, C). The two wrong options
  are what the most common mistakes produce (wrong P/Y, BGN vs END, n vs n − 1, forgetting to
  annualize, using the wrong rate…), each with its reason.
- **c5 — Say it back** (`explain`): one or two sentences in his own words; the model answer plus the
  2–3 points a complete answer must contain.

Multiple choice: exactly three options; vary which letter is right across the course; every option
gets a `cq-why` that says why it is right or exactly which mistake produces it.

Number answers: `data-answer` is a plain number at full precision from `verify/day-NN.py`;
`data-tol` is the absolute tolerance that accepts a correct answer typed at the precision the
question asks for (e.g. "to the nearest cent" → `0.01`; "as a percent to two decimals" → `0.01`
with `data-pct`; when reasonable rounding of intermediate steps moves the answer, widen the
tolerance to cover it and no more). State the precision and the unit in the question. Use
`data-signless` when only the size matters (a payment, a PV the question calls "how much").
`data-show` is the answer as a person would write it (`1,628.89`).

Each question's solution shows the work; for calculator questions it gives the BA II Plus and the
TI-84 Plus keystrokes.

## 4. Review days and the final stretch

Review days (`kind: "review"` in the plan) have no story or new concept. They open with a one-paragraph
"what this week was about", a compact formula recap (the week's memorize cards), then **ten** graded
questions (`c1`–`c10`, mixed `num` and `mc`, at least four needing the calculator, all CFA-style, all
new numbers), then the score box. `kind: "exam"` days follow the plan's own instructions.

## 5. report-meta and the index entry

```
"date": "__DATE__", "slot": "cfa", "title": <the h1 text>,
"path": "reports/cfa/l1/day-NN.html", "summary": <one sentence: what Logan learns and computes>,
"headlines": [
  "Day N of 90 · <Topic area>: <the concept, one clause>",
  "Calculator: <what he computes>, on the BA II Plus and the TI-84",
  "Check yourself: 5 questions graded on the page"      (review days: "Review: 10 questions graded on the page")
],
"reading_minutes": 20, "generated_at": "__GENERATED_AT__", "timezone": "America/New_York"
```
`<title>` is `<h1 text> — CFA Math, Day N of 90 · Logan’s Daily Newspaper`.

## 6. Before you hand it over

Run `python3 curriculum/cfa-l1/verify/day-NN.py` (it must print every number), then substitute the
placeholders into a temporary copy named `2026-11-16-cfa.html` and run
`python3 .github/scripts/check_report.py` on it: it must print `OK`.
