# spec_freeze_v4 governance audit and engine-gap audit (2026-09-22)

**Provenance.** Produced 2026-09-22 by the post-reboot session's automated investigation: three independent finders per track, one synthesizer, then three adversarial verifiers (citation checker, refuter, completeness critic). Verifier verdicts and corrections are appended verbatim at the end. Line numbers refer to the working tree at the time of the investigation; `pit_replay.py` was being edited concurrently (the streaming change), so its line numbers may have moved again. Facts and recommendations are separated as labelled. Nothing in this document is a decision.

---

# spec_freeze_v4 governance audit and engine-gap audit

Repository: `C:\Users\logan\shafferfineval-app\shafferfineval` (git root one level up). Audit date 2026-09-22. READ-ONLY: no file in the repository was created, edited, moved or deleted by this audit; `git status --porcelain` shows the same four pre-existing entries before and after (` M pit_replay.py`, ` M test_pit_replay.py`, `?? pit_archive/options/`, `?? pit_replay_rss.py`). No pytest, no replay, no import of `pit_replay`, no open of the 9.6 GB main store. One read-only immutable query was run on the 171 MB pilot DB. All line numbers below were re-pinned against the files as they are now; where a finder's number is stale it is noted.

## 0. Method, provenance, and one thing that moved under the audit

**[v4-00] pit_replay.py changed in the working tree while the audit was running.** `git status` shows ` M shafferfineval/pit_replay.py` (210 insertions, 55 deletions vs HEAD `2e9db18`), mtime `2026-09-22 19:33:50`; the finders reported mtime `Sep 21 19:34`. The diff is the Phase 2 streaming work: `DEFAULT_BATCH_SIZE = 500` (`pit_replay.py:187`), `class FreeSpaceAbort(RuntimeError)` (`:202`), `run_date(..., batch_size=...)` (`:1006`), plus `test_pit_replay.py` (+101 lines, "(10) THE STREAMED WRITE SIDE") and a new untracked `pit_replay_rss.py` ("Peak-RSS harness for the streamed replay"). The diff hunks touch only batching/free-space code; **eligibility, `FACTOR_PLAN`, `REASON_WHY`, `RAW_FIELD`, `normalise()` are unchanged in substance** (verified by re-reading them). Consequences: (a) every pit_replay.py line number the finders cited is stale by +12 before line ~1000 and by up to +86 after; this report uses the current numbers; (b) the live freeze recomputes to `912268b3ec11bfc641fcac78078a0fb288cc51ce5432f8c27f931a532d1faae9`, intact=True, 47 components, 0 drifted (my `-B` run, no bytecode written into the repo) — the engine changed materially and the freeze did not move, which is correct for a non-model change and is exactly the mechanism examined in §1.4.

**Mutation numbers** (§1.3) are Finder 2's results from a scratch copy under `C:\Users\logan\AppData\Local\Temp\claude\v4mut\src` (fresh interpreter, `__pycache__` removed, `python -B`). I did not re-run them; I re-verified that each mutated line exists as described in the current repository files. The one mutation site inside pit_replay.py (growth denominator) is now at `pit_replay.py:770-771`, not 759.

**No "handoff" document exists in the repository**: a recursive grep for `handoff`/`hand-off` over `*.md *.py *.txt *.json` (excluding `.venv`, `.git`) returns nothing. The "handoff's required v4 contents" and the claims "v4 required" / "v3 latest" are taken from the task text; the in-repo equivalents are `docs/MODEL-LINEAGE.md:1113-1128` and `docs/POST-REBOOT-CHECKLIST.md:1-73`.

---

## 1. What spec_freeze_v4 digests, component by component

### 1.1 Mechanism (FACT)

**[v4-01]** `manifest()` reads every `(module, attribute)` in `COMPONENTS` live via `importlib` (`pit_frozen_spec.py:323-327`); an attribute ending in `()` is called (`:325-326`; only `candidate_v2_registry()` uses this, `:279`). A value that is `str/int/float/bool` renders by `repr` (`:366-367`); anything else by `_canonical` = `json.dumps(value, sort_keys=True, default=enc, separators=(",",":"))` (`:354`). `enc` order: `as_dict()` → `dataclasses.asdict` → sorted `str()` of set/frozenset → `"callable:<qualname>"` → sorted `vars()` → `"unrenderable:<type>"` (`:339-353`). Missing module/attr renders as `MISSING(...)` instead of raising (`:363-364`). `digest()` is SHA-256 over `key=rendered` lines sorted by key (`:374-378`). `FROZEN_DIGEST = "912268b3…faae9"` (`:383`).

**[v4-02] Live state (verified by recompute, this session):** digest `912268b3…faae9` == `FROZEN_DIGEST`; `verify()` intact=True, n_components=47, missing=[], drifted=[]; no manifest value contains `callable:`, `unrenderable:` or `MISSING(`; kinds = **16 bodies, 5 numerics, 26 strings**. Matches `docs/POST-REBOOT-CHECKLIST.md:9-10` and `test_pit_frozen_spec.py:66-70`.

### 1.2 The 47 components (FACT; `pit_frozen_spec.py:256-320`)

| line | module:attribute | kind | render | governs |
|---|---|---|---|---|
| 258 | pit_factor_spec:CANDIDATE_MODEL_VERSION | STR | repr | lineage id |
| 259 | pit_score_signature:SIGNATURE_VERSION | STR | repr | score comparability |
| 260 | pit_score_signature:COMPANY_SCALE_V2 | NUM 0.85 | repr | company score scale |
| 261 | pit_score_signature:V2_MAJOR_WEIGHTS | BODY dict n=4 | JSON | block weights (`pit_score_signature.py:349-354`) |
| 262 | pit_score_signature:PILLARS | BODY tuple n=4 | JSON | **labelled "the block SET" but is v1's pillar tuple** (see v4-07) |
| 263 | pit_factor_blocks:FACTOR_BLOCK | BODY dict n=12 | JSON | factor→block (`pit_factor_blocks.py:132`) |
| 264 | pit_factor_blocks:BLOCK_MAP_VERSION | STR | repr | |
| 265 | pit_factor_blocks:MIN_BLOCK_WEIGHT | NUM 0.40 | repr | block-coverage floor (`pit_factor_blocks.py:271`) |
| 266 | pit_factor_blocks:WITHIN_BLOCK_WEIGHTS | BODY dict n=4 | JSON | E/Q/G weights; V renders `{}` by design (`pit_factor_blocks.py:432-445`) |
| 267 | pit_factor_blocks:POLICY_WEIGHTS_VERSION | STR | repr | |
| 268 | pit_factor_blocks:POLICY_WEIGHTS_STATUS | STR | repr | |
| 269 | pit_factor_blocks:FACTOR_ROLE | BODY dict n=12 | JSON | scoring vs challenger |
| 270 | pit_factor_blocks:V_FACTOR_SUBFACTORS | BODY dict n=2 | JSON | V granularity bridge |
| 271 | pit_valuation_spec:SUBFACTOR_WEIGHTS | BODY dict n=3 | JSON | V within-block weights |
| 275 | pit_factor_spec:FACTOR_SPEC_VERSION_V3 | STR (v4 add) | repr | `"factor_spec_v3"` (`pit_factor_spec.py:197`) |
| 276 | pit_factor_spec:SPECS_V3 | BODY tuple of 14 FactorSpec (v4 add) | `as_dict` | all 14 factor-spec bodies (`pit_factor_spec.py:1218`) |
| 277 | pit_factor_spec:PRICED_EPS_V2 | BODY PeerEligibility (v4 add) | `as_dict` | corrected P/E eligibility, `unavailable_primitives=()` (`pit_factor_spec.py:534-546`) |
| 278 | pit_factor_spec:REPLACED_BY_VERSION | BODY dict[str,frozenset] (v4 add) | JSON (sorted list) | `{"factor_spec_v3":["ebitda_benchmark","pe_ratio"]}` (`:1214-1216`) |
| 279 | pit_normalization:candidate_v2_registry() | BODY dict of 6 FactorSpec (v4 add, CALLED) | `as_dict` + nested `ScaleSpec.as_dict` | registry body (`pit_normalization.py:1766`, keys at 1818-1878) |
| 280 | pit_factor_blocks:DIRECTION_POLICY_VERSION | STR (v4 add) | repr | |
| 281 | pit_factor_blocks:FACTOR_DIRECTION_V1 | BODY dict n=12 (v4 add) | JSON | direction table (`pit_factor_blocks.py:649-665`) |
| 282 | pit_factor_blocks:SUBFACTOR_DIRECTION_V1 | BODY dict n=3 (v4 add) | JSON | (`:670-674`) |
| 283 | pit_valuation_spec:SUBFACTORS | BODY dict of 3 Subfactor (v4 add) | `as_dict` | valuation subfactor bodies (`pit_valuation_spec.py:403+`) |
| 286-288 | pit_policy:LATENCY_POLICY_VERSION, LADDER_VERSION, LADDER_VERSION_V2 | STR ×3 | repr | PIT rules |
| 291-293 | pit_valuation_spec:VALUATION_SPEC_VERSION, PRESENCE_RULE_VERSION, ANCHOR_POLICY_VERSION | STR ×3 | repr | |
| 294 | pit_valuation_spec:PE_ABSOLUTE_ANCHOR_V1 | NUM 20.0 | repr | anchor level (`pit_valuation_spec.py:711`) |
| 295 | pit_valuation_spec:TRANSFORM_VERSION | STR | repr | **the convex transform is covered by this string only** (v4-09) |
| 296 | pit_valuation_spec:DEBT_RUNG_POLICY_VERSION | STR | repr | |
| 297 | pit_valuation_spec:DEBT_RUNG_EVEBITDA_V1 | BODY dict n=6 | JSON | (`pit_valuation_spec.py:2032-2040`) |
| 300-302 | pit_price_basis:PRICE_BASIS_POLICY_VERSION, EPS_PAIR_POLICY_VERSION, ACTION_GATE_VERSION | STR ×3 | repr | **EPS pair hierarchy body `PAIR_PATHS` not digested** (v4-09) |
| 303 | pit_price_basis:EPS_GROWTH_MIN_POSITIVE_BASE_V1 | NUM 0.01 | repr | |
| 304 | pit_price_basis:PRICE_ROLE_REQUIRED_BASIS | BODY dict n=2 | JSON | (`pit_price_basis.py:171-174`) |
| 307-308 | pit_eps:EPS_LADDER_VERSION, TTM_RULE_VERSION | STR ×2 | repr | |
| 311 | pit_factor_spec:FACTOR_SPEC_VERSION_V2 | STR | repr | still digested beside V3 |
| 312 | pit_normalization:NORMALIZATION_POLICY_VERSION | STR | repr | |
| 313 | pit_coverage_contract:CONTRACT_VERSION | STR | repr | |
| 314 | pit_share_coverage:PIT_SHARE_STATE_COVERAGE_V2 | NUM 72.25 | repr | a measured result, not a rule |
| 315 | pit_share_coverage:CANONICAL_DEFINITION_ID | STR | repr | |
| 318 | pit_invariants:SPEC_VERSION | STR | repr | |
| 319 | pit_intermediates:INTERMEDIATE_SCHEMA_VERSION | STR | repr | |

