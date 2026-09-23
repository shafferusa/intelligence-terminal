# Streaming / batched write-side replay: design record and proof plan (2026-09-22)

**Provenance.** Produced 2026-09-22 by the post-reboot session's automated investigation: three independent finders per track, one synthesizer, then three adversarial verifiers (citation checker, refuter, completeness critic). Verifier verdicts and corrections are appended verbatim at the end. Line numbers refer to the working tree at the time of the investigation; `pit_replay.py` was being edited concurrently (the streaming change), so its line numbers may have moved again. Facts and recommendations are separated as labelled. Nothing in this document is a decision.

---

# Streaming / batched write-side replay — design record and proof plan

**Repository:** `C:\Users\logan\shafferfineval-app\shafferfineval` (git root one level up), HEAD `2e9db18`.
**Written:** 2026-09-22. **Mode:** read-only; nothing in the repository was edited, no test, replay or pilot was run, the 9.6 GB store was not opened. The only executions were small read-only `mode=ro&immutable=1` queries on the 171 MB pilot DB, pure-Python probes on module constants, and two trivial child processes (`python -c "print(os.getpid())"`) for the PID check in §5.

**Legend.** Every paragraph is labelled **FACT** (cited to file:line, verified in this session), **ESTIMATE** (arithmetic on cited numbers, not a measurement) or **PROPOSAL** (design, not in the repo, or a recommended change to what is in the working tree). Claim ids `stream-NN` are listed in the key-claims table for verifiers.

---

## 0. Provenance: what "old" and "new" mean here

**FACT (stream-01).** The working tree already contains an uncommitted draft of the streaming change. `git status` shows `pit_replay.py` modified (1830 lines, md5 `68455882…`, mtime 2026-09-22 19:33:50), `test_pit_replay.py` modified (1210 lines, md5 `69de9a30…`) and `pit_replay_rss.py` untracked (482 lines, md5 `c82b2e54…`); `git diff HEAD --stat` reports `+311/-55` over the two tracked files. HEAD `2e9db18` is the whole-cross-section engine. Every line number below is the **working tree at md5 `68455882…`** unless marked `@HEAD`. The file changed twice while the finders worked (one finder read it at 19:32:11 and proposed adding `n_targets_written`; by 19:33:50 it was present at `pit_replay.py:1109`), so someone is editing concurrently — see §8.

**FACT (stream-02, @HEAD).** The baseline writes the entire cross-section after assembling it: three lists at `@HEAD:1070-1072`, the loop `for entity_id in targets:` at `@HEAD:1084`, then `tick("write", n_features=len(feature_rows), …)` at `@HEAD:1280` and one `executemany` + `commit` per table at `@HEAD:1283-1294` (`written[table] = len(rows)` at 1294). That is **three transactions per date**, one per table — not "one transaction per as-of date" as `full_run_capacity_budget.json` (`peak_wal.assumption`) states.

**FACT (stream-26).** Machine at write time: free disk on C: 13.706 GiB (gate floor 7.5 GiB at `pit_replay_manifest.py:146`, per-date floor 7.0 GiB at `pit_replay_manifest.py:140` and `pit_replay.py:176`); `Win32_OperatingSystem.FreePhysicalMemory` 1.580 GiB of 7.452 GiB; pagefile 7631 MB allocated / 1390 MB in use / 1545 MB peak. The task's "~250 MiB free RAM" was not what the OS reported at either finder or synthesizer time (1.39 GiB and 1.58 GiB), but the no-heavy-run rule was honoured regardless.

---

## 1. Memory map

### 1.1 Per-date containers in `run_date` (FACT, stream-03)

| # | Container | Created | Scales with | Released | Notes |
|---|---|---|---|---|---|
| a | `universe` (list of `sqlite3.Row`) | `pit_replay.py:1051` `universe = pit_identity.peer_universe_as_of(main_conn_ro, day)` | cross-section (~8k) | function return | ordered `ORDER BY e.cik` (`pit_identity.py:1238`) |
| b | `all_ids`, `targets` | 1052-1059; `targets = targets[:int(entity_limit)]` at 1059 | targets | return | deterministic slice of (a) |
| c | `cohorts` | 1063 `cohorts = pit_coverage.stored_cohorts(main_conn_ro, day,` | cross-section | return | two SQL queries (`pit_coverage.py:645-660`); `members` list shared by reference per peer set (`pit_coverage.py:672-673`) |
| d | `needed` | 1065 `needed = set(targets)`; 1066-1069 adds every cohort member | targets ∪ members | return | **`entity_limit` does not bound this beyond the members' sets** |
| e | `index` (fact index) | 1072 `tick("fact_index", n_entities=len(needed))`, 1074 `index = pit_coverage.load_fact_index(…)` | rows of `pit_fact` in the 1170-day window for `needed` | **1090 `index.clear()`** | see §1.2; inside the loader a `staged` dict plus `intern` table exist transiently (`pit_coverage.py:406-407`, flattened by `pop` at 420-423) |
| f | `cpi_cache` | 1077 | distinct revenue reference periods | return | bounded |
| g | `primitives` | 1087 `tick("primitives", …)`, 1088 `primitives: Dict[int, Primitives] = {` | one `Primitives` per NEEDED entity | return | read by `CohortCache` (`pit_replay.py:846`, `855`) and the loop |
| h | `cohort_cache` | 1092 `cohort_cache = CohortCache(main_conn_ro, day, primitives)` | (peer_set × factor) value dicts, filled lazily | return | memo at `pit_replay.py:852-881` |
| i | `feature_rows` / `pillar_rows` / `score_rows` | 1096-1098 | **HEAD: cross-section; draft: one batch** | draft: `rows.clear()` at 1130 per flush | 13 / 4 / ≤1 tuples per target, each carrying JSON strings (appends at 1255, 1302, 1340) |
| j | tallies (`availability_tally`, `reason_tally`, `signature_tally`, `block_tally`) | 1160-1165 | 13 keys / reasons / signatures / 4 blocks | return | bounded, date-level |
| k | `written`, `n_batches_written`, `n_in_batch`, `n_targets_written` | 1106-1109 | scalars | return | draft only |

**FACT (stream-04).** The fact index is ONE scan of `pit_fact` per date. The SQL at `pit_coverage.py:393-401` filters on `period_end BETWEEN ? AND ?`, `available_date <= ?`, `segments = '' AND coreg = ''`, `qtrs IN (0, 4)`, `unit IN ('USD','shares')`, `tag IN (…)`; the entity filter is Python (`pit_coverage.py:408-411` `if entity_id not in wanted: continue`). Both `pit_fact` indexes lead with `entity_id` (`pit_store.py:296-299`), so the plan is a table scan (finder 1 ran `EXPLAIN QUERY PLAN` on the immutable store: `SCAN pit_fact`; not re-run here). Window: `INDEX_WINDOW_DAYS = 1170` (`pit_coverage.py:72`). The loader's docstring is explicit that this structure is the memory hazard: "The three windows together are ~3M rows; held at once that is a working set large enough to matter on a box whose OS has already killed a process for memory" (`pit_coverage.py:377-379`).

### 1.2 What is measured and what is not

**FACT (stream-28).** Measured on disk, not in Python: 13 / 4 / 0.353 rows per entity-date and 576.4 / 740.7 / 1705.1 B per row for `pit_feature` / `pit_pillar_score` / `pit_score` (`pilot_sizing_derived.json` → `per_date.2014-06-30.rows_per_entity_date`, `bytes_per_row`); 2014-06-30 wrote 105,716 + 32,528 + 2,873 rows for 8,132 targets (`pilot_sizing_run.log:11`); 83.63 % of `pit_feature` rows are `unavailable` rows (`full_run_capacity_budget.json` → `levers.lever_1…unavailable_share_of_pit_feature_rows_pct`).

