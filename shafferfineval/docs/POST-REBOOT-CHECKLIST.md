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
python pit_frozen_spec.py                  intact=True, digest 912268b3… (as of the reboot;
                                           from the v5 cut expect the digest in the status
                                           paragraph below, 61 components)
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
- No edits to spec_freeze_v1..v6 in place — a change is a v7.
- No writes to the main store.

---

## Status after the reboot — 2026-09-22 (post-reboot session)

Executed in order: §1 re-measured (free disk 13.85 GiB, pagefile 7.45 GiB
system-managed, untouched); §2 confirmed (v4 intact, digest 912268b3…, store
byte-identical); §3 done (run row 1 → `ABORTED_RESOURCE_GUARD`, UTC timestamps
from the meter report — the SQL above wrote local time with a Z suffix); Phase 2
implemented and measured; D3 reported, not changed; Phase 3, 5, 6/7 NOT started.
The full account, the measurements and every contradiction found are in
`docs/POST-REBOOT-REPORT-2026-09-22.md`; the D3 evidence in
`docs/D3-EVIDENCE-2026-09-22.md`; the v4 audit in
`docs/V4-GOVERNANCE-AUDIT-2026-09-22.md`; the streaming design record in
`docs/STREAMING-REPLAY-DESIGN-2026-09-22.md`.

## Status after spec_freeze_v5 — 2026-09-22 (later the same evening)

The owner decided D3 (growth A, acceleration A, coverage = operating income
over interest) and ruled the 2026-09-21 incident cause REJECTED_BY_MEASUREMENT.
spec_freeze_v4 is sealed (`FREEZE_V4`, may not execute the replay); the live
freeze is `spec_freeze_v5`, digest 58722f4c00b676fee76bcc10ba341f44ce18c540a2fbc90625570a51786a5b59, 61 components. Phase 3
above now reads: the engine derives eligibility from `factor_spec_v4`
(`ENGINE_MUST_DERIVE_FROM`, enforced), computes 6 of 13 factors, and still
owes the MARGIN benchmark path, the P/E chain and the EV/EBITDA, fcf,
net-debt and debt/market-cap transforms; GAP 5 and GAP 7 remain NOT FIXED and
FACTOR_DIRECTION_V1 is enforced only at validate time. The D3 line above is closed. Rule
for freezes: no edits to spec_freeze_v1..v5 in place -- a change is a v6.
The freeze's negative test is the file-level mutation procedure of the v4
audit (fresh interpreter, `__pycache__` removed, `python -B`) plus the
in-process drift probes in `test_pit_frozen_spec.py` section 2.
## Status after spec_freeze_v6 — 2026-09-23

The owner ruled R1–R21 and closed the six pending-v5 items; the paused-service
measurement chain ran (baseline, 1k and 4k streamed / unbatched, the 2 MiB
cache attribution, the direct HEAD oracle arms — `docs/POST-REBOOT-REPORT-2026-09-22.md`
§5b). `spec_freeze_v5` is sealed (`FREEZE_V5`); the live freeze is
`spec_freeze_v6`, digest 1afaca0c17c38657ddb131f03dad59e8d07aa1bceecf9bc9f336d8d09ee6fd85, 82 components. Engine `pit_replay/1.2`
under `factor_spec_v5` computes 13 of 13 keys; GAP 5 (`PEER_FLOOR_V1`) and GAP 7
(`PRICE_BASIS_TRANSLATION_V1`) are closed. Rule for freezes: no edits to
spec_freeze_v1..v6 in place — a change is a v7.

The post-fix store-backed `--slow` suite was re-run on 2026-09-23 and PASSED
(235 PASS / 0 FAIL / 0 SKIP, freeze intact at 1afaca0c… after the run), so
that owed item is closed. `pit_frozen_spec.STILL_BLOCKED` names the two
preconditions that remain before the diagnostic replay: a MEASUREMENT (the
1.2 engine's memory, never yet taken — every committed RSS artefact is
`pit_replay/1.1` under the sealed `spec_freeze_v5`) and an AUTHORISATION
(the owner's; none exists). Next, in order: measure the 1.2 engine with
`pit_replay_rss` one child at a time (1k, 4k, then 8k only if the headroom is
then obviously safe), run the four-date pilot on a disposable DB on the
owner's word, resume the three local services after the window.