Totals: 16 BODY + 5 NUM + 26 STR = 47. Modules represented: pit_coverage_contract, pit_eps, pit_factor_blocks, pit_factor_spec, pit_intermediates, pit_invariants, pit_normalization, pit_policy, pit_price_basis, pit_score_signature, pit_share_coverage, pit_valuation_spec. **Not represented: pit_replay, pit_derive, pit_coverage, pit_shares, pit_rawprice.**

**[v4-03] The v4 additions are 7 bodies + 2 version strings**, not "nine BODIES" as `docs/MODEL-LINEAGE.md:1117` and the `COMPONENTS` comment at `pit_frozen_spec.py:273-274` say: lines 275 (`FACTOR_SPEC_VERSION_V3`) and 280 (`DIRECTION_POLICY_VERSION`) are strings.

**[v4-04] Field coverage of the body dataclasses (FACT, Finder 1 probe + my read):** every body dataclass hits the `as_dict` branch, so coverage is what `as_dict` emits. `PeerEligibility.as_dict` emits all fields incl. `unavailable_primitives` (`pit_factor_spec.py:401-413`, Finder 1). `pit_factor_spec.FactorSpec.as_dict` (`:708-743`) emits 16 of 17 fields unconditionally and `max_admissible_rung` (+ two derived lists) only when set (`:739-742`); since key presence toggles with the value, setting/clearing a ceiling still moves the render. Cosmetic: it stamps `spec_version` as v1/v2, never v3, because `_canonical` calls it without `version` (`:734-736`). `pit_normalization.FactorSpec.as_dict` includes nested `ScaleSpec.as_dict` with `fallback_fixed` (`pit_normalization.py:360-366`, Finder 1), which is why `DEFAULT_EXCESS_MARGIN_SCALE_K` (`:304`) reaches the digest through `:1835-1836`. **No FactorSpec has a formula or denominator field** (`pit_factor_spec.py:650-666`): the spec body digests the NAME of each scored quantity (`member_quantity`, `graph_node`, prose in `graph_divergence`/`note`), never its arithmetic.

### 1.3 Mutation-test table — proof of body coverage (FACT, Finder 2's scratch-copy runs; sites re-verified by me)

Baseline: digest `912268b3…faae9`, intact=True, 47 components.

| # | mutation | file:line (current) | digest moved? | drifted key |
|---|---|---|---|---|
| 1a | drop `"revenue"` from `_to_v3` ebitda_benchmark required_primitives | pit_factor_spec.py:1193-1194 | YES `a004b6fb…` | pit_factor_spec:SPECS_V3 |
| 1b | drop `"revenue"` from `EBITDA_AND_REVENUE.primitives` | pit_factor_spec.py:438 | YES `9415455d…` | pit_factor_spec:SPECS_V3 |
| 2 | `ev_ebitda` LOWER→HIGHER_IS_BETTER | pit_factor_blocks.py:651 | YES `c4582467…` | pit_factor_blocks:FACTOR_DIRECTION_V1 |
| 3 | E-block `ebitda_benchmark` 0.35→0.36 | pit_factor_blocks.py:434 | YES `a464326f…` | pit_factor_blocks:WITHIN_BLOCK_WEIGHTS |
| 4 | `PE_ABSOLUTE_ANCHOR_V1` 20.0→21.0 | pit_valuation_spec.py:711 | YES `34521603…` | pit_valuation_spec:PE_ABSOLUTE_ANCHOR_V1 |
| 5 | `LongTermDebtNoncurrent` REJECTED→ELIGIBLE | pit_valuation_spec.py:2036 | YES `91a1abb7…` | pit_valuation_spec:DEBT_RUNG_EVEBITDA_V1 |
| 6a | `DEFAULT_EXCESS_MARGIN_SCALE_K` 0.06→0.07 | pit_normalization.py:304 | YES `2fac4f09…` | pit_normalization:candidate_v2_registry() |
| **6b** | 50-75 band lower percentile 0.50→0.40 | pit_normalization.py:923 | **NO — intact=True** | **none — HOLE** |
| **6c** | 50-75 band upper percentile 0.75→0.80 | pit_normalization.py:924 | **NO — intact=True** | **none — HOLE** |
| 7 | `MIN_BLOCK_WEIGHT` 0.40→0.45 | pit_factor_blocks.py:271 | YES `b243f87a…` | pit_factor_blocks:MIN_BLOCK_WEIGHT |
| 8 | `PRICED_EPS_V2.unavailable_primitives=()`→`("earnings_per_share_pit",)` (re-introduce v2 defect) | pit_factor_spec.py:537 | YES `806ba26d…` | PRICED_EPS_V2 **and** SPECS_V3 (pe_ratio embeds it, `:1207`) |
| 9a | SPECS_V3 ebitda_growth `member_quantity` text | pit_factor_spec.py:848 (Finder-reported) | YES `b3e60268…` | pit_factor_spec:SPECS_V3 |
| **9b** | pit_derive formula string `/ abs(ebitda_t-1)`→`abs(ebitda_t-2)` | pit_derive.py:481 | **NO** | **none — HOLE** |
| **9c** | **engine arithmetic** growth denominator `assets_lag1`→`ebitda_lag1` | pit_replay.py:770-771 (was 759) | **NO — intact=True** | **none — HOLE: executed model changed, gate would pass** |
| 9d | registry note `/ Assets_{t-4q}`→`/ Assets_{t}` | pit_normalization.py:1850 | YES `f886cff6…` | pit_normalization:candidate_v2_registry() |
| 9e | pit_coverage FEATURE_NOTE denominator text | pit_coverage.py:566 | NO | none (documentation text) |
| 10 | docstring-only `0.40`→`0.41` (control) | pit_factor_blocks.py:52 | NO (correct) | none |
| 10b | docstring-only edit (control) | pit_frozen_spec.py:25 | NO (correct) | none |

Bytecode hazard noted by Finder 2: a same-size edit within the same wall-clock second reused a stale `.pyc` on its first M6b run; all numbers above are from cache-cleared `-B` runs.

### 1.4 Governance holes (FACT)