**FACT (stream-21).** No process-memory figure exists anywhere in the engine artefacts: `grep -i "PeakWorkingSet|psutil|tracemalloc|GetProcessMemoryInfo|getrusage" pit_replay_meter.py pit_replay.py` returns nothing; the meter's `_Sample` slots are `(t, wal, shm, journal, free, temp, mark, source)` (`pit_replay_meter.py:268-269`) and `_take_sample` reads only file sizes and free disk (`pit_replay_meter.py:554-558`). Two reusable probes exist elsewhere: `pit_coverage._peak_working_set_mib()` (`pit_coverage.py:1187-1226`, with the argtypes warning at 1210-1213: "a HANDLE passed as ctypes' default C int is truncated on 64-bit Windows and the call fails silently, returning a peak of 0") and `pit_cohort_resources.peak_working_set(handle)` (`pit_cohort_resources.py:43-54`, can target a child handle).

**ESTIMATE (stream-28).** Finder 3's `sys.getsizeof` estimate from real pilot payload lengths gives ~1.26 KB per feature tuple and ~1.36 KB per pillar tuple, i.e. ~22-23 KB per target and **~0.19 GB for the whole 2014-06-30 cross-section of row tuples**. The budget attributes the 5,923,065,856 B pagefile growth to these lists (`full_run_capacity_budget.json` → `levers.lever_4_stream_the_assemble_stage.what`). The estimate cannot reach that figure by a factor of ~30; the fact index / primitives (e, g) are O(cross-section), unmeasured, and untouched by streaming. **Whether streaming flattens peak RSS is therefore an open measurement, not a foregone conclusion** — the draft's own constant says so: "Whether the bound holds is a MEASUREMENT (pit_replay_rss.py), not a claim" (`pit_replay.py:186`).

### 1.3 Stage timing (FACT, stream-20)

`tick()` (`pit_replay.py:1040-1044`) fires BEFORE each stage's work: `tick("fact_index")` at 1072 precedes the load at 1074; `tick("primitives")` at 1087 follows it. The pilot log (`pilot_sizing_run.log:6-12`, 2014-06-30, 8,132 targets) therefore reads: universe 0.0 s → cohorts 1.5 s → fact_index 1.6 s → primitives 57.7 s (**~56 s = the `pit_fact` scan + dict build**) → assemble 58.2 s (~0.5 s for 8,132 `resolve_primitives`) → write 378.6 s (**~320 s of assembly, dominated by the lazy `eligible_peers` SQL inside `CohortCache.get`**, `pit_replay.py:859-861`) → checkpoint 380.7 s (~2 s of inserts). `full_run_capacity_budget.json` → `non_space.elapsed.note` agrees: "per-date time is dominated by the assemble stage … not the insert".

### 1.4 Free-space guard mechanics (FACT, stream-19)

Engine guards: (i) the manifest gate requires free > 7.5 GiB before any writer exists (`pit_replay_manifest.py:146`, `955-957`); (ii) `run_pilot` pre-run check raises `FreeSpaceAbort` with nothing created (`pit_replay.py:1467`); (iii) the per-date check at the top of the date loop (`pit_replay.py:1562-1573`, reason `FREE_SPACE_FLOOR`); (iv) **draft only:** the per-batch check inside `_flush` AFTER the batch's commits (`pit_replay.py:1136-1153`, reason `FREE_SPACE_FLOOR_MID_DATE` at 1595). `MidDateFreeSpaceAbort` is NOT an engine mechanism: it lives in the sizing driver (`run_sizing_pilot.py:51-52`) and is raised from the `progress` callback (`run_sizing_pilot.py:75-82`) at tick boundaries. In the pilot it fired at the `write` tick on 2022-06-30 with 4.132 GiB free (`pilot_sizing_run.log:25`); the pilot DB had a 0-byte WAL and the measured cause was `C:\pagefile.sys` growth of 5,923,065,856 B inside the assemble window (`pilot_sizing_derived.json` → `outcome.abort_cause_measured`).

---

## 2. Cross-section-wide vs per-entity (FACT)

**Once per date — must stay outside any batch (stream-03, stream-05, stream-06).**
1. `created` timestamp, `pit_replay.py:1038` — one value per date; the pilot DB has exactly 1 distinct `created_at` per date (probe: `('2014-06-30', 1, 105716), ('2019-06-28', 1, 92326)`).
2. `universe` → `targets` (1051-1059), deterministic by CIK.
3. `cohorts` + `needed` (1063-1069). `needed` ⊇ every cohort member, so **every cohort member's primitives exist before any target is normalised**; in the pilot `n_entities_indexed == n_targets` on all three dates (log lines 7-8, 14-15, 21-22: 8132/8132, 7102/7102, 8033/8033).
4. `load_fact_index` (1074) and `index.clear()` (1090).
5. `primitives` for all `needed` (1088-1089) — `resolve_primitives` has "NO DATABASE ACCESS except the CPI lookup" (finder-cited docstring; `cpi` memo at 1077-1085).
6. `CohortCache` (1092): memo keyed `(peer_set_id, factor_key)` (`pit_replay.py:852-855`), fills lazily from inside the loop (1206 `record = cohort_cache.get(peer_set_id, rung, members, key)`), "identical by construction because the inputs are" (840). Its only side effects are the memo and `n_eligible_calls` (858), which is reported in stats (1417). **It must persist across batches** or `n_eligible_peer_calls` changes (row values would not).
7. `run_id` — one `lastrowid` per run, before the date loop (`pit_replay.py:1552-1553` region; the only `lastrowid` in the module).

**Cohort-scoped, not cross-section-scoped (stream-07).** `normalise` (`pit_replay.py:893`) passes the cohort's values minus the target for `COHORT_RANK` (910-911) and the whole cohort for `COHORT_DISPERSION` (914-916); the primitives come from `self.primitives.get(int(member))` (`pit_replay.py:867-870`). Minimums: `MIN_RANK_PEERS = 4`, `MIN_DISPERSION_COHORT = 8` (`pit_normalization.py:318`, `310`); entry points `percentile_rank_score` (579), `resolve_scale` (475), `normalize` (856). `eligible_peers` (`pit_factor_spec.py:2109`) issues indexed read-only SELECTs against the store. `benchmark_margin_50_75` (`pit_normalization.py:901`) appears in `pit_replay.py` only as prose (lines 54 and 255), never as a call; `ebitda_benchmark` is refused before any cohort lookup (`pit_replay.py:1192` `if plan.verdict != VERDICT_COMPUTABLE:`). **There is no cross-section-wide statistic, rank or normalisation anywhere in `run_date`.**

