# Post-reboot report — 2026-09-22

**Scope.** What the post-reboot session found, changed and measured, in the
order the owner's handoff prescribed. Facts carry their source. Recommendations
are labelled. Nothing here is a decision on any replay. D3 and the v5 freeze
were decided by the owner later the same evening and are recorded in 5a, 6
and 9; sections 1 to 8 otherwise describe the state before that decision.

Companion records written the same day:
`D3-EVIDENCE-2026-09-22.md`, `V4-GOVERNANCE-AUDIT-2026-09-22.md`,
`STREAMING-REPLAY-DESIGN-2026-09-22.md` (each produced by three independent
finders, one synthesizer and three adversarial verifiers; verifier notes are
appended to each).

---

## 1. Machine state after the reboot (measured first, before anything else)

| item | value |
|---|---|
| last boot | 2026-09-22 19:09:26 local |
| free disk C: | 13.85 GiB at first read (was ~4.3 GiB before the reboot) |
| RAM | 7.45 GiB total; 0.28 GiB free at first read, 1.6–1.7 GiB free once the finsim server was trimmed |
| pagefile.sys | 8,002,048,000 B on disk (7.45 GiB; was ~11.87 GiB), system-managed, NOT changed |
| pagefile in use / peak since boot | 995 MB → 1,380 MB / 1,545 MB |
| commit charge | 13.1–13.4 GB of a 14.9 GB limit, at idle |
| main PIT store | 9,632,415,744 B, mtime 2026-09-21 16:07:48 — byte-identical (size, mtime, head+tail SHA re-checked after every run) |
| pilot DB | 171,016,192 B before repair; 171,040,768 B after (one row's `summary_json` grew) |
| WAL / SHM | both stores: WAL 0 B, SHM 32 KiB; the store's SHM mtime moves on every read-only open, as expected |
| git | root `C:\Users\logan\shafferfineval-app`, branch `claude/market-signal-engine-328vyy`, 127 dirty paths (6 modified, 121 untracked) before the checkpoint |

Two machine facts worth knowing. The finsim game server autostarts from the
Startup folder (`FinSim Server.vbs`) and grew from 773 MiB to 1,104 MiB
in the ten minutes after boot before the OS trimmed it. The scheduled task
"ShafferFinEval Daily Refresh" (16:30 ET daily) writes only the 4 MB v1
`shafferfineval.db`; it never opens the PIT store.

**Where the machine's commit actually goes (measured ~20:15 local, after the
host had stopped this session's background runs for system-wide low memory):**

| process | private commit | working set |
|---|---|---|
| `python.exe` — `scripts\serve_local.py` (Mikhail backend, autostarted) | 1,833 MiB | 3 MiB |
| `pythonw.exe` — `finsim serve` (Startup folder) | 1,602 MiB | 154 MiB |
| `python.exe` — `mikhail\engine\maia2_worker.py` (child of the above server) | 1,136 MiB | 4 MiB |
| claude (this session) | 741 MiB | 716 MiB |
| Steam + steamwebhelper | ~0.93 GiB | — |
| Chrome (12 processes) | ~0.88 GiB | — |
| Defender (MsMpEng) | 392 MiB | 328 MiB |

Commit charge 13.07 GB of a 14.9 GB limit; three autostarted Python services
alone hold ~4.5 GiB, almost entirely paged out. No replay or harness process
was running at that moment. This is the strongest evidence this session has
about the 2026-09-21 incident: the machine's commit is dominated by resident
services that have nothing to do with the replay, and the replay engine itself
measures at ~245 MiB (§5). It is inference, not proof, that the same services
drove the pre-reboot pagefile from ~7.5 to 11.87 GiB; the pre-reboot
per-process figures were not recorded. These are the owner's services; nothing
was stopped or changed.

## 2. Contradictions between the handoff and the repository

Reported, not resolved, per the working rule. *State as of the first cut
(~20:15 local). Superseded the same evening: `spec_freeze_v5` is live, the
engine derives from `factor_spec_v4`, computes 6 of 13, and `validate()`
checks `FACTOR_DIRECTION_V1` against the registry; see 5a and 9. Items 1 and
2 are kept as written because they are the evidence of what the handoff
got wrong.*

1. **v4 already existed.** The handoff asked for a successor freeze v4. The
   repository has `spec_freeze_v4` live (`pit_frozen_spec.py`, digest
   `912268b3…`, 47 components, `verify()` intact, `0 problems / PASS` when
   run this session) and `docs/MODEL-LINEAGE.md` records D1 (margin reading),
   D2 (directions) and the corrected P/E eligibility as folded into it on
   2026-09-22. v3 is sealed with reason `DIGEST_COVERED_VERSION_STRINGS_NOT_BODIES`.
   The handoff is stale on the freeze version; no freeze was edited.
2. **"v4 required" is met by the specification and open for the engine.** The
   freeze module's own `STILL_BLOCKED` says "SPECIFICATION complete as of
   spec_freeze_v4; ENGINE not": `pit_replay.py` still derives eligibility from
   `factor_spec_v2`, refuses `ebitda_benchmark` as ambiguous although v3 decided
   MARGIN, computes 5 of 13 factors, and never reads `FACTOR_DIRECTION_V1`.
   Phase 3 is real work, not glue (see the v4 audit §3 and §5.1).
3. **The checklist's repair timestamp was local time with a Z suffix.**
   `docs/POST-REBOOT-CHECKLIST.md` §3 proposed `finished_at='2026-09-21T20:05:08Z'`;
   the meter report gives the floor breach as `2026-09-22T00:05:08+00:00` and the
   run end as `2026-09-22T00:12:26+00:00`. The UTC values were used.
4. **The recorded root cause of the 2026-09-21 memory incident is not supported
   by measurement** (§5 below). `pit_frozen_spec.STILL_BLOCKED`,
   `full_run_capacity_budget.json` (lever 4) and `pilot_sizing_derived.json`
   (known defect 4) all say the whole-cross-section row lists "forced the
   pagefile to grow" by 5.9 GB. The engine process, measured directly, commits
   ~245 MiB at 4,000 targets with the full 7,102-entity index resident.
5. Smaller ones, all in the companion records: "nine BODIES" is 7 bodies +
   2 version strings; `PILLARS` is mislabelled as the v2 block set; the
   `COMPONENTS` comment says "repr" while the code hashes canonical JSON; the
   engine's refusal text says the spec "sets `normalization` to None" (no such
   field) and "EPS IS NOT IN THIS STORE" (888,486 rows say otherwise); the
   design record's 34% and the sourced 37.8% for EBITDA ≤ 0 disagree.