**[v4-05] HOLE A — the EBITDA benchmark band and cohort floor are literals inside a function.** `benchmark_margin_50_75` (`pit_normalization.py:901-931`) holds `percentile(ordered, 0.50)` / `percentile(ordered, 0.75)` (`:923-924`) and `min_cohort: int = 3` (`:903`) as inline literals. Functions are never rendered (the digest holds only `callable:<qualname>` if one appears, and none does). The digested registry body carries only the label `anchor_source=ANCHOR_COHORT_50_75_MEAN_MARGIN` (`:1834`) and prose; SPECS_V3 names the function in prose (`pit_factor_spec.py:1200`). Mutations 6b/6c prove it: the bar for the model's largest single factor weight (0.35 × 0.35 = 12.25%, `pit_replay.py:378`) moves and the freeze reads intact.

**[v4-06] HOLE B — no executable definition of any factor is digested.** No `pit_replay`, `pit_derive` or `pit_coverage` attribute is in `COMPONENTS` (§1.2). Mutation 9c changed the growth arithmetic at `pit_replay.py:770-771` with intact=True. `pit_replay_manifest.gate()` refuses only on `verify()["intact"]` (plus can_execute_replay, empty tables, free space — `pit_replay_manifest.py:57-62, 915-916`), and `run_pilot` stamps `verify()["current_digest"]` before and after (`pit_replay.py:1460-1461, 1663-1666`). An engine with altered arithmetic passes the gate and stamps rows with the unchanged v4 digest. The freeze digests the NAME and PROSE of each scored quantity, not the code that computes it. (The in-flight streaming edit, v4-00, is a live demonstration of the same property.)

**[v4-07] Label / order gap.** `COMPONENTS` labels `pit_score_signature.PILLARS` "the block SET" (`pit_frozen_spec.py:262`), but `PILLARS` is v1's `(valuation, growth, profitability, debt)` (`pit_score_signature.py:152-153`). The v2 block tuple `V2_BLOCKS` — whose ORDER is documented as load-bearing for signature equality (`:319-323`) — is not in `COMPONENTS` (verified). Block MEMBERSHIP is covered indirectly by the keys of `V2_MAJOR_WEIGHTS` and `WITHIN_BLOCK_WEIGHTS`; the canonical order is not.

**[v4-08] Latent seal-check gap.** `validate()` rejects a manifest containing `unrenderable:` (`pit_frozen_spec.py:934-937`) but does not scan for `callable:` (`:349-350`). A body that later acquires a function-valued field would pass `validate()` with the function's content outside the digest. Not triggered today.

**Doc-comment inconsistency (minor).** `pit_frozen_spec.py:254-255` says a non-scalar "is hashed by its repr"; the code hashes canonical JSON (`:366-369`) and the `_canonical` docstring explains why repr was rejected (`:333-337`).

**What is proven sound (FACT):** `validate()` proves by perturbation that the digest depends on all 47 keys (`pit_frozen_spec.py:865-870`); `FREEZE_V3` is sealed (`:181-209`: digest `2f9bba31…`, 38 components, `STATUS_SUPERSEDED`, reason `DIGEST_COVERED_VERSION_STRINGS_NOT_BODIES`, 0 main-store rows, `superseded_by: "spec_freeze_v4"`), guarded by `validate()` (`:916-929`), pinned by `test_pit_frozen_spec.py:106-127`, and recorded in `docs/MODEL-LINEAGE.md:1098-1110`. The in-process drift test `test_the_freeze_detects_its_own_violation` (`test_pit_frozen_spec.py:150-234`) covers anchor, debt rung, SPECS_V3 body, direction flip and a tampered registry — all components already in `COMPONENTS`; it has no negative case for a body outside `COMPONENTS`. Git holds one commit touching `pit_frozen_spec.py` (`2e9db18`), so "FREEZE_V3 unchanged since pre-v4" cannot be diffed, only checked against seals.

---

## 2. Are the handoff's required v4 contents covered? (FACT)

Reference for "required contents": the task's list, `docs/MODEL-LINEAGE.md:1120-1125` ("What v4 digests that v3 did not"), `docs/POST-REBOOT-CHECKLIST.md:9-10`.

| required item | covered by BODY | covered by version STRING only | omission |
|---|---|---|---|
| factor specs (all 14, margin `ebitda_benchmark`, corrected P/E) | YES — SPECS_V3, PRICED_EPS_V2, REPLACED_BY_VERSION (`pit_frozen_spec.py:276-278`); `validate()` also asserts `"revenue" in BY_KEY_V3["ebitda_benchmark"].required_primitives` and `pe_ratio…unavailable_primitives == ()` (`:938-943`) | — | arithmetic of the benchmark not covered (v4-05) |
| normalization registry | YES — `candidate_v2_registry()` (`:279`), 6 entries incl. ScaleSpec | — | registry has no entry for the 5 Q/V keys (that is an engine/spec gap, §3, not a digest gap) |
| factor-direction table | YES — FACTOR_DIRECTION_V1 n=12, SUBFACTOR_DIRECTION_V1 n=3 (`:281-282`) | + DIRECTION_POLICY_VERSION (`:280`) | none |
| factor→block map | YES — FACTOR_BLOCK n=12 (`:263`) | + BLOCK_MAP_VERSION | none |
| within-block weights | YES — WITHIN_BLOCK_WEIGHTS (E/Q/G), SUBFACTOR_WEIGHTS (V) (`:266, 271`) | | none |
| block floor | YES as NUMBER — MIN_BLOCK_WEIGHT 0.40 (`:265`) | | none |
| V subfactor map / bodies | YES — V_FACTOR_SUBFACTORS, SUBFACTORS (`:270, 283`) | | none |
| debt-rung policy | YES — DEBT_RUNG_EVEBITDA_V1 n=6 (`:297`) | + DEBT_RUNG_POLICY_VERSION | none |
| price-basis policy | YES — PRICE_ROLE_REQUIRED_BASIS n=2 (`:304`) | + PRICE_BASIS_POLICY_VERSION | none for PRICE basis |
| P/E anchor policy | anchor LEVEL as number (PE_ABSOLUTE_ANCHOR_V1, `:294`) | ANCHOR_POLICY_VERSION, **TRANSFORM_VERSION** (`:293, 295`) | **[v4-09a]** transform body `TRANSFORM_A=25.0, TRANSFORM_THETA=ln2, TRANSFORM_C=60.0, TRANSFORM_FLOOR_MULTIPLE=10.0, TRANSFORM_FLOOR=-100.0, TRANSFORM_B=solve_b()` (`pit_valuation_spec.py:878-915`) — none in COMPONENTS |
| EBITDA benchmark definition | declaration only (SPECS_V3 body; registry `anchor_source`, `fallback_fixed 0.06`) | | **[v4-05]** band 0.50/0.75 and min_cohort=3 not digested |
| share-basis policy | — | EPS_PAIR_POLICY_VERSION, ACTION_GATE_VERSION, CANONICAL_DEFINITION_ID + measured number PIT_SHARE_STATE_COVERAGE_V2=72.25 (`:301-302, 314-315`) | **[v4-09b]** `pit_price_basis.PAIR_PATHS` (`pit_price_basis.py:266-268`) not in COMPONENTS; no component from `pit_shares` / `pit_rawprice` |
| v2 block set / order | membership indirectly (weights keys) | | **[v4-07]** V2_BLOCKS absent; PILLARS mislabelled |

Summary: the nine items the lineage doc lists are all present as it describes. The omissions are (i) the P/E convex transform parameters, (ii) the benchmark band/floor literals, (iii) the EPS pair hierarchy body and any share-class guard, (iv) V2_BLOCKS order, and (v) any executable definition (v4-06).

---

## 3. Engine gaps vs the v4 specification (FACT)

### 3.1 Where the engine sits

**[v4-10] pit_replay.py derives peer eligibility from `factor_spec_v2`, not v3.** `FACTOR_SPEC_VERSION = "factor_spec_v2"` (`pit_replay.py:169`) is passed explicitly at the single eligibility call site `pit_factor_spec.eligible_peers(..., version=FACTOR_SPEC_VERSION)` (`:860-862`) and stamped into score provenance (`:1337`). The tokens `SPECS_V3`, `PRICED_EPS`, `PRICED_EPS_V2`, `FACTOR_SPEC_VERSION_V3` do not occur in pit_replay.py. Omitting `version` would fall to `DEFAULT_SPEC_VERSION = "factor_spec_v1"` (`pit_factor_spec.py:183, 202, 2143`), not v3. The docstring still reads "The executable half of `spec_freeze_v3`" (`pit_replay.py:8`) while `SPEC_FREEZE_VERSION = "spec_freeze_v4"` (`pit_frozen_spec.py:212`). `pit_frozen_spec.STILL_BLOCKED` records this itself: "SPECIFICATION complete as of spec_freeze_v4; ENGINE not … (b) derives eligibility from factor_spec_v2 … (c) computes 5 of 13 factors" (`pit_frozen_spec.py:594-603`).