**Per-entity — batchable (stream-07).** The loop body `for entity_id in targets:` at `pit_replay.py:1170` through the score append at 1340-1363: primitives lookup, refusal short-circuit (1192), cohort lookup (1206), `normalise(key, raw, {}, …)` for the cohort-free factor (1225), block scoring/assembly (pure functions of one entity's dict; `pit_factor_blocks.block_score` / `assemble`), and the three tuple appends (1255, 1302, 1340). No row is read back from the pilot DB; `pilot_conn` is touched only in `_flush`. Row order within a table is loop order × `REPLAY_FEATURE_KEYS` order; the pilot DB shows `feature_id == rowid` for all 198,042 rows and contiguous ids 1..198042 (probe).

---

## 3. Patch plan (PROPOSAL, measured against the working-tree draft)

The draft already implements most of this; items are marked **keep** / **change** / **add**.

### 3.1 Parameters — keep (stream-08)
`run_date(…, batch_size: Optional[int] = DEFAULT_BATCH_SIZE, free_floor_bytes: Optional[int] = None)` (`pit_replay.py:1014-1015`); `DEFAULT_BATCH_SIZE = 500` (187); `<= 0 → None` = legacy single write (1039-1040); `run_pilot(…, batch_size=DEFAULT_BATCH_SIZE)` (1440) passing both down (1580-1581); CLI `--batch` with `0 = legacy` (1802-1804). `test_batching_is_declared` (`test_pit_replay.py:1055-1072`) pins the signatures.

### 3.2 Loop structure — keep
Everything above `tick("assemble")` (1095) stays once per date. Inside the loop: `n_in_batch += 1; if batch_size is not None and n_in_batch >= int(batch_size): _flush(final=False)` (1366-1368). After the loop: `pending_final` (1371), `tick("write", …cumulative…)` (1372-1377), `_flush(final=True)` (1378). The `_flush` early-return rule (1116-1117) makes the exact-multiple case a no-op final flush and the zero-target case a single empty batch; the `tick("write")` `n_batches` arithmetic (1375-1376) agrees with both.

### 3.3 Commit points — change
Draft: per table, `executemany` then `commit` (1122-1128) — **three transactions per batch**. Proposal:
- **Measurement mode** (`per_table_scopes and meter`): keep per-table commits inside `meter.table_scope(table)`, because the scope "requires the writes to be COMMITTED before the block exits" (`pit_replay_meter.py:893-895`).
- **Production mode** (no scopes): three `executemany` then ONE `commit`, so a batch is atomic across the three tables. Today a failure between the feature commit and the pillar commit of the same batch leaves up to 13 × K feature rows with no pillar rows (pillar rows are "written whether or not a score was emitted", 1287).
- Narrow the meter's exclusivity work: `table_scope` takes `count(*)` on every `exclusivity_tables` entry on entry AND exit (`pit_replay_meter.py:511-518`, `903`, `917`) and the engine passes six tables (`pit_replay.py:1500-1502`). At K=500 a full date is ~17 batches × 3 scopes × 2 × 6 counts. Either `expect_exclusive=True` only for the first batch of each date, or narrow `exclusivity_tables` to the three replay tables.

### 3.4 Guard checks — keep + add
- Keep the engine-side post-commit guard (1136-1153) and its `.detail` dict; keep the ordering comment "The engine's own guard runs BEFORE the tick" (1133-1135), which is what lets the engine's `FreeSpaceAbort` pre-empt the driver's `MidDateFreeSpaceAbort`.
- **Add** a pre-write check at the top of `_flush` (same floor) so the floor prevents a write instead of reporting one; the draft as written commits one batch below the floor before stopping.
- **Add** `tick("batch_full", batch=i, n_features=len(feature_rows), …)` at the top of `_flush` BEFORE any `executemany`: the draft's `write_batch` tick fires after `rows.clear()` (1154-1158), so no observer ever sees the batch at its memory peak. This tick is what §5's attribution needs. Keep the existing stage names so `run_sizing_pilot.py:85-87`'s stage filter still works (it does not list `write_batch`; those ticks are logged only when >5 s apart — harmless).

### 3.5 Summary accounting — keep + add (stream-11)
Keep `written[table] += len(rows)` (1129), `stats["rows"] = written` (1407), `entity_dates = len(targets)` (1406), `batch_size` / `n_batches` (1419-1420); `run_pilot` totals are unchanged (1583-1585). **Add** `n_targets_written` to the returned stats — today it exists only in the closure, the abort detail and the tick (1109, 1148, 1155) — and two end-of-date assertions `written["pit_feature"] == 13 * n_targets_written` and `written["pit_pillar_score"] == 4 * n_targets_written` (the invariants `test_real_slice` already checks at `test_pit_replay.py:1001-1006`). Fix the meter's double count separately: `mark(rows=…)` (`pit_replay_meter.py:710`) and scope exit (976) both add to `_rows_written` (pilot report 396,084 vs DB 198,042; `pilot_sizing_derived.json` → `known_defects_found_by_this_run[2]`).

### 3.6 WAL checkpoint policy — keep + add (stream-10)
Keep one `PRAGMA wal_checkpoint(TRUNCATE)` per date after the final flush with the return triple READ (1381-1397). `wal_autocheckpoint` is set nowhere (`grep` over `pit_store.py pit_replay.py pit_replay_meter.py pit_intermediates.py`: none), so SQLite's default (1000 pages, PASSIVE) applies at each batch commit; the per-date TRUNCATE's `wal_bytes_before` will therefore no longer equal the date's WAL peak. **Add** in `_flush`, after commit: `os.path.getsize(<pilot>-wal)` if present → `stats["wal_peak_bytes_observed"]`, `stats["wal_peak_batch"]`; and an optional `checkpoint_every_n_batches` (default `None`). When the meter runs, its `scope:*:exit` and sampler samples already capture per-batch WAL (`pit_replay_meter.py:918`, `555`).

### 3.7 `run_pilot` — keep, with one open defect (stream-12)
Keep `except FreeSpaceAbort` → `report["aborted"]["reason"] = "FREE_SPACE_FLOOR_MID_DATE"` with `partial_date = detail` (1589-1602) and `except BaseException` → `UPDATE pit_replay_run … 'aborted_exception'` then re-raise (1603-1625); success/abort UPDATE at 1632-1640. This closes pilot defect [0] (run row left `running`). Defect [1] (per-date stats dicts lost on exception) is only partly closed: `summary_json` now records `totals`, `entity_dates`, `dates_completed`, `at_date`, `batch_size` (1616-1621), but the per-date `stats` dicts (tallies, signatures) are still only in the local `report` that the re-raise discards. Proposal: write each completed date's `stats` into `summary_json` (or a sidecar JSON next to the pilot DB) as it completes.

### 3.8 Version stamps — defer
`ENGINE_VERSION = "pit_replay/1.0"` (150) is stamped into `resolved_inputs_json["ev"]` (1251), `provenance_json["engine"]` (1329) and the run row (1543). Leave it at 1.0 until the oracle in §4 has passed against HEAD; bump to 1.1 in a separate commit (or normalise those two JSON keys in the oracle SQL). `pit_replay` is not a frozen-spec component (`pit_frozen_spec.py:256-321` lists none; `grep pit_replay pit_frozen_spec.py` hits only prose at 17 and 705), so the edit does not move `FROZEN_DIGEST 912268b3…` (383).

---

## 4. Equality oracle (PROPOSAL; SQL exact)

### 4.1 Slice definition (stream-30)
Date `2019-06-28`; `entity_limit=50` → the 50 lowest CIKs in that date's `peer_universe_as_of` (`pit_identity.py:1238` `ORDER BY e.cik`; slice at `pit_replay.py:1059`). Deterministic and identical across arms. Note the checklist's "50-entity smoke slice" (`docs/POST-REBOOT-CHECKLIST.md:57`) has no committed fixture: `test_real_slice` uses `entity_limit=5` (`test_pit_replay.py:996-997`); the uncommitted `test_streaming_equals_legacy` uses 50 (`test_pit_replay.py:1095`).

### 4.2 Arms (all into `C:\Users\logan\AppData\Local\Temp\claude\streaming-design\`; basenames other than `shafferfineval_pit.db` are writable per `pit_replay_meter.assert_write_allowed`)
- **H** — HEAD engine: `git show 2e9db18:shafferfineval/pit_replay.py > <scratch>/pit_replay_head.py`, load with `importlib.util.spec_from_file_location("pit_replay_head", path)` with the repo on `sys.path`, call `run_pilot(["2019-06-28"], "<scratch>/oracle_head.db", entity_limit=50, measure=False)` (HEAD has no `batch_size`).
- **L** — working tree, `batch_size=None`.
- **S7** — `batch_size=7` (8 batches, last ragged). **S25** — `batch_size=25` (exact multiple; final flush must be a no-op, `n_batches == 2`). **SM** — `batch_size=7, measure=True, per_table_scopes=True` (exercises the `table_scope` branch of `_flush`, which the draft test never runs: both its arms use `measure=False`, `test_pit_replay.py:1096-1099`).
Each arm starts from a fresh file (`ensure_pilot_db(overwrite=True)`), so `replay_run_id` is 1 everywhere.

### 4.3 Natural keys and excluded columns (FACT, stream-13)
- `pit_feature`: `UNIQUE (entity_id, as_of_date, feature_key, model_version, replay_run_id)` (`pit_store.py:490`); exclude `feature_id` (AUTOINCREMENT, 467) and `created_at`. 24 columns in the pilot DB (23 DDL + `resolved_inputs_json`).
- `pit_pillar_score`: `UNIQUE (entity_id, as_of_date, pillar, model_version, replay_run_id)` (`pit_intermediates.py:424`); no id column; exclude `created_at`. 20 columns.
- `pit_score`: `UNIQUE (entity_id, as_of_date, model_version, replay_run_id)` (`pit_store.py:564`); no id column; exclude `created_at`. 37 columns in the pilot DB after migrations.
Take the column lists from `PRAGMA table_info` at run time minus `(feature_id, created_at)` — exactly what the draft helper `_table_rows` does (`test_pit_replay.py:1074-1085`, excluding INTEGER PK and `created_at`).

### 4.4 SQL (validated mechanics: `ATTACH … ?mode=ro`, `EXCEPT`, `json_extract`/`json_remove` all work on SQLite 3.50.4 against the real pilot schema — probe)
Open `sqlite3.connect(":memory:", uri=True)` AFTER both `run_pilot` calls have closed their connections (Windows holds `-wal` otherwise), then per pair (A = reference arm, B = arm under test):
```sql
ATTACH DATABASE 'file:C:/Users/logan/AppData/Local/Temp/claude/streaming-design/oracle_head.db?mode=ro'    AS a;
ATTACH DATABASE 'file:C:/Users/logan/AppData/Local/Temp/claude/streaming-design/oracle_stream7.db?mode=ro' AS b;

-- 1. counts
SELECT 'pit_feature',      (SELECT count(*) FROM a.pit_feature),      (SELECT count(*) FROM b.pit_feature)
UNION ALL SELECT 'pit_pillar_score', (SELECT count(*) FROM a.pit_pillar_score), (SELECT count(*) FROM b.pit_pillar_score)
UNION ALL SELECT 'pit_score',        (SELECT count(*) FROM a.pit_score),        (SELECT count(*) FROM b.pit_score);

-- 2. symmetric difference over every comparable column (F = PRAGMA table_info(pit_feature) minus feature_id, created_at)
SELECT 'a-b' AS side, * FROM (SELECT F FROM a.pit_feature EXCEPT SELECT F FROM b.pit_feature)
UNION ALL
SELECT 'b-a', * FROM (SELECT F FROM b.pit_feature EXCEPT SELECT F FROM a.pit_feature)
ORDER BY entity_id, as_of_date, feature_key, model_version, replay_run_id;          -- expect 0 rows

-- 3. key-level presence (shows WHICH keys differ when 2 is non-empty)
SELECT 'missing_in_b', a.entity_id, a.feature_key FROM a.pit_feature a
  LEFT JOIN b.pit_feature b USING (entity_id, as_of_date, feature_key, model_version, replay_run_id) WHERE b.entity_id IS NULL
UNION ALL
SELECT 'missing_in_a', b.entity_id, b.feature_key FROM b.pit_feature b
  LEFT JOIN a.pit_feature a USING (entity_id, as_of_date, feature_key, model_version, replay_run_id) WHERE a.entity_id IS NULL;  -- expect 0

-- same pattern for pit_pillar_score (P = all 20 columns minus created_at; ORDER BY entity_id, as_of_date, pillar, model_version, replay_run_id)
-- and pit_score (S = all 37 columns minus created_at; ORDER BY entity_id, as_of_date, model_version, replay_run_id).
-- Once ENGINE_VERSION is bumped, replace resolved_inputs_json by json_remove(resolved_inputs_json,'$.ev')
-- and provenance_json by json_remove(provenance_json,'$.engine') in F / S.

-- 4. run row (summary_json differs by batch_size; compare its totals only)
SELECT status, n_entities, n_dates, json_extract(summary_json,'$.totals'), json_extract(summary_json,'$.entity_dates') FROM a.pit_replay_run
EXCEPT
SELECT status, n_entities, n_dates, json_extract(summary_json,'$.totals'), json_extract(summary_json,'$.entity_dates') FROM b.pit_replay_run;  -- expect 0

-- 5. streamed-side invariants
SELECT as_of_date, count(DISTINCT created_at) FROM b.pit_feature GROUP BY as_of_date;   -- expect 1 per date
SELECT count(*) FROM b.pit_feature WHERE feature_id != rowid;                           -- expect 0
SELECT count(*) FROM b.pit_feature WHERE replay_run_id != 1;                            -- expect 0
```
Python side, in addition: SHA-256 over `SELECT <cols> FROM x.<table> ORDER BY <natural key>` per arm and table; compare `report["dates"][0]` on exactly the key list the draft test uses (`rows, n_targets, n_entities_indexed, n_scored, n_refused_insufficient_block_coverage, n_no_peer_set, n_challenger_rows, blocks_resolved, signatures, availability, reasons, n_eligible_peer_calls`; `test_pit_replay.py:1104-1108`), never `elapsed_s`, `free_bytes_after`, `checkpoint`, `batch_size`, `n_batches`. All REAL comparisons are exact: same inputs, same arithmetic, no tolerance.

### 4.5 What the archived pilot DB can and cannot prove (FACT, stream-24)
`shafferfineval_pilot.db` rows are stamped `spec_freeze_v3` / `2f9bba3136ac…` (`resolved_inputs_json` sample `"fd":"2f9bba3136ac"`, probe; log line 4) while the live freeze is `spec_freeze_v4` / `912268b3…` (`pit_frozen_spec.py:212`, `383`), and its run row's `code_revision c9b4050…` "does NOT describe the code that ran" (`docs/POST-REBOOT-CHECKLIST.md:18`). It is not a byte oracle; the H arm is.

### 4.6 Gating
`test_streaming_equals_legacy` runs only under `--slow` or `--oracle` (`test_pit_replay.py:1180-1185`), needs the store present and > 7.5 GiB free (gate). Its "legacy" arm is the NEW code with batching off, not HEAD; add the H arm (or a one-off script) before calling the equality proven.

---

## 5. RSS harness design

### 5.1 Facts about the draft `pit_replay_rss.py` (stream-22, stream-23)
- Spawns `sys.executable` (`pit_replay_rss.py:193`) and polls `OpenProcess(0x0400|0x0010, False, proc.pid)` (206) at 0.2 s (243-249); `peak_pagefile` comes ONLY from the parent side (247-248, 292).
- The venv interpreter is a launcher stub: `.venv/Scripts/python.exe` is 255,200 B, `pyvenv.cfg` `home = C:\Users\logan\AppData\Local\Python\pythoncore-3.14-64`. **Verified this session:** a child spawned from the venv exe reported `os.getpid()=13552` while `Popen.pid=5244`; from the base exe the two matched (9636). So the parent-polled `peak_ws` and the only `peak_pagefile` measure the stub, not the engine. The docstring's promise "parent-side: the child's PeakWorkingSetSize, polled … so the peak cannot be missed" (15-17) does not hold under the venv.
- Child self-reports `ws / peak_ws / pagefile / peak_pagefile` at every engine tick (`memory_counters`, 97-100; `progress`, 151-157) and at start/done (159, 170-185). `peak_ws` is rescued by `max(parent, child)` (269-276); `peak_pagefile` is not.
- Attribution: `fixed = ws_at("assemble")` (277), `write_side = peak − fixed` (278-279) — a process-lifetime peak minus a stage reading, which mis-attributes whenever the peak was set during `fact_index`/`primitives`. Its own note admits the index pages may not be returned to the OS (305-311).
- Runs the child with `measure=False, per_table_scopes=False` (166): no WAL observation.
- Has a disk guard only (361-362); `machine_memory()` (117-128, `GlobalMemoryStatusEx`) exists but is used only for before/after snapshots.
- Matrix defaults: `--limits [1000]`, `--batch 500`, `--modes stream,legacy`, `--date 2019-06-28` (465-471); DBs deleted between runs (381-386); verdict is a side-by-side table with `write_side_ratio_hi_over_lo` (398-419).

### 5.2 Proposal
**(a) Measure the right process.** Spawn `sys._base_executable` (the engine is "Stdlib only", `pit_replay.py:108`, so no venv site-packages are needed) with `cwd=repo`, `PYTHONPATH=repo`; assert the child's `start.pid == proc.pid`, else set `parent_numbers_are_stub=True` and use child-side counters only. Alternative: a Job Object (`QueryInformationJobObject` → `PeakProcessMemoryUsed`) which also covers grandchildren.

**(b) Primary metric = commit, not working set.** The pilot died of pagefile growth, not WS; and the OS can trim WS under pressure, which would make WS-based deltas understate. Use child-side `PagefileUsage` per tick and `PeakPagefileUsage` (already in `memory_counters`); keep WS as the secondary series; optionally add `PrivateUsage` via `PROCESS_MEMORY_COUNTERS_EX`.

**(c) Separate the fact-index residency from the write side using the ticks the engine already emits plus one new one.** Ticks bracket the consumers exactly (§1.3): `fact_index` fires before the load (1072), `primitives` after the load (1087), `assemble` after `resolve_primitives` and `index.clear()` (1090, 1095); the proposed `batch_full` (§3.4) fires with a full batch in memory; `write_batch` after `rows.clear()` (1154); `write` before the final flush (1372). Define, on the commit series `c(tick)`:
- `index_resident = c(primitives) − c(fact_index)` — the fact index (+ loader transients) while resident;
- `primitives_net = c(assemble) − c(primitives)` — primitives added, minus whatever `index.clear()` returned (pymalloc arenas usually keep it; report the sign, do not assume);
- `fixed_cost = c(assemble)`;
- `write_side_peak = max_i c(batch_full_i), c(write)) − c(assemble)` — the streamed bound under test;
- `per_batch_growth_i = c(batch_full_i) − c(write_batch_{i−1})` — the within-run flatness test: in streamed mode this series should be ~constant in `i`, independent of N; in legacy mode `c(write) − c(assemble)` should grow ~linearly in N;
- `peak_reached_at_stage` = the first tick at which the child's `peak_pagefile` equals its final value. If that stage is `primitives` or `assemble` in BOTH modes, the process peak is set by the fact index and streaming cannot lower it — the next lever is the index, not the write side.
Report `n_entities_indexed` per run (already in the `done` line, 176): with `entity_limit=N`, `needed` still includes every cohort member (1066-1069), so `fixed_cost` is a function of `n_entities_indexed`, not of N.
Optional exact cross-check at N=1000 only (memory overhead is significant): `tracemalloc` snapshots taken in the `progress` callback at `primitives`, `assemble` and the first `batch_full`, grouped by `filename:lineno` — the index build is `pit_coverage.py:416-422`, primitives `pit_replay.py:1088`, row tuples 1255/1302/1340. This gives per-container Python bytes that no counter can.

**(d) WAL.** Run the child with `measure=True` (sampler + scope-exit samples, stat calls only) or read `stats["wal_peak_bytes_observed"]` from §3.6; the draft's `measure=False` leaves the WAL claim unmeasured.

**(e) Guards.** Keep the disk guard; add a parent-side `GlobalMemoryStatusEx` poll: `TerminateProcess` the child and record `aborted_memory_guard` if `ullAvailPhys < 300 MiB` or `ullAvailPageFile < 1 GiB`; record a Win32_PageFileUsage `CurrentUsage` reading before and after each child. Pre-run conventions (state them as conventions): `AvailPhys ≥ 1 GiB` before a 1k child, `≥ 2 GiB` before a 4k child. Today's 1.58 GiB free RAM permits 1k, not 4k, until §1 of the checklist is re-measured after closing other processes.

**(f) Matrix and order.** `{1000, 4000} × {stream 500, legacy None}` on one date, run in this order: stream 1k → legacy 1k → stream 4k → legacy 4k (only if the 1k legacy write-side extrapolated ×4 plus `fixed_cost(4k)` fits `AvailPhys − 512 MiB`). One child at a time; DBs deleted between runs; 8k withheld pending §7. **Add** one two-date arm (`["2019-06-28","2022-06-30"]`, stream 1k) and compare `c(universe)` of date 2 with date 1 — the pilot survived two dates and died on the third, and a one-date-per-child harness cannot see cross-date accumulation.

---

## 6. Risk list (each with the code that creates it)

| id | Risk | Evidence |
|---|---|---|
| R1 | **Partial-date commits and idempotency.** After a mid-date abort, committed batches remain under the same `replay_run_id`; re-running the date under the same run id fails at the first duplicate (`UNIQUE` includes `replay_run_id`, `pit_store.py:490`; plain `INSERT`, `pit_replay.py:958-960`); under a new run id it doubles the partial rows. | draft raises after commit: 1136-1153; "COMMITTED and stay in the pilot DB" (1141-1142). Mitigation: resume by skipping `n_targets_written` targets (deterministic order) or `SELECT DISTINCT entity_id FROM pit_pillar_score WHERE as_of_date=? AND replay_run_id=?` (pillar rows exist for every target, 1287). No DELETE triggers exist on the replay tables (`BEFORE DELETE` only on `pit_fact` and `pit_label_set`, `pit_store.py:308`, `711`), so cleanup is possible but wastes AUTOINCREMENT ids. |
| R2 | **Cross-table atomicity per batch.** Three commits per batch (1122-1128). | §3.3 |
| R3 | **Guard after commit.** One batch is written below the floor before the abort (1129 → 1137). | §3.4 |
| R4 | **FK parents are seeded per date list.** `pit_peer_set` copied only `WHERE as_of_date IN (…)` (`pit_replay.py:617-623`, `INSERT OR IGNORE`); `foreign_keys = ON` on every writer (`pit_store.py:1047`). A resumed run with a different date list must re-seed (safe: OR IGNORE). | |
| R5 | **Hard kills still leave `status='running'`.** Only in-process exceptions reach the new `except` arms (1589, 1603); a `TerminateProcess` from a harness guard or a power loss does not. The archived pilot row is exactly this state (probe: `(1, 'running', None, 4, …)`; `docs/POST-REBOOT-CHECKLIST.md:14`, repair SQL at 45-48). | |
| R6 | **Meter cost and double counting.** `count(*)` on six tables per scope entry/exit (`pit_replay_meter.py:511-518`, `pit_replay.py:1500-1502`); rows double-counted (710, 976). | §3.3, §3.5 |
| R7 | **WAL peak understated by the per-date TRUNCATE** once auto-checkpoints run at batch commits; `wal_autocheckpoint` unset (grep none). Budget assumes "ONE transaction per as-of date" (`full_run_capacity_budget.json` → `peak_wal.assumption`); the code never did that. | §3.6 |
| R8 | **`ENGINE_VERSION` unchanged** despite changed write semantics; stamped in rows (1251, 1329). Bumping before the oracle breaks the H comparison. | §3.8 |
| R9 | **Harness measures the launcher stub** under the venv (verified PID mismatch); `peak_pagefile` has no child-side fallback (292). | §5.1 |
| R10 | **The 5.9 GB attribution is unproven.** Row lists ≈ 0.19 GB by estimate; the index/primitives are unmeasured and O(cross-section). Streaming may not move the peak. | §1.2 |
| R11 | **Concurrent edits.** `pit_replay.py` mtime moved during the finders' review and again before this synthesis (`n_targets_written` appeared). Line numbers here are pinned to md5 `68455882…`. | §0 |
| R12 | **The draft test proves less than the checklist asks.** Its legacy arm is new code (`test_pit_replay.py:1096-1097`); both arms `measure=False`; no exact-multiple case; no `created_at` cardinality check; no run-row totals check. | §4.2, §4.4 |
| R13 | **`n_targets_written` is not returned** in stats (only 1109/1148/1155), so a normal completion cannot be cross-checked against 13×/4×. | §3.5 |
| R14 | **Machine headroom.** 1.58 GiB free RAM now; the pilot's 8k assemble grew the pagefile by 5.9 GB. A 4k or 8k child without a memory guard can repeat the incident. | §5.2(e), §7 |
| R15 | **Per-date stats dicts still lost on exception** (pilot defect [1] partly open). | §3.7 |
| R16 | **Cross-date accumulation unmeasured** (pilot died on date 3; harness runs one date per child). | §5.2(f) |

---

## 7. Acceptance criteria

All thresholds below are **PROPOSED conventions**; the numbers in brackets say where each comes from.

### 7.1 Correctness (required before any memory run is interpreted)
1. `python pit_frozen_spec.py` → `intact=True`, digest `912268b3…` before and after (`pit_frozen_spec.py:383`); `test_pit_replay.py` fast suite passes.
2. Oracle §4: for each of L, S7, S25, SM vs H — counts equal; 0 rows from every symmetric `EXCEPT` and every key-level `LEFT JOIN`; run-row totals equal; stats identical on the listed keys; `n_batches` = 1 / 8 / 2 / 8; 1 `created_at` per date; `feature_id == rowid`; both `checkpoint.return_read is True` (`test_pit_replay.py:1015-1016`); run rows `complete`.

### 7.2 1k and 4k runs (one date, `2019-06-28`, stream 500 vs legacy)
Per run, from child-side commit series unless stated:
- **M1 (bound holds):** stream `write_side_peak(4k) / write_side_peak(1k) ≤ 1.5`, and stream `per_batch_growth_i` has `max/median ≤ 1.5` across batches. [ESTIMATE of expected size: ~22-23 KB per target × 500 ≈ 11 MB per batch, so an absolute ceiling of 64 MiB is generous.]
- **M2 (harness can see the effect):** legacy `write_side_peak(4k) / write_side_peak(1k) ≥ 2.5` (expected ≈ 4). If M2 fails, the harness cannot resolve the write side and M1 is uninformative — do not proceed to 8k on M1 alone.
- **M3 (stage attribution reported, not judged):** `index_resident`, `primitives_net`, `fixed_cost`, `peak_reached_at_stage`, `n_entities_indexed` for every run.
- **M4 (machine):** parent `AvailPhys ≥ 300 MiB` and `AvailPageFile ≥ 1 GiB` throughout; Win32_PageFileUsage `CurrentUsage` delta across each child ≤ 512 MB; free disk ≥ 7.0 GiB throughout; no `aborted_memory_guard`, no `FreeSpaceAbort`.
- **M5 (WAL):** stream WAL peak per date ≤ legacy WAL peak at the same N; every TRUNCATE returns `(0,0,0)`; 0 busy refusals. [Pilot: 61.35 / 69.28 MB per date for 8k, `(0,0,0)` each time.]
- **M6 (time):** stream elapsed ≤ 1.25 × legacy elapsed at the same N (the assemble stage dominates, §1.3, so batching should be near-neutral; the extra commits under `synchronous=NORMAL`, `pit_replay.py:193`, are the only added cost).
- **M7 (rows):** `rows == {13N, 4N, ≤N}`, `n_scored + n_refused == N` (`test_pit_replay.py:1001-1013`).

### 7.3 What justifies an 8k run — and what does not
Run 8k (stream only, never legacy) only if ALL hold:
- G1: M1 and M2 both pass at 1k → 4k.
- G2: `peak_reached_at_stage` for stream 4k is `primitives`/`assemble` (fixed cost), or, if it is `batch_full`, `write_side_peak(4k) ≤ 64 MiB`.
- G3: projected peak commit at 8k = `fixed_cost(4k) × n_entities_indexed(8k) / n_entities_indexed(4k) + write_side_peak(4k) + 512 MiB` ≤ `AvailPhys` at start (so no pagefile growth is expected at all — the 2022 date grew the pagefile by 5.9 GB, and "fits in AvailPageFile" is what the pilot already had). `n_entities_indexed(8k)` is known from the pilot log for the same dates (8132 / 7102 / 8033).
- G4: free disk ≥ 7.5 GiB (gate) and the checklist's §1 re-measurement done.
Do NOT run 8k if: M2 fails (harness blind); M1 fails (bound not holding); `fixed_cost(4k) > 50 %` of `AvailPhys` (then the fact index is the problem and streaming is the wrong lever); any pagefile growth > 512 MB during the 4k child; or the two-date 1k arm shows `c(universe)` of date 2 exceeding date 1 by more than `write_side_peak(1k)` (cross-date accumulation, R16).

---

## 8. Contradictions and open items
See the structured `contradictions` and `unresolved` lists. The most consequential: (i) the budget's "one transaction per date" vs the code's three; (ii) lever_4's 5.9 GB attribution vs the ~0.19 GB estimate for the row lists; (iii) the harness measuring the venv stub; (iv) the draft oracle's "legacy" arm being new code; (v) the working tree changing under review.

---

## Verification notes: citations

## Citation check — streaming/batched write-side replay design

**Result: 32 of 32 key claims CONFIRMED** (0 REFUTED, 0 UNVERIFIABLE). Every cited snippet was found in the cited file at or within a few lines of the cited line, with one systematic exception (the RSS harness, below). Line-number drift elsewhere is 0–6 lines and the content matches.

### Corrections to make in the report

1. **`pit_replay_rss.py` moved under review (again).** At session start it was md5 `c82b2e54…`/482 lines, matching stream-01; at 19:57:40 it became md5 `04641693…`/514 lines. All §5.1 / stream-22 line numbers are now stale by +18..+27 in the second half (`sys.executable` 193→211, `OpenProcess` 206→224, `measure=False` 166→184, `fixed = ws_at("assemble")` 277→295, `peak_pagefile_bytes` 292→311, disk guard 361→386-387, `--limits/--batch` 468→495-496). Re-pin or cite by snippet. Add this to R11.

2. **The harness has already been run — cite the results.** Untracked `rss_report_1k.json` (19:50) and `rss_report_4k_stream.json` (19:56) exist. Child-reported: stream 1k peak 206.7 MiB / fixed 194.4 / write-side 12.3 (2 batches); legacy 1k 208.8 / 194.4 / 14.3 (1 batch); stream 4k 248.9 / 197.7 / 51.2 (8 batches). By the harness's own attribution the streamed write-side delta grew **4.2×** from 1k to 4k, so §7.2 M1 (≤1.5×) fails as measured, and M2 is unevaluable (no legacy 4k). §5, §7 and R10 should reference these as data with the caveat in (3).

3. **stream-23 has production evidence, not just a probe.** In all three real runs `child_pid != start.pid` (14444 vs 12632; 17932 vs 7984; 10568 vs 10556) and the parent-polled peak was 4.3–4.5 MiB versus 207–249 MiB child-reported — the parent measured the venv launcher stub. `peak_pagefile_bytes` in those reports is the stub's.

4. **stream-04: three `pit_fact` indexes, not two.** The store also has `sqlite_autoindex_pit_fact_1` from `UNIQUE (entity_id, taxonomy, tag, unit, period_end, qtrs, segments, coreg, accn)`. All three lead with `entity_id`; `EXPLAIN QUERY PLAN` (mode=ro&immutable=1) returns `SCAN pit_fact`. Conclusion unchanged.

5. **stream-18: the "lower bound" declaration lives in `pilot_sizing_derived.json:130,226` and `pilot_sizing_report.json:101-102`** (`peak_is_lower_bound: true`, `"LOWER BOUND, ..."`), not in `full_run_capacity_budget.json` (whose line-87 caveat is about temp spills).

6. **stream-14: the three replay tables also reference `pit_listing(listing_id)`** (nullable; `pit_replay.py:632-633` explains NULL is not FK-checked). Mention it under R4.

7. **stream-20: label the `eligible_peers` attribution as inference.** The 56 / 0.5 / 320 / 2 s gaps are arithmetic on the log and the tick ordering is as claimed; the cause of the 320 s is supported (no other main-store access in the loop; budget note "reads from the main store") but not profiled.

8. **Small anchor fixes:** checklist `status='running'` is line 15 (14 is the pilot-DB line); `exclusivity_tables` at `pit_replay.py:1506-1508`; `tick()` defined at 1042; CohortCache lines 853/859/865; `n_eligible_peer_calls` at 1416; `table_scope`'s exit `count(*)` is conditional on `expect_exclusive` (default `True`, `pit_replay_meter.py:887`, `918`), which makes the §3.3 narrowing proposal a one-keyword change; untracked `pit_archive/options/` is also in `git status`.

### Independent re-checks performed (read-only)
- md5/wc/diff-stat re-pinned twice; HEAD copy of `pit_replay.py` extracted to scratch (1675 lines, 0 hits for `batch_size`).
- Pilot DB (`mode=ro&immutable=1`): created_at per date `(2014-06-30,1,105716),(2019-06-28,1,92326)`; feature_id 1..198042 contiguous, 0 rowid mismatches; 24/20/37 columns; run row `(1,'running',None,4,…,'c9b40504…')`.
- Store (`immutable=1`): index list + `EXPLAIN QUERY PLAN` only (no data read).
- getsizeof re-estimate on pilot samples: 1185 / 1406 / 2526 B per feature/pillar/score tuple → 21.4 KB/target → 0.178 GB for 8,132 targets (claim: ~0.19 GB, ~30×).
- PID probe re-run: venv exe `Popen.pid≠os.getpid()`; base exe equal.
- Machine: 1.393 GiB free of 7.452; pagefile 7631/1360/1545 MB; C: 13.695 GiB free (transient values drift from the report's; totals match).

---

## Verification notes: refute

## Refuter verdict — streaming/batched write-side design record

**Scope.** Read-only; nothing edited. Executions: `git show`/`grep`/`awk` on source, `mode=ro&immutable=1` queries on the 171 MB pilot DB, two `python -c "print(os.getpid())"` children, one CIM/PSDrive read. The 9.6 GB store was not opened.

**Tally.** 31 CONFIRMED, 1 REFUTED (stream-32), 0 UNVERIFIABLE. Two tracked files still match their pinned md5s (pit_replay.py 68455882, test_pit_replay.py 69de9a30); `pit_replay_rss.py` does not.

### Corrections (FACT)

1. **stream-32 — REFUTED (superseded).** The pilot run row is no longer `running`. Probe at 20:00 EDT: `(1, 'ABORTED_RESOURCE_GUARD', 15234, 2, '2026-09-21T23:49:43+00:00', '2026-09-22T00:12:26+00:00', 'c9b4050…', "free-space floor (7.0 GiB) breached at 2026-09-22T00:05:08+00:00 during the 'wri…")`. `shafferfineval_pilot.db` mtime is 19:58, after the finders' probe; row counts are unchanged (198042/60936/5582). The applied repair differs from `docs/POST-REBOOT-CHECKLIST.md:46-49` (finished_at, reason text). R5's "The archived pilot row is exactly this state" is now false. Checklist citation is line 15, not 14.

2. **stream-01 / stream-22 — pin broken for the harness.** `pit_replay_rss.py` was rewritten at 19:57:40: 514 lines, md5 04641693, adding a `--cache-kib` page-cache override (lines 153-166, 501-504). Every rss line citation is stale by 14-27 lines (e.g. `argv = [sys.executable` 193→211, `OpenProcess` 206→224, `"peak_pagefile_bytes"` 292→311, `fixed = ws_at` 277→295, `measure=False` 166→184, disk guard 361→387, `--limits` 468→495). Substance of stream-22 holds on the new file.

3. **Missed artefacts — the measurements the report says do not exist are on disk.** Untracked `rss_report_1k.json` (23:40:22Z) and `rss_report_4k_stream.json` (23:51:07Z):
   - 1k stream: peak_ws 206.7 MiB, fixed(assemble) 194.4, write_side 12.3, 2 batches, n_entities_indexed 6926.
   - 1k legacy: peak 208.8, fixed 194.4, write_side 14.3, 1 batch.
   - 4k stream: peak 248.9, fixed 197.7, write_side 51.2, 8 batches, n_entities_indexed 7102; write_batch WS rises monotonically 205.1 → 248.9 MiB (~6.3 MiB/batch); child PagefileUsage 197.7 → 241.9 MiB in step.
   - Consequences: (a) §1.2's "No process-memory figure exists anywhere in the engine artefacts" is stale (stream-21 remains true only for meter/engine code); (b) the report's M1 (4k/1k ≤ 1.5) already FAILS: 51.2/12.3 = 4.16; (c) at 1k legacy (14.3) ≈ stream (12.3), so M2 is untested and the 1k pair cannot resolve the modes; (d) the 5.9 GB attribution is contradicted by measurement, not just by estimate — legacy 1k write-side ≈ 14.6 KB/target → ~115 MiB for 8,132 targets, the same order as the 0.19 GB estimate. Code-visible candidates for the per-batch growth, both unbounded by `batch_size`: the 64 MiB writer page cache (`pit_replay.py:192`) and the CohortCache memo (`pit_replay.py:848, 882`).
   - The harness already records child-side `peak_pagefile` (child_done.peak_pagefile 209,108,992 B vs top-level stub value 913,408 B), so §5.2(b)'s commit metric can be computed from the existing reports.

4. **stream-18 strengthened.** `pilot_sizing_report.json` → `meter_report_after_abort.table_scopes` shows the *pilot* code also committed per table: three exclusive scopes per date with committed `rows_delta`; on 2014-06-30 `wal_bytes_delta` = 61,313,840 for pit_feature and 0 for the pillar/score scopes — SQLite's default auto-checkpoint recycled the WAL between the per-table commits. The pilot "WAL peak" (61.35 / 69.28 MB) is therefore one table's commit, and the budget's "ONE transaction per as-of date" was false for the code that ran, not only for HEAD.

5. **stream-23 corroborated by the artefacts** (beyond the re-run probe: venv Popen.pid 12448 vs child 11644; base 1752/1752): rss reports show child_pid 14444 vs child_start.pid 12632 and parent-polled peak_ws 4,468,736 B (the 255,200-byte launcher).

6. **Minor line drift on unchanged files** (finder error): stream-05 852/858/867/1417 → 853-854/859/865/1416; stream-15 1180 → 1185; stream-16 1001/1015 → 1003/1016; stream-17 1500 → 1506; stream-20 1040 → 1042.

7. **Artefact inconsistency:** `pilot_sizing_derived.json` outcome.abort_reason says 4.118 GiB; the log (line 25) and report say 4.132 GiB.

8. **stream-04 caveat:** the table-scan conclusion follows from the DDL (only two pit_fact indexes, both leading with entity_id; `grep 'ON pit_fact'` finds no others in source); the live `EXPLAIN QUERY PLAN` was not re-run here.

9. **stream-31 nuance:** the driver's guard also runs at `tick("write")` (1372), which precedes the final `_flush(final=True)` (1378), so engine-before-driver ordering is guaranteed for non-final batches only.

### Recommendations (RECOMMENDATION, separate from the facts above)

- Fold `rss_report_1k.json` / `rss_report_4k_stream.json` into §5/§7 before adopting any thresholds; §7.2 M1 is already exercised and fails on working set — either the metric or the bound needs revisiting (page cache and cohort memo are the first suspects; the new `--cache-kib` arm targets the former).
- Re-pin `pit_replay_rss.py` (md5 04641693) and renumber §5.1.
- Reword stream-32 / R5 to "was `running` until repaired at 19:58 on 2026-09-22; repair values differ from the checklist SQL" and record the repair.
- Add the pilot `table_scopes` evidence to §8(i): the "one transaction per date" contradiction is settled for both the pilot code and HEAD.

---

## Verification notes: complete

## Corrections to the streaming design record (completeness critic)

All FACTs below were verified in this session read-only; the repository was not edited, no test/replay/pilot was run, and the only opens of the 9.6 GB store were two schema-only statements over `file:…?mode=ro&immutable=1` (`PRAGMA index_list`, `EXPLAIN QUERY PLAN`).

### 1. Stale or refuted claims

**stream-32 / R5 — REFUTED as of 19:58:36.** `shafferfineval_pilot.db` was modified after the report. Run row 1 now reads `status='ABORTED_RESOURCE_GUARD'`, `finished_at='2026-09-22T00:12:26+00:00'`, `n_entities=15234`, `n_dates=2`, with the long `invalidated_reason` ("free-space floor (7.0 GiB) breached at 2026-09-22T00:05:08+00:00 during the 'write' stage of 2022-06-30 … main store untouched"). The checklist §3 repair (`docs/POST-REBOOT-CHECKLIST.md:46-49`) has been applied; note its `finished_at` differs from the checklist SQL's `'2026-09-21T20:05:08Z'`. The second half of the claim (in-process exceptions only, `pit_replay.py:1603-1625`) stands.

**stream-22 / §5.1 / stream-01 — citations no longer pinned.** `pit_replay_rss.py` was rewritten at 19:57:40 (md5 `04641693d05a32099d2a3b6a1eef9c2e`, 514 lines; the report pinned `c82b2e54…`, 482 lines). Current lines: `argv = [sys.executable, …, "--child"` 211; `OpenProcess(0x0400 | 0x0010, False, proc.pid)` 224-225; `measure=False, per_table_scopes=False` 184; `fixed = ws_at("assemble")` 295; `write_side = (peak - fixed …` 296; `"peak_pagefile_bytes": peak_pagefile_parent` 311; `free = _free_bytes(HERE)` 386; `--date` 492, `--limits` 495, `--batch` 496, `--modes` 498, `--out` 500, new `--cache-kib` 501-504 (child-side override of `PILOT_PRAGMAS` cache_size, 153-166). The stub problem (R9) persists: no re-target to `start["pid"]`; headline `peak_pagefile_bytes` is still parent-only; still no memory guard.

**Minor line corrections.** Checklist run-row line is `:15` (not 14); repair SQL `:46-49` (not 45-48); `run_id = int(cur.lastrowid)` is `pit_replay.py:1547` (not "1552-1553 region"); the budget's `evidence_files` are named `exp_*.json` but exist as `capacity_exp_*.json`.

### 2. The measurement the report says is open has been made (FACT)

Four harness runs exist and are uncited: `rss_report_1k.json` (2026-09-22T23:40:22Z, stream + legacy, N=1000), `rss_report_4k_stream.json` (23:51:07Z), `rss_report_4k_legacy.json` (23:57:41Z; file appeared 20:02:58 ET during this review). All on `2019-06-28`, freeze v4 intact, `cache_kib_override` None.

| mode | N | peak WS MiB | fixed (assemble) | write_side | batches | n_entities_indexed | elapsed s |
|---|---|---|---|---|---|---|---|
| stream | 1000 | 206.7 | 194.4 | 12.3 | 2 | 6926 | 280 |
| legacy | 1000 | 208.8 | 194.4 | 14.3 | 1 | 6926 | 314 |
| stream | 4000 | 248.9 | 197.7 | 51.2 | 8 | 7102 | 305 |
| legacy | 4000 | 250.4 | 198.0 | 52.4 | 1 | 7102 | 317 |

- **§7.2 M1 fails on this data:** stream write_side ratio 4k/1k = 51.2/12.3 = **4.17** (criterion ≤ 1.5). **M2 passes:** legacy 52.4/14.3 = 3.66. Streamed and legacy peaks are equal. Stream-4k per-batch WS at each `write_batch` tick: 205.1, 211.4, 217.9, 223.8, 230.3, 236.5, 242.9, 248.9 MiB — cumulative +6 MiB per 500 targets after `rows.clear()`. Under §7.3 G1 as written, the 8k run is blocked.
- **Direct measurement of the row lists (legacy arms):** `ws(write) − ws(assemble)` = 1.9 MiB at 1k and 3.9 MiB at 4k with the entire cross-section of tuples in memory (tick `write` at `pit_replay.py:1372` precedes `_flush(final=True)`). Caveat: arenas freed by `index.clear()` (1090) are reusable, so this is net growth. It nonetheless makes lever_4's 5.9 GB attribution (`full_run_capacity_budget.json`) untenable for the 2019 date — the report's R10 should be upgraded from "unproven" to "contradicted by measurement on 2019-06-28".
- **Stub confirmed by artefact:** every run has Popen `child_pid` ≠ child `os.getpid()` (14444/12632, 17932/7984, 10568/10556, 11456/11604); parent-polled peak WS 4.3-4.5 MiB; parent `peak_pagefile_bytes` 0.9 MiB vs child 199-244 MiB.
- **Fixed cost is the whole cross-section at any N:** n_entities_indexed 6926 at N=1000 and 7102 at N=4000 (the pilot's full 2019 count is 7102).
- **Timing:** assemble is front-loaded in the cohort warm-up (4k batch 1 at +170 s, batches 2-8 at 43, 12, 2, 9, 3, 1.3, 1.9 s); 4k costs only 25 s more than 1k.

### 3. Hypothesis the owner should test before re-reading M1 (NOT a fact)

The measured write_side growth tracks bytes written to disk: pilot bytes/row × rows = 10.5 MiB (N=1000) and 42.2 MiB (N=4000) vs measured 12.3/51.2 and 14.3/52.4. `PILOT_PRAGMAS` sets `cache_size -64000` (`pit_replay.py:192`, applied in `open_pilot` 579-580), so SQLite's page cache filling with written pages — capped at 64 MiB, identical in both modes — is the quantitatively consistent explanation. The rewritten harness's `--cache-kib` exists for exactly this but has not been used. Until it is, "write side" as the harness defines it cannot distinguish page cache from Python lists, and §7.2 M1's "absolute ceiling of 64 MiB" coincides with the cache cap.

### 4. Papered-over or overbroad statements

- §3.4 "no observer ever sees the batch at its memory peak" — false for the final batch: `tick("write")` (1372) fires with it in memory; the legacy runs are that observation. `batch_full` is needed for non-final batches only.
- §1.3 / stream-20 — the report's 320 s "assemble" is presented as uniform; the RSS ticks show it is ~O(peer sets touched), which changes M6 and the budget's elapsed projection.
- §0 "the 9.6 GB store was not opened" — true for the report author; the harness children opened it read-only concurrently (`-shm` mtime 19:57:43; `.db` unchanged at 9,632,415,744 B, 2026-09-21 16:07:48). §8 (v) understates the concurrent activity: engine, harness, pilot DB and three reports all moved during review.
- §3.3 does not name `run_sizing_pilot.py` (239-247) as the consumer: it passes no `batch_size` and uses `measure=True, per_table_scopes=True`, so the four-date rerun would take the R6 metered path by default.

### 5. Sources present in the repo but not consulted

`pilot_sizing_report.json` (primary for WAL peaks 61,350,952 / 69,281,952 with `wal_peak_is_lower_bound: true`, the double count 396084/121872/11164, sampler stats, the driver traceback line map); `capacity_exp_walsat_idx.json` and the four other `capacity_exp_*.json` (the batched saturation experiment behind 119,992.4 WAL B/entity-date); `pilot_pristine_manifest.json` (two concatenated JSON documents — `json.load` fails at line 494; records the dirty pre-pilot code state); tests `test_free_space_guard` (296-311), `test_per_date_free_space_abort` (314-372), `test_checkpoint_return_is_read` (948-985), `test_pit_replay_manifest.py` gate tests, and `pit_replay_meter.validate()` (1999) / `main()` (2371). No test exercises `FREE_SPACE_FLOOR_MID_DATE`, `write_batch`, `per_table_scopes=True` with batching, or `measure=True`.

### 6. Machine context missing from §7.2 M4 (FACT, 20:00 ET)

FreePhysicalMemory 1.446 GiB of 7.452; pagefile 7631 / 1362 / 1545 MB; C: free 13.686 GiB. Three python processes started 19:10-19:12 hold 1,833 / 1,135 / 1,601 MiB commit with 3 / 3.5 / 154 MiB resident (PIDs 6648, 9292, 14576 pythonw). The RSS children ran at avail_phys 1,698-1,742 MiB.

### 7. Recommendations (separate from the facts above)

1. Re-issue the record against the four RSS reports; state that M1 fails as defined and why that may be the page cache, not the lists.
2. Run stream 1k and 4k with `--cache-kib 2000` on 2019-06-28, then one stream 1k child on **2022-06-30** (the incident date, never measured) with child-side `PagefileUsage`; add the `tracemalloc` snapshot at `primitives` / `assemble` / first batch as a required, not optional, measurement.
3. Fix the harness to open the child's reported PID (or spawn `sys._base_executable`) before any number from it is quoted as the engine's.
4. Add tests for the per-batch guard, the exact-multiple flush, and the `per_table_scopes=True` batched path; run `python pit_replay_meter.py` after any change to `_rows_written`.
5. Update `run_sizing_pilot.py` explicitly for `batch_size` before Phase 5; repair or split `pilot_pristine_manifest.json`.