## 3. Lineage and state actions taken

- **Checkpoint commit `2e9db18`** on the v2 branch before any edit: 121 files,
  excluding databases, WAL/SHM and the downloaded option snapshots under
  `pit_archive/options/`. Not pushed. The checkout had no git identity; the
  owner's name and e-mail were passed on the command line, not written to config.
- **Pilot run row repaired** (checklist §3, once free space > 7.0 GiB):
  `status='ABORTED_RESOURCE_GUARD'`, `finished_at='2026-09-22T00:12:26+00:00'`,
  `n_dates=2`, `n_entities=15234`, `invalidated_reason` naming the breach time,
  the stage, the free space and that no 2022-06-30 row was written; the four
  requested dates and the checklist's timestamp error are preserved in
  `summary_json.repair`. WAL checkpointed with `(0, 0, 0)` and removed on close.
- **No replay launched. Main store never opened for writing.**

## 4. Streaming write side — implemented and proven equal

`pit_replay.run_date` now flushes its three row lists every `batch_size`
targets (default `DEFAULT_BATCH_SIZE = 500`): compute → write → commit → clear.
The per-entity arithmetic is untouched. `batch_size=None` keeps the old
single-write shape for the oracle. Also in the same change, all glue:

- an engine-side free-space check BEFORE every batch write (so the floor
  prevents a write rather than reporting one), raising `FreeSpaceAbort` with a
  `.detail` dict of exactly what was committed; the driver-side guard in
  `run_sizing_pilot.py` is unchanged and now fires second;
- one transaction per batch in production mode (a target never has feature rows
  committed without its pillar rows); per-table commits only under the meter's
  scopes, with exclusivity verified on the first batch of each date only;
- `run_pilot` finalises the `pit_replay_run` row on completion, on a mid-date
  breach (`aborted_free_space`, with `partial_date`) and on any other exception
  (`aborted_exception`, with the per-date statistics kept in `summary_json`) —
  the zombie `running` row of the first pilot cannot recur from this path (a
  hard kill still can; noted in the design record as R5);
