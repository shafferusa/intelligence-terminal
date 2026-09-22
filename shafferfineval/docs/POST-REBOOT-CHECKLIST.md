# Post-reboot checklist — written 2026-09-22 before the authorised reboot

The reboot ends the Claude Code session that wrote this. Everything below is
what the next session must do, in order, and what it must NOT do.

## State at reboot

```
live freeze        spec_freeze_v4   digest 912268b3ec11bfc641fcac78078a0fb288cc51ce5432f8c27f931a532d1faae9
                   47 components, verify() intact, can_execute_replay() == (True, [])
sealed             v1 7a844dca…  v2 5219bdd5…  v3 2f9bba31…   (all preserved, 0 main-store rows)
main store         9,632,415,744 B, BYTE-IDENTICAL since 2026-09-21 16:07
                   pit_feature 0 | pit_score 0 | pit_replay_run 0 | pit_pillar_score ABSENT
pilot DB           shafferfineval_pilot.db 171 MB, isolated, disposable
                   pit_replay_run row 1 still status='running'  <- FALSE, repair after recovery
machine (pre)      4.3 GiB free (99%), pagefile 11.87 GiB on disk / 2.47 GiB used, 1.0 GiB free RAM
Phase 1 scan       INCOMPLETE — du over ~230 GB did not finish in 60 min; no partial output exists
git                HEAD c9b4050 does NOT describe the code that ran; ~115 dirty/untracked paths
```

## 1. Re-measure FIRST (nothing else until this is done)

```
df -h /c                                   free disk
ls -la /c/pagefile.sys                     pagefile on disk
Get-CimInstance Win32_PageFileUsage        AllocatedBaseSize / CurrentUsage / PeakUsage
Get-CimInstance Win32_OperatingSystem      FreePhysicalMemory
```

If the system-managed pagefile shrank on its own, LEAVE IT system-managed.
Do NOT change pagefile settings without explicit owner approval (§21).

## 2. Confirm nothing moved

```
python pit_frozen_spec.py                  intact=True, digest 912268b3…
python test_pit_replay.py                  expected to PASS once free space > 7.5 GiB
                                           (it failed pre-reboot ONLY because the disk gate
                                            refused at 4.2 GiB — correct behaviour, not a bug)
ls -la shafferfineval_pit.db               9,632,415,744 B, mtime Sep 21 16:07
```

## 3. Repair the pilot run row (§25) — only once free space > 7.0 GiB

```sql
UPDATE pit_replay_run SET status='ABORTED_RESOURCE_GUARD',
       finished_at='2026-09-21T20:05:08Z', n_dates=2, n_entities=15234,
       invalidated_reason='free-space floor breached during 2022-06-30 assemble; pagefile expansion; no 2022 row written'
 WHERE run_id=1;
```
Preserve: two completed dates, the guard breach, no third-date writes.

## 4. Then, in order

- **Phase 2** — streaming `run_date` (batch ~500: compute → write → commit → clear);
  `load_fact_index` stays once per date. RSS harness via `ctypes` PeakWorkingSetSize at
  1k → 4k → 8k. Peak RSS must be ~flat in entity count. Prove identical rows vs the
  50-entity smoke slice.
- **Phase 3** — engine derives eligibility from `factor_spec_v3` (`PRICED_EPS_V2`,
  margin `ebitda_benchmark` via `pit_normalization.benchmark_margin_50_75`). Wire the five
  Q/V factors with `FACTOR_DIRECTION_V1`; direction applied AFTER domain/refusal checks.
  Apply GAP 5/7/8 fixes (peer floor = max(spec, normalizer); price-basis translation
  constant; explicit `version='factor_spec_v3'`). Diagnostic-only bypasses must be labelled.
- **D3** — still UNDECIDED (owner). Do not change the growth denominator (engine uses
  Assets_t−4q) or pick an interest-coverage numerator (engine has none) without a decision.
- **Phase 5** — rerun the four-date pilot only when disk safe + streaming proven + 13/13 glue.
- **Phase 6/7** — recompute the budget with the refined gate; STOP.

## Never

- No 165-date replay without explicit owner authorisation.
- No edits to spec_freeze_v1/v2/v3/v4 in place — a change is a v5.
- No writes to the main store.