**[v4-11] Verdicts are a literal table that reads no spec.** `FACTOR_PLAN` (`pit_replay.py:371-458`); `plan_record()` "Reads nothing" (`:505`). For any non-COMPUTABLE key `run_date` short-circuits at `if plan.verdict != VERDICT_COMPUTABLE: reason = plan.refusal_reason` (`:1192-1194`) before primitives, cohort or `eligible_peers` — the eight refusals are the engine's own declarations, not outcomes of the v2 (or v3) rule.

**[v4-12] Provenance hazard.** The engine stamps `pit_frozen_spec.SPEC_FREEZE_VERSION` and the live `verify()["current_digest"]` on run rows, score provenance and `resolved_inputs_json` (`pit_replay.py:1330-1331, 1475-1476, 1541`). A rerun today would label rows `spec_freeze_v4` / `912268b3…` while deriving eligibility from `factor_spec_v2` and refusing P/E with a reason the v4-digested body says is false.

### 3.2 The 13 factor keys

| key | block | status | verdict / reason (`pit_replay.py`) | root cause |
|---|---|---|---|---|
| ebitda_benchmark | E | **REFUSED** | REFUSED_AMBIGUOUS / `benchmark_definition_ambiguous` (`:373-380`) | Refusal text `:247-265` describes the dollar-vs-margin ambiguity that `factor_spec_v3` has since decided for MARGIN (`pit_factor_spec.py:1189-1203`; `docs/MODEL-LINEAGE.md:1135-1149`). Engine never calls `benchmark_margin_50_75` (mentions only at `:54, :255`); `RAW_FIELD` maps the key to EBITDA dollars (`:810`); plan has `needs_cohort=True` but no `cohort_use`, so `normalise()` would fall through to `normalize(spec, value, as_of_date=as_of)` (`:918`) with no `benchmark=` kwarg, which `normalize`'s BENCHMARK_ANCHORED branch requires (`pit_normalization.py:858, 879-885`). Computed as NEITHER reading. |
| ebitda_growth | E | WIRED | COMPUTABLE / EBITDAGrowth, dispersion cohort (`:381-386`) | arithmetic `:762-771`, denominator `assets_lag1` (D3 alternative B, undecided) |
| ebitda_efficiency | E | WIRED | COMPUTABLE / EBITDAMarginRank, rank cohort (`:387-394`) | — |
| ebitda_acceleration | E | WIRED | COMPUTABLE / EBITDAAcceleration (`:395-400`) | — |
| ebitda_scale | E | WIRED (zero-weight challenger) | COMPUTABLE / EBITDAScale (`:401-410`) | — |
| pe_absolute | V | **MISSING** | NOT_COMPUTABLE / `primitive_genuinely_unavailable` (`:413-416`) | REASON_WHY says "EPS IS NOT IN THIS STORE" (`:275-281`) — false: `pit_eps_obs` 888,486 rows (`pit_factor_spec.py:518-520`), `PRICED_EPS_V2.unavailable_primitives=()` (`:537`). No EPS/price/listing fields in `Primitives` (`:644-675`); `listing_id` NULL on every row (`:490-492`); no call to the valuation transform. |
| pe_relative | V | **MISSING** | same (`:417-420`) | same |
| ev_ebitda_supplement | V | **MISSING** | NOT_COMPUTABLE / `no_v2_normalization_declared` (`:421-429`) | no `Primitives` fields for price/shares/debt/cash; `normalise()` reads only `_REGISTRY[plan.norm_key]` (`:890, :909`) and the registry has six keys (`pit_normalization.py:1818-1878`), none for V/Q; no cohort-median anchor code. The spec DOES declare `BENCHMARK_ANCHORED` (`pit_factor_spec.py:916`) and `SUBFACTORS` declares `ANCHOR_COHORT_MEDIAN` (`pit_valuation_spec.py:464-465`). |
| real_revenue_growth | G | WIRED | COMPUTABLE / RealRevenueGrowth, no cohort (`:432-439`) | — |
| fcf_conversion | Q | **MISSING** | NOT_COMPUTABLE / `no_v2_normalization_declared` (`:442-445`) | no primitives, no arithmetic, no registry entry; spec declares PERCENTILE_RANK (`pit_factor_spec.py:1071`), direction HIGHER (`pit_factor_blocks.py:652`) |
| interest_coverage | Q | **MISSING** | same (`:446-449`) | as above (`:1054`, direction `:653`); numerator is D3-undecided: `operating_income` (`pit_derive.py:555`) vs `EBITDA` (`pit_coverage.py:578`) |
| net_debt_ebitda | Q | **MISSING** | same (`:450-453`) | as above (`:986`, direction `:654`) |
| debt_market_cap | Q | **MISSING** | same (`:454-457`) | as above (`:1007`, direction `:655`) |

Census: 5 COMPUTABLE / 1 REFUSED_AMBIGUOUS / 7 NOT_COMPUTABLE; reachable block weight 0.50 (`KNOWN_LIMITATIONS`, `pit_replay.py:495-497`); pinned by `test_pit_replay.py:157`.

**[v4-13] Pilot DB confirms (my read-only immutable query):** `pit_replay_run` row 1 `status='running'`, `source_versions_json` = `{"spec_freeze_version": "spec_freeze_v3", "freeze_digest": "2f9bba31…", "engine": "pit_replay/1.0"}`, `summary_json.verdict_counts = {REFUSED_AMBIGUOUS:1, COMPUTABLE:5, NOT_COMPUTABLE:7}`, `reachable_block_weight 0.5`. Each of the 8 keys has exactly 15,234 `available_flag=0` rows with the reason above. `pilot_sizing_report.json` has `replay_report: null` and `error.type = MidDateFreeSpaceAbort` (the run aborted before the report was assembled); the plan record lives only in the pilot DB.

**[v4-14] The engine's stated reason for the five Q/V refusals is partly inaccurate.** `REASON_WHY[R_NO_V2_NORMALIZATION]` says pit_factor_spec "sets `normalization` to None on every spec" (`pit_replay.py:270-271`). There is no `normalization` attribute; `FactorSpec.normalization_type` (`pit_factor_spec.py:654`) is declared on every one of the five (PERCENTILE_RANK ×4, BENCHMARK_ANCHORED for ev_ebitda). The precise gap is: no `candidate_v2_registry()` entry (the only thing `normalise()` consults) and no anchor computation.

**[v4-15] Direction is not consumed by the engine.** The token `direction` does not occur in `pit_replay.py`. `FACTOR_DIRECTION_V1`, `SUBFACTOR_DIRECTION_V1`, `direction_of()` (`pit_factor_blocks.py:649-700`) and the rule `DIRECTION_IS_ORIENTATION_ONLY` (`:619-624`) are never read; the only orientation on the computable path is the registry spec's own `higher_is_better` inside `percentile_rank_score` (`pit_normalization.py:870-873`).

### 3.3 GAP 5 / 7 / 8

The numbering exists only at `docs/POST-REBOOT-CHECKLIST.md:62-63`; no numbered GAP list is anywhere else in the repository. Each gap is real and none is fixed:

- **[v4-16] GAP 5 — peer floor = max(spec, normaliser): NOT FIXED.** Spec floors: `min_peer_count=3` for the rank factors (`pit_factor_spec.py:800, 825, 949, 987, 1008, 1024, 1040, 1055, 1072`), 12 for `ebitda_benchmark`/`ev_ebitda` (`:761, 917`); `eligible_peers` gates on it at `:2244`. Normaliser floors `MIN_RANK_PEERS = 4` (`pit_normalization.py:318`), `MIN_DISPERSION_COHORT = 8` (`:310`). The engine checks only the eligibility verdict (`pit_replay.py:1211-1212`) and lets the normaliser refuse afterwards inside `normalise()`; no `MIN_RANK`/`MIN_DISPERSION`/`min_peer` token exists in pit_replay.py. Measured second-stage refusals in the pilot DB: `cohort_too_small_for_dispersion` 483 (ebitda_acceleration) / 424 (ebitda_growth) — verified by my query; `cohort_too_small_to_rank` 93 / 62 (Finder 3).
- **[v4-17] GAP 7 — price-basis translation constant: NOT FIXED.** `PRICED_EPS` (and therefore `PRICED_EPS_V2`, every v3 spec) names `price_adjusted` (`pit_factor_spec.py:502, 534-546`); `pit_price_basis.PRICE_ROLE_REQUIRED_BASIS` requires `PRICE_RAW_AS_TRADED` for the valuation role (`pit_price_basis.py:171-174`); `pit_valuation_spec.SUBFACTORS` declares `PRICE_RAW_AS_TRADED` (`pit_valuation_spec.py:460`) and `KNOWN_LIMITATIONS["pe_ratio_price_basis_conflict"]` records the divergence (`:2253-2277`). No translation/mapping constant exists in code; the engine computes no price-dependent factor (`pit_replay.py:490-492`). Because `SPECS_V3` is a digested body, changing the primitive name is a v5 event.
- **[v4-18] GAP 8 — explicit `version='factor_spec_v3'`: NOT FIXED** (`pit_replay.py:169, 860-862`).