- row accounting closes at the end of every date (13 feature and 4 pillar rows
  per target, at most one score row, every target written) or the run stops;
- `batch_full` / `write_batch` ticks, `n_targets_written`, per-batch WAL peak in
  the stats; the meter's row double-count fixed at the call site.

**Equality.** `test_pit_replay.py --oracle` (new test 10b): 50 targets,
`batch_size=None` versus `batch_size=7` (8 batches) — every row of
`pit_feature`, `pit_pillar_score`, `pit_score` identical with ids and
`created_at` excluded, every per-date statistic identical; 154 checks, 0
failures. The fast suite passes (132) before and after the engine tightening;
`pit_replay.py --validate` PASS; freeze digest unchanged (`pit_replay` is not a
freeze component, which the v4 audit flags as governance hole B).

**HEAD arm.** The design review pointed out the test's "legacy" arm is the new
code with batching off. A HEAD arm (the engine loaded from commit `2e9db18`)
was run on the same slice: HEAD versus the new code unbatched — 31/31 checks
pass (row counts, symmetric `EXCEPT` differences empty, key-level presence,
ordered-content SHA-256 per table, run-row totals, every per-date statistic;
`oracle_head_vs_worktree.json`). With the in-suite proof that unbatched equals
batch-7, the streamed path equals the checkpoint engine by transitivity. The
direct HEAD-versus-S7, S25 (exact multiple) and SM (meter on) arms were
interrupted: the host stopped the session's background commands for
system-wide low memory (§1). Not rerun on the session's own initiative.

## 5. Memory measurements (`pit_replay_rss.py`)

One date (2019-06-28), one child process per run, counters read with
`GetProcessMemoryInfo`, primary metric = private commit. All numbers are
MiB; "fixed" = commit at the `assemble` tick (index loaded, primitives
resolved, index cleared); "write side" = peak minus fixed.

| targets | mode | entities indexed | peak commit | fixed | write side | batches | rows |
|---|---|---|---|---|---|---|---|
| 50 | stream 7 | 3,728 | ≈136 (WS) | 129 | 6 | 8 | 650 / 200 / 25 |
| 50 | unbatched | 3,728 | ≈136 (WS) | 129 | 7 | 1 | same |
| 1,000 | stream 500 | 6,926 | 207 (WS) | 194 | 12 | 2 | 13,000 / 4,000 / 461 |
| 1,000 | unbatched | 6,926 | 209 (WS) | 194 | 14 | 1 | same |
| 4,000 | stream 500 | 7,102 | 242 | 190 | 52 | 8 | 52,000 / 16,000 / 1,786 |
| 4,000 | unbatched | 7,102 | 244 | 191 | 53 | 1 | same |