### 3.4 Labelled diagnostic bypasses

**[v4-19] None exist.** The owner rule ("an engine-side bypass is permitted only for diagnostics and only when labelled") is at `pit_factor_spec.py:529-533`; the docs say "an engine override was explicitly refused by the owner" (`docs/MODEL-LINEAGE.md:1157-1158`). pit_replay.py contains no override of eligibility, `run_pilot()` (`:1429`) exposes no spec-version/eligibility parameter, and `test_pit_replay.py` contains no `FACTOR_SPEC_VERSION|PRICED_EPS|SPECS_V3|bypass` token (Finder 3). The only test that mutates an eligibility rule (`test_pit_factor_spec.section_1b_mutations`, Finder-reported) mutates v1 in try/finally to assert `validate()` fails and never runs the engine; the "deliberate bypass" in `test_pit_guard_acceptance.py:262` is the registry redundancy-guard waiver `register_factor(unchecked_reason=...)`, unrelated to eligibility.

### 3.5 D3

**[v4-20]** Undecided and the engine matches the record: growth uses `Assets_{t-4q}` (`pit_replay.py:762, 770-771`; `R_NO_ASSETS_LAG1`, `:227`); interest_coverage has no numerator because it is NOT_COMPUTABLE. `docs/POST-REBOOT-CHECKLIST.md:64-65` forbids changing either without an owner decision; `docs/MODEL-LINEAGE.md:1176-1184` and design record `docs/SHAFFER-EQUITY-V2-CANDIDATE.md:288-289` (open question 6, "Growth as an amount rather than a rate?") support growth-B. Mutation 9b/9c show the digest would not notice a code-side "resolution" either way.

---

## 4. Contradictions: handoff vs repository (FACT)

**[v4-21] "v3 latest" is STALE; "v4 required" is satisfied on the SPEC side and NOT on the ENGINE side.**
- Repository: `SPEC_FREEZE_VERSION = "spec_freeze_v4"` (`pit_frozen_spec.py:212`); `FROZEN_DIGEST = "912268b3…"` (`:383`); `FREEZE_V3["superseded_by"] = "spec_freeze_v4"` (`:204`); `docs/MODEL-LINEAGE.md:1113-1117` ("FREEZE — spec_freeze_v4, 2026-09-22 … components 47"); `docs/POST-REBOOT-CHECKLIST.md:9-10`; live recompute intact at 912268b3…, 47 components. So a handoff calling v3 the latest freeze predates 2026-09-22 18:58 (mtime of `pit_frozen_spec.py`).
- But the engine is unchanged from the v3 era on everything that matters: `pit_replay.py:8` "executable half of `spec_freeze_v3`", `:169` `factor_spec_v2`, `:371-458` literal verdicts; `STILL_BLOCKED` in the freeze module says so (`pit_frozen_spec.py:594-603`). If "v4 required" means "the engine must implement v4", that requirement is open (§3).

Other disagreements between docs, code and tests:

| where | says | but | which is stale |
|---|---|---|---|
| `docs/MODEL-LINEAGE.md:1157-1158` | "Production replay derives eligibility from the corrected spec" | `pit_replay.py:169, 860-862` derive from v2; `pit_frozen_spec.py:594-603` records the engine gap | the lineage sentence is a specification statement, not a description of code |
| `docs/MODEL-LINEAGE.md:1117`; `pit_frozen_spec.py:273-274` | "nine BODIES" | 7 bodies + 2 version strings (`:275, :280`) | docs wording |
| `pit_frozen_spec.py:254-255` | non-scalars "hashed by its repr" | canonical JSON (`:366-369`) | comment |
| `pit_frozen_spec.py:262` | PILLARS = "the block SET" | v1 pillars (`pit_score_signature.py:152-153`); V2_BLOCKS absent | label |
| `pit_replay.py:270-271` | spec "sets `normalization` to None" | `normalization_type` declared on every spec (`pit_factor_spec.py:654, 1071…`) | engine text |
| `pit_replay.py:275-281` | "EPS IS NOT IN THIS STORE" | 888,486 rows; `PRICED_EPS_V2` (`pit_factor_spec.py:518-520, 537`) | engine text (frozen in v3 era) |
| `pit_replay.py:8` | "executable half of spec_freeze_v3" | live freeze is v4 (`pit_frozen_spec.py:212`) | engine docstring |
| `test_pit_replay.py:157` | pins 5/1/7 census | v4 spec expects 13/13 (`docs/POST-REBOOT-CHECKLIST.md:66`) | test pins the current engine, must change with Phase 3 |
| `docs/POST-REBOOT-CHECKLIST.md:18` | "git HEAD c9b4050 … ~115 dirty paths" | HEAD is now checkpoint `2e9db18`; working tree has 2 modified + 2 untracked paths (Phase 2 streaming in flight) | checklist snapshot |
| task premise | `pilot_sizing_report.json` holds the replay_report | `replay_report: null`, `MidDateFreeSpaceAbort` | premise |
| finder citations | pit_replay.py lines e.g. 359/759/848/1106/1244 | now 371/771/860/1192/1330 after the uncommitted streaming edit | finder line numbers |

---

## 5. Recommendations (separate from the facts above; nothing here was implemented)

### 5.1 Phase 3 — engine wiring, in order, each with the spec source it must derive from

1. **Version switch.** `FACTOR_SPEC_VERSION = pit_factor_spec.FACTOR_SPEC_VERSION_V3` (source `pit_factor_spec.py:197`), passed at `pit_replay.py:860-862`, stamped at `:1337`; fix docstring `:8`. Add to `pit_replay.validate()` (`:1677`) an assertion that the engine's spec version equals the one v4 digests (`pit_frozen_spec.py:275`) so a mismatch refuses rather than mislabels (v4-12).
2. **ebitda_benchmark, MARGIN reading.** Source `BY_KEY_V3["ebitda_benchmark"]` (`pit_factor_spec.py:1189-1203`): cohort eligibility `EBITDA_AND_REVENUE` (`:436`), per-member `(ebitda, margin)` pairs from `Primitives.margin` (`pit_replay.py:668`) through `CohortCache`, `M_s` from `benchmark_margin_50_75` (`pit_normalization.py:901`), passed as `benchmark=` into `normalize()` (`:858`) with `cohort_values` for the registry scale (`:1835-1836`); `RAW_FIELD["ebitda_benchmark"]` → margin (`pit_replay.py:810`); plan verdict COMPUTABLE with a declared `cohort_use`; retire `R_BENCHMARK_AMBIGUOUS` text to history.
3. **GAP 5 peer floor** before any normaliser call: one declared floor per factor = `max(spec.min_peer_count, MIN_RANK_PEERS | MIN_DISPERSION_COHORT by transform)` evaluated in `CohortCache.get`, so a refusal carries one reason.
4. **GAP 7 translation** (prerequisite for 5): a single declared mapping from spec primitive vocabulary (`price_adjusted`, `pit_factor_spec.py:502`) to `pit_price_basis` basis (`PRICE_ROLE_REQUIRED_BASIS`, `pit_price_basis.py:171-174`), applied by the engine when resolving a valuation-role price; if it lives in a module, add it to `COMPONENTS` (v5).
5. **pe_absolute / pe_relative.** Sources: `SPECS_V3["pe_ratio"]` with `PRICED_EPS_V2` (`pit_factor_spec.py:534-546, 1204-1208`), `pit_valuation_spec.SUBFACTORS` (`:403+`), anchor `PE_ABSOLUTE_ANCHOR_V1` (`:711`), transform `TRANSFORM_*`/`solve_b` (`:878-915`), EPS pairing via `pit_price_basis.PAIR_PATHS` (`:266-268`) and `pit_eps` TTM rule; listing resolution (today `listing_id` NULL, `pit_replay.py:490-492`).
6. **ev_ebitda_supplement.** Sources: `SUBFACTORS[SUBFACTOR_EV_EBITDA]` primitives and `ANCHOR_COHORT_MEDIAN` (`pit_valuation_spec.py:454-470`), `DEBT_RUNG_EVEBITDA_V1` for debt-rung eligibility (`:2032-2040`), `SPECS_V3["ev_ebitda"]` BENCHMARK_ANCHORED / min_peer 12 (`pit_factor_spec.py:916-917`). Requires an owner decision on WHICH declaration governs normalisation (see 5.2-R2).
7. **fcf_conversion, net_debt_ebitda, debt_market_cap** (interest_coverage blocked on D3): primitives from `SPECS_V3` (`pit_factor_spec.py:1071, 986, 1007`), transform PERCENTILE_RANK as declared, direction from `FACTOR_DIRECTION_V1` (`pit_factor_blocks.py:649-665`).
8. **Direction wiring.** Consume `direction_of()` (`pit_factor_blocks.py:695`) after domain/refusal checks (`:619-624`); for E/G factors assert `direction_of(k)` agrees with the registry's `higher_is_better` (both are digested bodies).
9. **Labelling.** Any diagnostic-only eligibility bypass carries a row-level label (`feature_role` or `resolved_inputs_json`) per `pit_factor_spec.py:529-533`; none exists today.
10. **Stale text and tests.** `REASON_WHY` `:266-281`, `KNOWN_LIMITATIONS` `:495-497`, `test_pit_replay.py:157`, and the D3 status must be revisited together with each verdict flip.
11. **D3 stays untouched** until an owner decision (`docs/POST-REBOOT-CHECKLIST.md:64-65`).