(The 50 and 1k rows were taken with harness 1.0, which reported working set as
its headline; the commit series recorded at every tick tells the same story.
Harness 1.1 spawns the base interpreter — the venv `python.exe` is a launcher
stub whose PID is not the engine's, so 1.0's parent-side peak measured the
stub; every peak quoted above is the child's own counter.)

What the series show, with the per-tick commit values:

- **The once-per-date cost dominates and is already the full cross-section at
  1k targets.** Cohort membership pulls 6,926 of ~7,100 entities into the fact
  index for 1,000 targets. Loading it moves commit from ~44 to ~195 MiB; clearing
  it returns nothing to the OS.
- **The row lists cost almost nothing on top, in either mode.** Unbatched at
  4k, with all 69,786 row tuples assembled and not yet written, commit is
  195.0 MiB against 190.8 at `assemble` (4.2 MB; 1.3 MB at 1k) — about 1 KB
  per target, against the ~22 KB per target a `sys.getsizeof` estimate gives,
  because the arenas freed by the index absorb the lists.
- **What grows during the write is the same in both modes and tracks bytes
  written**: +49 MiB in one step unbatched, +6.3 MiB per batch of 500 streamed
  (197.7 → 241.9 across eight batches). Both land at 242–244 MiB. This matches
  the writer's SQLite page cache (`cache_size = -64000`, 64 MiB cap) filling
  with the ~50 MB of pages written, not Python accumulation. **Attribution
  not yet tested**: the 4k streamed run with a 2 MiB page cache (harness
  `--cache-kib 2000`, a runtime, diagnostic-only override) was stopped by the
  host mid-run for system-wide low memory. Until it runs, "page cache" is the
  best-fitting hypothesis, not a measurement; the lazily filled cohort cache
  is the alternative.
- **Therefore the invariant "write-side memory = O(batch)" is true of the row
  lists and cannot be seen in the process peak at this scale**, because the
  peak is set by the fact index and the page cache, neither of which streaming
  touches. Streaming is correct hygiene for a full 8k date (an estimated
  ~0.19 GB of tuples unbatched) and for WAL size; it is not what would have
  saved the 2026-09-21 pilot.
- **The 5.9 GB is not explained by this engine.** Extrapolating the unbatched
  path to a full 8,132-target date gives roughly 300–350 MiB of process commit.
  The pilot's "5,923,065,856 B non-database transient" was a free-space delta
  on the volume coincident with the assemble window, not a per-process
  measurement. What consumed it is not identified by this session. Candidates,
  labelled as candidates: another process (the finsim server grew ~35 MiB/min
  after boot; Chrome, Steam and Edge were resident), or something in the
  meter-on path (`measure=True`, `per_table_scopes=True`, 5 Hz sampling,
  `dbstat`) that the harness's default `measure=False` does not exercise, or
  something specific to the incident date: every harness run so far is on
  2019-06-28, and 2022-06-30 — the date whose assemble window coincided with
  the growth — has never been measured.
  **Not yet run**: the two-date, meter-on 1k arm (harness `--measure --dates
  2019-06-28 2022-06-30`) that would test cross-date accumulation, the meter's
  own footprint and the incident date itself. It was queued behind the
  diagnostic run and not started after the host's memory stop.

**Runs stopped by the host, 2026-09-22 ~20:15 local.** Claude Code terminated
the two background commands then running (the 2 MiB-cache diagnostic and the
HEAD-arm oracle's S7 arm) because the machine was critically low on memory.
The stop is about the machine, not the commands: at that moment the replay
child was ~250 MiB and the oracle process ~130 MiB, against ~4.5 GiB of
commit held by autostarted services (§1). Harness temp files were removed;
the H and L oracle databases were kept. To finish the measurement programme
when the owner decides the machine is ready (recommendation: with the Mikhail
backend, finsim server and Steam not running, and in this order):

```
python pit_replay_rss.py --limits 4000 --modes stream --cache-kib 2000 --out rss_report_4k_stream_diag_cache2m.json
python pit_replay_rss.py --limits 1000 --modes stream --measure --dates 2019-06-28 2022-06-30 --out rss_report_1k_twodate_measure.json
python <scratch>/oracle_head.py S7 S25 SM        # HEAD DB already on disk
```

Guards during every run: free disk ≥ 7.0 GiB (13.7 GiB throughout), available
RAM ≥ 300 MiB and available commit ≥ 1 GiB (harness 1.1 terminates the child
otherwise; never triggered). 8k was not run: on these numbers an 8k streamed
child would be ~300 MiB and the measurement is low-risk, but a full date is the
scale of the four-date pilot, which the handoff withholds. Owner's call.

## 5a. Incident record — 2026-09-21 pagefile exhaustion (owner ruling, 2026-09-22)

**Previous explanation: REJECTED_BY_MEASUREMENT.** The explanation recorded in
`pit_frozen_spec.STILL_BLOCKED`, `full_run_capacity_budget.json` (lever 4) and
`pilot_sizing_derived.json` (known defect 4) — that the engine's whole-cross-
section row lists forced the pagefile to grow by 5.9 GB — is withdrawn. The
engine measures at ~245 MiB of commit at 4,000 targets with the full index
resident, and its row lists at ~1 KB per target. The machine was operating
near its commit ceiling because of resident services (§1: ~4.5 GiB in three
autostarted Python processes, ~1.8 GiB in Steam and Chrome, 13.1 of 14.9 GB
committed with nothing of ours running). The replay was at most the marginal
trigger. The measurement artefacts are left as written (they are evidence of
what was believed and why); the live narrative in the freeze module is
corrected with the v5 cut, and `docs/MODEL-LINEAGE.md` carries the ruling.

The streaming patch stays as engineering: its equality proofs are strong and
it bounds the row lists and the WAL. It is not credited with fixing the
incident, because it did not cause it.

Controlled measurement plan (owner's order): pause the three local Python
services (all bind 127.0.0.1; the Cloudflare tunnel and watchdog tasks are
disabled and no tunnel process exists, so no public site is served from this
machine), leave Chrome and Steam, record per-process commit before and after,
then rerun the 1k/4k pairs, the 2 MiB-cache attribution, the two-date
meter-on run including 2022-06-30, and the remaining oracle arms.

## 5b. Controlled measurements with the three local services PAUSED (2026-09-23)

Owner authorisation of 2026-09-23. The three LOCAL Python services (Mikhail
`serve_local.py`, `maia2_worker.py`, the FinSim server) were identified by PID
and command line and stopped for the window; the remote Mikhail.Live
deployment was not touched. Baseline immediately after the pause
(`rss_baseline_paused_2026-09-23.json`, 11:22:16Z): **8.557 GB committed of a
14.905 GB limit, 1.727 GiB free RAM of 7.452 GiB, 13.655 GiB free disk,
pagefile 211 MB in use against a 1,545 MB peak since boot, and ZERO python
processes on the box.** (That artefact is written with a UTF-8 BOM, so a
strict `json.load(open(...))` needs `encoding="utf-8-sig"`.)

**READ THE ENGINE LABEL BEFORE USING ANY NUMBER BELOW.** Every run in this
section is `pit_replay/1.1` under `spec_freeze_v5` (digest 58722f4c…), the
freeze now sealed as `FREEZE_V5` with `may_execute_replay = False`. That
engine computed six of thirteen keys and had **no market side at all**: no
listing resolution, no price read, no share audit, no TTM ladder, no P/E, no
EV/EBITDA, no leverage ratio. The numbers are therefore a floor for
`pit_replay/1.2`, not an estimate of it. See the closing paragraph.

One child at a time, one date (2019-06-28), commit measured as the child's
private commit charge.

| run | peak | fixed (assemble) | write side | batches | per batch | WAL peak | score rows | elapsed |
|---|---|---|---|---|---|---|---|---|
| 1k streamed (500) | 200.4 MiB | 187.2 | 13.2 | 2 | 0.55 MiB | 7.4 MB | 475 | 276 s |
| 1k unbatched | 200.8 | 187.5 | 0.7 | 1 | 0.70 | 13.0 MB | 475 | 270 s |
| 4k streamed (500) | 241.6 | 189.9 | 51.7 | 8 | 0.20 | 12.5 MB | 1,851 | 310 s |
| 4k unbatched | 244.9 | 190.1 | 4.4 | 1 | 4.40 | 47.1 MB | 1,851 | 321 s |
| 4k streamed, 2 MiB page cache (diagnostic) | 204.2 | 189.7 | 2.2 | 8 | 0.24 | 12.5 MB | 1,851 | 317 s |

Reports: `rss_report_paused_1k_stream.json`, `…_1k_legacy.json`,
`…_4k_stream.json`, `…_4k_legacy.json`, `…_4k_diag_cache2m.json`. The
resident fact index is 144–150 MiB of the ~190 MiB fixed cost.

**Attribution — what the 4k streamed "write side" of 51.7 MiB actually is.**
Capping the pilot writer's SQLite page cache at 2 MiB drops the same run's
write side to **2.2 MiB** and flattens its per-batch growth to
`[0.7, 0.4, 0.3, 0.4, 0.0, 0.1, 0.0, 0.0]`. So roughly 49.5 MiB of the 51.7
is the writer's 64 MiB page cache filling toward its pragma, not rows held in
memory. The cache-capped run is labelled `diagnostic_note` in its own report
and is an ATTRIBUTION arm, not evidence about the configuration that ships.

**The row-side cost, stated correctly.** It is about **0.2–0.25 MiB per
500-target batch and 2.2 MiB for a whole 4,000-target date** — not ~2 MiB per
batch. Unbatched, the same date holds 4.4 MiB of rows in one transaction,
twice the streamed figure, which is what a 4,000-target batch against a
500-target batch should look like. Streaming's real benefit here is the
**WAL**, 12.5 MB against 47.1 MB, a 3.8× reduction; on commit the two shapes
are within ~4 MiB of each other once the page cache is accounted for.
Streaming remains good engineering and still must not be credited with the
2026-09-21 incident (§5a).

**The direct HEAD oracle arms** (`oracle_head_1_1.json`; fresh scratch DBs, 50
targets at 2019-06-28): the committed engine loaded from `git show`
(source sha256 `9940aea4862f7c75`) against the working tree unbatched (L),
batched in 7 (S7), batched in 25 (S25) and batched in 7 under measurement
mode (SM) — **132 checks, 0 failed**, every arm 25 score rows, 115–118 s each.
This closes the "direct H arm never run" note of §9 **for engine 1.1**. Arm H's
own availability tally shows `ebitda_benchmark`, `pe_absolute`, `pe_relative`,
`ev_ebitda_supplement`, `fcf_conversion`, `net_debt_ebitda` and
`debt_market_cap` at 0 of 50 complete: the arms could not and did not exercise
a single V or Q path. The artefact carries no engine version or freeze digest
of its own, so its provenance is this paragraph.

**What this section does NOT establish, and what is therefore still owed.**
Nothing here measures `pit_replay/1.2`. Under v6 the engine gained a per-date
market side that runs over every entity in `needed` — listing resolution, one
valuation price read with a step-back loop, one strict share-class audit, one
TTM ladder walk and one corporate-action gate read per priced entity — plus
four more cohort factors. A 200-target smoke of 1.2 took 396 s where the 1.1
harness did 1,000 targets in 276 s, so the shape of the per-date cost changed,
not merely its size. Before any sizing pilot the 1.2 engine must be measured
with `pit_replay_rss` one child at a time at 1k and 4k (8k only if the
headroom is then obviously safe), and the owner-ordered two-date meter-on arm
including 2022-06-30 (§5) has still never been run.

## 6. D3 — reported; decided by the owner on 2026-09-22 (growth A, acceleration A, coverage OI/interest; see the `spec_freeze_v5` section of docs/MODEL-LINEAGE.md)

Full record: `D3-EVIDENCE-2026-09-22.md` (20 claims, 20/20 citations confirmed;
the refuter refuted only the diff-size particulars of one housekeeping claim).
In one paragraph each:

**ebitda_growth denominator.** A = `(E_t − E_{t−1}) / abs(E_{t−1})`, a rate:
`pit_derive.py:481` (a formula string; the module computes nothing) and the
frozen v1 core `company_scoring.py:592-603, 628-631`, asserted on a fixture by
`test_pit_derive.py:373-375`. B = `(E_t − E_{t−4q}) / Assets_{t−4q}`, an amount:
the registry body `pit_normalization.py:1840-1851`, `pit_coverage.py:566`,
and the ENGINE (`pit_replay.py` `resolve_primitives`, refusing with
`R_NO_ASSETS_LAG1`), asserted by `test_pit_replay.py:558`. The engine computes B
end to end; the frozen SPECS_V3 body and `pit_factor_contract.py:331-332`
still describe A's inputs (no Assets requirement), so cohort eligibility and
computability disagree by construction. Design docs: open question 6 of the
design record poses it, the addendum does not settle it, and the freeze record
cites that open question as "evidence for B" while A carries an authored
rationale — a contradiction, not evidence. No measurement compares the two.
Economic consequence (reasoning, labelled in the record): A is scale-free but
unbounded near a zero base; B is bounded but is ΔEBITDA per dollar of assets,
which favours asset-light firms and correlates with `ebitda_efficiency` in the
same block. **Recommendation (separate):** keep B as the scoring input for now
because it is what is frozen, implemented and tested; carry A as a zero-weight
research column with a declared base policy; reconcile the spec/contract/derive
artefacts to whichever scores; fix the 34%/37.8% record. Any of these except
the code-side reconciliation is a v5 event. Two things the decision must cover
at the same time (completeness critic): `ebitda_acceleration` is entangled —
`pit_derive.py:485-488` defines it as a difference of A-rates while the engine
computes a second difference over the asset base, so the denominator choice
decides two factors, not one; and design-record open question 4 ("which copy
of EBITDA growth survives") is unsettled and adjacent, while C5 caps dual
specifications at three, which a research column for growth-A would exceed.

**interest_coverage numerator.** A = `operating_income / interest_expense`:
`pit_derive.py:554-557`, the SPECS_V3 primitives and eligibility rule
(`pit_factor_spec.py:1049-1064, 586-593`), and the distress flag in
`pit_coverage.py:217-220` (a boolean, not a ratio), plus a fourth authored
artefact the record itself missed: `pit_invariants.py:197-200` declares the
factor "an operating-versus-interest question; no market data". B = `EBITDA /
interest expense`: `pit_coverage.py:578` (a FEATURE_NOTE) and the availability
boolean at `pit_coverage.py:811-812`. The engine computes NEITHER: the key is
`NOT_COMPUTABLE` with reason `no_v2_normalization_declared`, and `Primitives`
has no interest field. v1 authored no numerator (its debt pillar is
net_debt_ebitda + debt_market_cap). No test asserts a numerator; no artefact
measures either. Consequence (reasoning): the two differ by D&A/interest, which
varies within a SIC cohort so the rank moves; B needs the scarcer D&A leaf and
would make three of four Q factors share one EBITDA input; either way
`interest_expense <= 0` needs a declared treatment that the direction
docstring promises and nothing implements. **Recommendation (separate):**
adopt A, declare the missing registry entry (PERCENTILE_RANK, higher is
better), implement the denominator domain rule, correct the B artefacts and the
false refusal prose. A registry entry is a v5 event under either choice.

## 7. Governance findings on v4 (from the audit, mutation-tested)

Proven by editing a scratch copy and recomputing the digest:

| mutation | digest moved? |
|---|---|
| drop `revenue` from ebitda_benchmark's spec body | yes |
| flip one direction; change a within-block weight; anchor 20→21; a debt rung; MIN_BLOCK_WEIGHT; PRICED_EPS_V2 body; registry note | yes |
| the 50th/75th percentile band or `min_cohort=3` inside `benchmark_margin_50_75` | **no — hole A** |
| the engine's growth arithmetic (`assets_lag1` → `ebitda_lag1`) | **no — hole B** |
| `pit_derive.py` formula string; docstrings (controls) | no (expected) |

So v4 does digest bodies, as the handoff required, and it digests names and
prose rather than arithmetic: the bar for the model's largest single factor
weight and every executable definition sit outside the digest. Also outside:
the P/E convex transform parameters (only `TRANSFORM_VERSION`), the EPS pair
hierarchy body, `V2_BLOCKS` order. A latent seal-check gap: `validate()`
rejects `unrenderable:` but not `callable:`. `_FROZEN_MANIFEST` re-digests to
`FROZEN_DIGEST` today, but no test asserts it. Recommendations R1–R8 in the
audit are all "as a v5, never in place". *(Closed the same evening in
`spec_freeze_v5`: `validate()` names a `callable:` body and proves
`digest(_FROZEN_MANIFEST) == FROZEN_DIGEST`; R1, R4, R5, R6, R7 and R8
landed; R3 landed as `FORMULAS_V1` with golden witnesses; R2 is the
registry-versus-spec question the V/Q wiring will settle.)*

## 8. Defects found in artefacts (not fixed unless stated)

- `pilot_pristine_manifest.json` is not valid JSON (a stray closing brace on
  its last line). It records the pilot's pristine state; left as found.
- `full_run_capacity_budget.json` is stamped `spec_freeze_v3 / 2f9bba31…`; the
  Phase 6/7 budget must be recomputed under v4 and the refined gate.
- `pilot_sizing_report.json` has `replay_report: null` (the abort discarded
  it); the plan record lives only in the pilot DB's run row.
- `pilot_sizing_derived.json` says free fell to 4.118 GiB; the run log and the
  meter report say 4.132 GiB. Same event, two numbers.
- `pit_replay_manifest.save()` writes valid JSON, so the stray brace in the
  pristine manifest was added by something else; not traced.
- The meter's row double-count (`mark(rows=…)` plus `table_scope`): fixed at
  the engine call site, not in the meter.
- The run row's `code_revision` drops the dirty flag that `git_revision()`
  computes (design record R-note); the pilot's `c9b4050` did not describe the
  code that ran.

## 9. Not done, and why (as of the first report; updated the same evening)

- **Later the same evening:** the owner decided D3 (growth A, acceleration A,
  coverage = operating income over interest) and `spec_freeze_v5` was cut
  (digest 58722f4c…, 61 components; v4 sealed as `FREEZE_V4`, may not execute
  the replay). The engine now derives eligibility from `factor_spec_v4`,
  computes growth A under `EBITDA_GROWTH_BASE_POLICY_V1`, acceleration A and
  interest coverage under `INTEREST_COVERAGE_DOMAIN_V1` (census 6/1/6,
  reachable weight 0.60), and `ENGINE_VERSION` is `pit_replay/1.1`. Full
  record: `docs/MODEL-LINEAGE.md`, `spec_freeze_v5` section. Six items in v5
  await the owner's word and are named in
  `pit_frozen_spec.SCORE_ASSEMBLY_GAP["owner_confirmation_pending_v5"]`: the
  rate band (10.0), the matched-period coverage rule, the refusal vocabulary,
  the PERCENTILE_RANK transform for coverage, the inverted ordering among
  negative-OI rows under that rank, and the single-factor Q availability path
  (E + Q = 0.45 clears the floor).
- Still not built in the engine: the MARGIN benchmark path (D1 decided in the
  spec), the P/E chain, and registered transforms for EV/EBITDA, fcf_conversion,
  net_debt_ebitda and debt_market_cap. That is the "wire full V/Q engine" step.
  GAP 5 (peer floor = max(spec, normaliser)) and GAP 7 (price-basis translation
  constant) remain NOT FIXED (audit v4-16, v4-17); GAP 8 is closed by
  `ENGINE_MUST_DERIVE_FROM`; `FACTOR_DIRECTION_V1` is enforced only as a
  validate-time consistency check against the registry, not read in the
  scoring path.
- The controlled memory measurements wait on the service pause (§5a).
- Four-date pilot rerun, full replay, 8k measurement: not run.
- `ENGINE_VERSION` was left at `pit_replay/1.0` in the first cut (the HEAD-arm
  oracle passed only transitively: H = L in full, L = S7 in the suite) and
  bumped to `pit_replay/1.1` with the v5 cut because the arithmetic changed
  (growth A, acceleration A, coverage OI/interest); 1.0 rows differ from 1.1
  rows by design. The direct H = S7 / S25 / SM arms were never run and are
  moot against 1.1; the in-suite oracle (unbatched = batched, same code)
  passes under 1.1 (176 checks).
- The three autostarted services were left running; they are the owner's.
  Every further memory measurement on this machine is only meaningful with a
  recorded per-process commit baseline taken immediately before it.
## 10. spec_freeze_v6 — the V/Q engine wired under the owner's rulings (2026-09-23)

The owner ruled on all twenty-one V/Q items (R1–R21) and on the six
pending-v5 proposals on 2026-09-23. One cut: `spec_freeze_v6`, digest
1afaca0c17c38657ddb131f03dad59e8d07aa1bceecf9bc9f336d8d09ee6fd85, 82 components; v5 sealed as `FREEZE_V5`. Engine `pit_replay/1.2`
under `factor_spec_v5`: all thirteen keys computable (census 13/0/0, reachable
weight 0.85), the coverage floor state, `PEER_FLOOR_V1`, `WITHIN_BLOCK_FLOOR_V1`
(the single-factor Q path is closed), the V legs through `pit_valuation_spec`'s
frozen scorers, one valuation price resolver under `VALUATION_PRICE_POLICY_V1`,
one basis translation, corporate-action gate v2. The full ruling-by-ruling
record, the six interpretations the session reported rather than chose, and
the new row provenance are in `docs/MODEL-LINEAGE.md`, `spec_freeze_v6` section.

Verification at the cut: `pit_frozen_spec.validate()` 0 problems;
`pit_replay.validate()` []; twelve fast suites green; `test_pit_replay --slow`
(the real 5-entity slice and the 50-target streaming oracle) on a disposable
pilot DB — see the commit message for the counts. The digest was cut from ONE
`python -B` manifest run with `sqlite3.connect` blocked. The 1.2 engine's
memory is not yet measured; `pit_replay_rss` runs one child at a time before
any sizing pilot. No replay authorisation exists; the three local services stay
paused only for the measurement window and are the owner's to resume.