### 5.2 Governance — as `spec_freeze_v5`, never in place (`docs/POST-REBOOT-CHECKLIST.md:72`; `pit_frozen_spec.py:79`)

- R1. Lift the band and floor out of `benchmark_margin_50_75` into a digested constant (e.g. `BENCHMARK_BAND_V1 = (0.50, 0.75)`, `BENCHMARK_MIN_COHORT_V1 = 3`) and add to `COMPONENTS` (closes v4-05).
- R2. Decide, on the record, which declaration governs normalisation for V/Q — `pit_factor_spec.normalization_type` or a `candidate_v2_registry()` entry — since adding registry entries changes a digested body (v5) and `normalise()` today reads only the registry (`pit_replay.py:909`).
- R3. Digest a DECLARED executable definition rather than prose: a `FORMULAS_V1: dict[str,str]` per scored factor (reconciling `pit_derive.py:481` with the engine), plus a golden-value test that evaluates the engine's growth/assemble code on fixed inputs (e.g. ebitda=110, ebitda_lag1=100, assets_lag1=1000 → growth 0.01) so a change at `pit_replay.py:770-771` fails a test (closes v4-06 to the extent a digest can; hashing source is not recommended since docstrings are deliberately ignored).
- R4. Add `pit_score_signature.V2_BLOCKS`, the six `TRANSFORM_*` parameters (or a `TRANSFORM_PARAMS` dict incl. `TRANSFORM_B`), and `pit_price_basis.PAIR_PATHS` to `COMPONENTS`; relabel `PILLARS` as "v1 pillar set (legacy)".
- R5. Add a `callable:` scan beside the `unrenderable:` scan in `validate()` (`pit_frozen_spec.py:934-937`), or make `enc` refuse callables inside bodies (closes v4-08).
- R6. Fix the `COMPONENTS` comment (`:254-255`, "repr" → canonical JSON) and the docs' "nine BODIES" wording.
- R7. Record Finder 2's file-level mutation procedure (fresh interpreter, cache cleared, `-B`) in `docs/POST-REBOOT-CHECKLIST.md` as the freeze's negative test; the in-process test can only exercise components already in `COMPONENTS`.
- R8. Until items 5.1-1 through 5.1-8 land, either do not stamp `spec_freeze_v4` on engine rows, or have the engine refuse when its `FACTOR_SPEC_VERSION` differs from the one the live freeze digests (v4-12).

---

## Could not verify

- The handoff document itself (absent from the repository); its claims are taken from the task text.
- FREEZE_V3 "unchanged since pre-v4": only one commit (`2e9db18`) touches `pit_frozen_spec.py`, so no earlier revision exists to diff; verified only against seals, tests and docs.
- Mutation digests: Finder 2's scratch-copy results, not re-run here; sites re-verified in current files.
- Test pass/fail state: no pytest was run. `test_pit_frozen_spec.py`, `test_pit_replay.py` were read.
- `pit_replay.validate()` beyond `:1710` (whether per-factor block equality vs `FACTOR_BLOCK` is checked).
- Pilot DB `cohort_too_small_to_rank` counts 93/62: Finder 3's numbers (outside my LIMIT 20 window); 483/424 dispersion refusals verified.
- `test_pit_factor_spec.section_1b_mutations` and `test_pit_guard_acceptance.py:262`: Finder 3's reading, not re-opened.
- Whether the streaming edit in `pit_replay.py` is complete or mid-write; it was uncommitted at audit time.

---

## Verification notes: citations

## Citation check — spec_freeze_v4 governance / engine-gap audit

**Result: 22 / 22 key claims CONFIRMED.** Every cited file:line was opened with `sed -n`; every snippet was found at the cited line (or within a line or two) and supports the claim as worded. No REFUTED, no UNVERIFIABLE.

Independent re-verification performed in this check (read-only, no repo writes):
- `git status --porcelain` / `git diff --numstat` / `stat`: four working-tree entries, `210 55 shafferfineval/pit_replay.py`, `101 0 shafferfineval/test_pit_replay.py`, HEAD `2e9db18`, mtime `2026-09-22 19:33:50`.
- `git diff -U0 pit_replay.py` hunk headers: 137, 174, 176, then only inside `run_date` (≥1001), `run_pilot`, `main`. The diff contains no line touching `FACTOR_SPEC_VERSION`, `FACTOR_PLAN`, `REASON_WHY`, `RAW_FIELD`, `def normalise`, or `eligible_peers`.
- Live freeze recompute (`python -B`, after confirming no component module opens a DB at import): digest `912268b3ec11bfc641fcac78078a0fb288cc51ce5432f8c27f931a532d1faae9` == `FROZEN_DIGEST`, intact True, 47 components, missing [], drifted [], kinds `{'body': 16, 'num': 5, 'str': 26}`, no `callable:`/`unrenderable:`/`MISSING(` renders; `V2_BLOCKS`, `PAIR_PATHS`, `TRANSFORM_A` not in `COMPONENTS`; modules = the 12 listed in the report (no pit_replay/pit_derive/pit_coverage/pit_shares/pit_rawprice).
- Pilot DB (`file:...?mode=ro&immutable=1`, LIMITed): run row 1 `status='running'`, `spec_freeze_v3` / `2f9bba31…`, `reachable_block_weight` 0.5, `verdict_counts` {1,5,7}; all eight unavailable keys at exactly 15,234 rows with the declared reasons; dispersion refusals 483/424 and rank refusals 93/62.
- `pilot_sizing_report.json`: `replay_report: None`, `error.type = MidDateFreeSpaceAbort`, message as quoted.

### Corrections / precision notes (no claim overturned)

1. **v4-00 line-shift wording.** The +12 shift applies to lines from ~189 up to ~1000 (hunks at 174 and 176 insert 12 lines); lines below 174 are unshifted. Replace "stale by +12 before line ~1000" with "stale by +12 between ~189 and ~1000".
2. **"Could not verify" list.** `cohort_too_small_to_rank` 93 (ebitda_efficiency) / 62 (ebitda_scale) are now verified by query; move out of the unverified list.
3. **v4-04 citation gap.** Add `pit_normalization.py:1497` (`"scale": self.scale.as_dict() if self.scale is not None else None,` in `FactorSpec.as_dict`, def at :1489) as the line that nests `ScaleSpec.as_dict` (:360-368) into the digested registry body.
4. **Mutation table (§1.3).** Still Finder 2's results; not re-run by the report author or by this check. The structural basis for 6b/6c/9c is confirmed, the specific digests are not.
5. **Minor:** the `run_date(..., batch_size=...)` reference at `:1006` was not confirmed at that exact location (def line confirmed, kwarg not read); `run_pilot`'s `batch_size: Optional[int] = DEFAULT_BATCH_SIZE` at `:1439` is confirmed.

### Verbatim confirmations of the load-bearing snippets

| id | file:line | found |
|---|---|---|
| v4-00 | pit_replay.py:187 / :202 / :169 | `DEFAULT_BATCH_SIZE = 500` / `class FreeSpaceAbort(RuntimeError):` / `FACTOR_SPEC_VERSION = "factor_spec_v2"` |
| v4-01 | pit_frozen_spec.py:325-326, 349-350, 363-364, 366-369 | all four snippets verbatim |
| v4-02 | pit_frozen_spec.py:383; POST-REBOOT-CHECKLIST.md:9; test_pit_frozen_spec.py:70 | verbatim; live recompute agrees |
| v4-03 | pit_frozen_spec.py:275, :280; MODEL-LINEAGE.md:1117 | two strings among the nine v4 entries; "nine BODIES" verbatim |
| v4-04 | pit_factor_spec.py:650-666, :739-742; pit_normalization.py:1835-1836 | no formula field; verbatim |
| v4-05 | pit_normalization.py:903, :923-924, :1834; pit_replay.py:378 | verbatim |
| v4-06 | pit_replay.py:770-771, :1663, :1666; pit_derive.py:481; pit_replay_manifest.py:915-916 | verbatim |
| v4-07 | pit_frozen_spec.py:262; pit_score_signature.py:152-153, :319-323 | verbatim; probe False |
| v4-08 | pit_frozen_spec.py:934-935, :254-255 | verbatim; `callable:` only at :350 |
| v4-09 | pit_valuation_spec.py:878, :915; pit_frozen_spec.py:295, :301; pit_price_basis.py:266-268 | verbatim |
| v4-10 | pit_replay.py:169, :860-862, :8; pit_frozen_spec.py:212, :599-600; pit_factor_spec.py:2143, :183, :202 | verbatim; token grep empty |
| v4-11 | pit_replay.py:371, :505, :1192-1194 | verbatim |
| v4-12 | pit_replay.py:1330-1331, :1337, :1460-1461 | verbatim |
| v4-13 | pilot DB; pilot_sizing_report.json; pit_replay.py:495-497 | as stated |
| v4-14 | pit_replay.py:270, :909; pit_factor_spec.py:654, :1071; pit_valuation_spec.py:464-465 | verbatim |
| v4-15 | pit_factor_blocks.py:649-651, :619-620; pit_normalization.py:872 | verbatim; `direction` absent from pit_replay.py |
| v4-16 | pit_normalization.py:310, :318; pit_factor_spec.py:2244; pit_replay.py:1211-1212 | verbatim; DB 483/424 |
| v4-17 | pit_factor_spec.py:502; pit_price_basis.py:171-174; pit_valuation_spec.py:2258-2259; pit_replay.py:490 | verbatim |
| v4-18 | pit_replay.py:373-375, :810, :918; pit_normalization.py:879-880; pit_factor_spec.py:1190-1195 | verbatim; no call to benchmark_margin_50_75 |
| v4-19 | pit_factor_spec.py:529-533; pit_replay.py:1429; test_pit_replay.py:157 | verbatim; no bypass tokens |
| v4-20 | pit_replay.py:762; POST-REBOOT-CHECKLIST.md:64-65; MODEL-LINEAGE.md:1178; SHAFFER-EQUITY-V2-CANDIDATE.md:288 | verbatim |
| v4-21 | pit_frozen_spec.py:181-187, :204; MODEL-LINEAGE.md:1113, :1157-1158 | verbatim |

---

## Verification notes: refute

# Refuter verdict on the spec_freeze_v4 governance / engine-gap audit

**Result: 22 of 22 key claims CONFIRMED; 0 REFUTED; 0 UNVERIFIABLE.** Several citations need line or attribution corrections, and the adversarial sweep found four items the finders missed. Everything below was verified read-only against the working tree as of 2026-09-22 (HEAD `2e9db18`, dirty: ` M pit_replay.py`, ` M test_pit_replay.py`, `?? pit_replay_rss.py`, `?? pit_archive/options/`). I recomputed the live freeze myself with `python -B` (no bytecode written): digest `912268b3ec11bfc641fcac78078a0fb288cc51ce5432f8c27f931a532d1faae9`, intact True, 47 components = 16 bodies / 5 numerics / 26 strings, no `callable:`/`unrenderable:`/`MISSING(` renders, `can_execute_replay() == (True, [])`. One immutable read-only query set was run on the 171 MB pilot DB; the main store was never opened; no pytest, no replay, no mutation re-runs.

## FACT — corrections to the claim set

| claim | correction | evidence |
|---|---|---|
| v4-00 | `class FreeSpaceAbort(RuntimeError)` is **not** part of the streaming edit; it exists at HEAD line 190 and was in `__all__` at HEAD:137. What is new: `DEFAULT_BATCH_SIZE` (:187), the `_flush` closure (+60 lines at :1100), raising `FreeSpaceAbort` mid-date with `.detail`, and an `except BaseException` path that finalises the run row to `aborted_exception` with `invalidated_reason` — a run-row behaviour change, not only batching/free-space. Eligibility, `FACTOR_PLAN`, growth arithmetic and the verdict short-circuit have no diff hunks (HEAD 169/359/758/850/1106 → 169/371/770/862/1192). | `git show HEAD:shafferfineval/pit_replay.py`; `git diff -U0` hunk headers |
| v4-03 | The COMPONENTS comment at `pit_frozen_spec.py:273-274` reads `# ---- v4: THE BODIES.` — it does **not** say "nine"; only `docs/MODEL-LINEAGE.md:1117` does. The 7-bodies-plus-2-strings count is correct (probe over `COMPONENTS[14:23]`). | pit_frozen_spec.py:273-283 |
| v4-07 | `V2_BLOCKS` tuple is at `pit_score_signature.py:322-323`; :319-321 is its comment. Order is also not captured indirectly: `V2_MAJOR_WEIGHTS` is a dict serialised with `sort_keys=True` (`pit_frozen_spec.py:354`). The engine iterates `pit_factor_blocks.BLOCKS = pit_score_signature.V2_BLOCKS` (`pit_factor_blocks.py:113`). | probe: `V2_BLOCKS in COMPONENTS` → False |
| v4-08 | The `unrenderable:` scan is at `pit_frozen_spec.py:941-942`, **not** 934 (934-939 is the `for need in (...)` loop). Substance unchanged: no `callable:` scan exists in `validate()` (:850-950). | grep -n |
| v4-10 | STILL_BLOCKED spans `pit_frozen_spec.py:594-603`; the quoted fragment is :599-600. | sed |
| v4-17 | `KNOWN_LIMITATIONS` opens at `pit_valuation_spec.py:2253`; the quoted sentence is :2258-2259. No translation constant: every non-test `price_adjusted` hit outside the two spec modules is a `pit_derive` graph/coverage entry (:391, :518-520, :659, :737-739, :1189, :2160). | grep |
| v4-12 | Nuance: a rerun today would **not** be stopped by the gate — `can_execute_replay() == (True, [])` and `df -h /c` shows 14G free vs `FREE_SPACE_GATE_FLOOR_BYTES = int(7.5 * GIB)` (`pit_replay_manifest.py:146`). The checklist's "4.3 GiB free" (:15) is stale. The mislabelling hazard therefore is live, not hypothetical. | df; recompute |
| v4-21 | `docs/MODEL-LINEAGE.md:1157-1158` is written in the present tense ("Production replay derives eligibility from the corrected spec"); calling it "a specification statement" is an interpretation. As a description of `pit_replay.py:169/860-862` it is false; `pit_frozen_spec.py:594-603` admits the gap. | sed |

## FACT — what the finders missed

1. **A fourth definition of `ebitda_growth`, rate-consistent, outside the digest.** `pit_factor_contract.py:331-332`: `"ebitda_growth": {"observations": 4, "why": "E_t and E_t-1, each two primitives"}` — four primitive observations and no total-assets observation, which matches alternative A (a rate) and not the engine's `Assets_{t-4q}` (which needs a fifth observation). The D3 record (`pit_frozen_spec.py:698-699`) lists only `pit_derive` as A's origin. `pit_factor_contract` is not in `COMPONENTS` (verified module set), so this contract is governed by nothing.
2. **The historical origin of alternative A** is the frozen v1 production code: `company_scoring.py:628-631` `calculate_ebitda_growth` → `_growth(ebitda_annual[-1], ebitda_annual[-2])`, a rate. Not cited in D3.
3. **Provenance drops the dirty flag.** `pit_replay.py:1544` stores only `git_revision().get("revision")` in `pit_replay_run.code_revision`; `pit_replay_manifest.git_revision()` (:399-430) computes `dirty`/`n_dirty_paths` ("The dirty flag is not decoration") and the engine discards them. A run from today's dirty tree would record `2e9db18`, which does not describe the streamed engine — the failure mode `docs/POST-REBOOT-CHECKLIST.md:18` already records for `c9b4050`. No engine-source hash exists anywhere; the gate's only hash is `schema_sha256` over schema SQL (`pit_replay_manifest.py:495`).
4. **`benchmark_margin_50_75` has no production caller at all** — only `test_pit_guard_acceptance.py:88-89` and `test_pit_normalization.py:159,185,386-387`. Stronger than "the engine never calls it": the MARGIN reading decided in factor_spec_v3 is executed nowhere in the repository.

## FACT — additional verifications performed

- Pilot DB (immutable, LIMIT): run 1 `status='running'`, `source_versions_json` = spec_freeze_v3 / 2f9bba31… / pit_replay/1.0; `verdict_counts {REFUSED_AMBIGUOUS:1, COMPUTABLE:5, NOT_COMPUTABLE:7}`; `reachable_block_weight 0.5`; all eight unavailable keys at exactly 15,234 rows with the declared reason; dispersion refusals 483/424; **rank refusals 93 (ebitda_efficiency) / 62 (ebitda_scale)** — Finder 3's numbers now verified. `pilot_sizing_report.json`: `replay_report: None`, `error.type MidDateFreeSpaceAbort`.
- `FactorSpec` has 17 dataclass fields, none a formula; rendered `SPECS_V3` stamps `spec_version` only as v1/v2 (probe).
- `direction_of(` is called only inside `pit_factor_blocks.py` (:695 def, :967 self-check); `direction` does not occur in `pit_replay.py`.
- `run_pilot` parameters (`pit_replay.py:1429-1439`): store_path, entity_limit, entity_ids, overwrite, free_floor_bytes, measure, per_table_scopes, sample_hz, progress, batch_size — no eligibility/spec-version switch.
- `git log -- pit_frozen_spec.py` = one commit (`2e9db18`); FREEZE_V3 can be checked only against its seals.
- The in-process drift test (`test_pit_frozen_spec.py:150-234`) mutates PE anchor, debt rung, SPECS_V3 body, direction table and the registry — all already in `COMPONENTS`; `section_1b_mutations` (`test_pit_factor_spec.py:101-140`) swaps `FS.SPECS`/`BY_KEY` in try/finally and calls only `FS.validate()`.

## Not verified by me

- Mutation digests 1a-10b are Finder 2's scratch-copy results; I verified each mutation site and the structural reason for each hole (a literal inside a non-digested function; a module absent from `COMPONENTS`), not the hashes.
- No pytest run; no replay; the 9.6 GB main store was not opened.

## RECOMMENDATION (separate from the facts; nothing implemented)

- Add to the D3 record the two omitted A-side declarations (`pit_factor_contract.LEAF_ARITHMETIC["ebitda_growth"]`, `company_scoring.calculate_ebitda_growth`) so the owner decides with the full inventory; reconcile `LEAF_ARITHMETIC` with whichever denominator is chosen.
- Store `git_revision()`'s `dirty`/`n_dirty_paths` on the run row (or refuse a pilot on a dirty tree) so `code_revision` cannot present a clean hash for uncommitted engine code.
- Apply the line corrections above before the audit is filed; fix the report's "COMPONENTS comment says nine BODIES" sentence.
- Keep the report's R1-R8 as written; the refutation pass found no reason to withdraw any.

---

## Verification notes: complete

## Corrections to the report (FACT)

1. **v4-00 — working tree.** `git status --porcelain` at 19:54 EDT shows FIVE entries, not four: ` M pit_replay.py`, ` M test_pit_replay.py`, `?? pit_archive/options/`, `?? pit_replay_rss.py`, **`?? rss_report_1k.json`** (generated_at `2026-09-22T23:40:22+00:00`, mtime 19:50). The RSS harness ran two `run_pilot` children (~280 s each, 1000 entities, 2019-06-28) during the audit window; its `freeze` block reads `{"version": "spec_freeze_v4", "digest": "912268b3…", "intact": true, "can_execute": true}`.
2. **v4-00 — scope of the uncommitted edit.** Not only batching/free-space: the old-1456 hunk wraps the date loop in `try/except FreeSpaceAbort / except BaseException` and finalises the run row on any exit (pit_replay.py:1446-1452 docstring "THE RUN ROW TELLS THE TRUTH"). This answers `pilot_sizing_derived.json` `known_defects_found_by_this_run[0..1]`; defect [2] (meter double-count) is untouched — `pit_replay_meter.py` is unmodified.
3. **v4-03 — attribution.** `pit_frozen_spec.py:273-274` reads `# ---- v4: THE BODIES. A version string cannot detect a change to the thing it versions; these are the things.` — no count. "nine BODIES" appears only in `docs/MODEL-LINEAGE.md:1117`; the likely source is the module docstring's nine-category list at `pit_frozen_spec.py:28-31`, five of which were already digested in v3.
4. **v4-08 — line number.** The `unrenderable:` scan is at `pit_frozen_spec.py:941-942`, not `:934-937`.
5. **v4-14 — line number.** The snippet `"its PEER ELIGIBILITY but sets `normalization` to None on every spec, "` is on `pit_replay.py:271` (report says :270).
6. **v4-16 — GAP 5 is three-stage, not two.** Upstream of the spec floor, `pit_coverage.MIN_COHORT_N = 12` (`pit_coverage.py:88`, enforced in `stored_cohorts` :655-657) decides which stored sets exist at all; the engine's own `R_NO_PEER_SET` text cites it (`pit_replay.py:284`). Pilot DB also carries first-stage `insufficient_peers` refusals (32/24/28/28) the report does not list. The 93/62 `cohort_too_small_to_rank` counts are now verified by my query.
7. **§1.4 "What is proven sound" omits a contradiction inside the freeze module.** `STILL_BLOCKED["the_diagnostic_replay_itself"]` (`pit_frozen_spec.py:594-603`) says "ENGINE not … No replay authorisation exists", but `SCORE_ASSEMBLY_GAP["what_still_blocks"] = ()` (`:684`) makes `can_execute_replay()` return `(True, [])` (verified live), and `pit_replay_manifest.gate()` never reads `STILL_BLOCKED`. The recorded engine gap does not gate anything — call it **Hole C**; `rss_report_1k.json` is the artefact showing the gate passed today.
8. **§1.4 omits `_FROZEN_MANIFEST`** (`pit_frozen_spec.py:429-524`), the recorded 47-entry manifest that verify() uses to locate drift. Only its non-emptiness (`:875`) and length (`test_pit_frozen_spec.py:145-147`) are checked; nothing asserts `digest(_FROZEN_MANIFEST) == FROZEN_DIGEST`. I verified it holds today (True; kinds 16/5/26; no `callable:`/`unrenderable:`/`MISSING(`).
9. **v4-09 / v4-07 read as "unprotected"; the repo shows test-level pins.** `TRANSFORM_B` is pinned to `PINNED_CURVE["b_published"] = 16.3825` (`pit_valuation_spec.py:977-995`, `test_pit_valuation_spec.py:252-254`); `PAIR_PATHS` ends (`test_pit_price_basis.py:208-210`); `V2_BLOCKS`/`"EVGQ"` (`pit_score_signature.py:342`, `test_pit_score_signature.py:266-282`); the 50-75 band behaviour (`test_pit_normalization.py:159-186`). These are not digests, but the owner needs them to size R1/R4.
10. **v4-13 provenance.** The run row's `code_revision` is `c9b405049c27…` (HEAD at pilot time, dirty tree per `pilot_pristine_manifest.json`, which is itself not parseable JSON — "Extra data: line 494").
11. **"Could not verify `pit_replay.validate()` beyond :1710"** — closed: `:1720-1753` asserts `MODEL_VERSION == FROZEN_MODEL_VERSION` (:1730-1733) and never touches `FACTOR_SPEC_VERSION`; block membership is checked against `within_block_weights` (:1697-1705), not `FACTOR_BLOCK` directly.
12. **Line-offset statement** ("+12 before ~1000, up to +86 after") understates the tail: hunks continue to old 1648-1669 → new 1799-1823 (+154 by end of file).

## What the owner still cannot decide from this report (FACT → RECOMMENDATION)

- FACT: Phase 2's RSS invariant has one rung (1k) measured, not the 1k→4k→8k the checklist requires (`docs/POST-REBOOT-CHECKLIST.md:56-57`). RECOMMENDATION: treat Phase 2 as "written and oracle-tested, not proven"; run 4k/8k before any pilot rerun.
- FACT: Every artefact in the repo root that carries a freeze stamp (`pilot_sizing_report.json` via the run row, `pilot_sizing_derived.json`, `full_run_capacity_budget.json`) is stamped `spec_freeze_v3 / 2f9bba31… / 38`. RECOMMENDATION: list them as superseded-freeze artefacts in the lineage doc and recompute the budget after Phase 3 (checklist :67).
- FACT: the gate is satisfied by `can_execute_replay()`, which is satisfied by an empty tuple at `pit_frozen_spec.py:684`. RECOMMENDATION (v5, not in place): either populate `what_still_blocks` from `STILL_BLOCKED` while the engine derives from v2, or make `pit_replay_manifest.gate()` refuse when `pit_replay.FACTOR_SPEC_VERSION != pit_factor_spec.FACTOR_SPEC_VERSION_V3`; add `digest(_FROZEN_MANIFEST) == FROZEN_DIGEST` and a `callable:` scan to `validate()`.
- FACT: mutation digests 1a-9d are Finder 2's and were not replicated; Holes A and B need no replication (provable by construction; A shown in-process with a raising stub). RECOMMENDATION: mark each mutation row "replicated / not replicated / by construction" rather than a single footnote.