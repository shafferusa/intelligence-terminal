# V/Q engine wiring plan and owner-decision packet (2026-09-22)
**Provenance.** Produced 2026-09-22 (after the spec_freeze_v5 cut, HEAD 609cefe) by the session's automated investigation: four independent finders (benchmark margin path, Q factors, V chain, registry/freeze/test mechanics), one synthesizer, then three adversarial verifiers (citation checker, refuter, completeness critic). Verifier verdicts and corrections are appended verbatim; where a verifier REFUTED a particular, the refutation stands over the plan text. Facts cite file:line; proposals are labelled; every economics choice is an OWNER DECISION and none was made here. Nothing in this document is a decision.
**Headline.** No remaining factor is decision-free. Even the margin benchmark path (Stage A) needs OD-01 to OD-03. The refuter found OD-08, OD-10, OD-12 already decided in substance by digested bodies or declared choices, and OD-19's premise false; those are listed as 'confirm the repository's answer'.

---

# ShafferFinEval PIT engine -- wiring the seven remaining factors (plan, HEAD 609cefe = spec_freeze_v5, digest 58722f4c, 61 components)

Labels: **FACT** = read from the repository at the cited line; **PROPOSAL** = this plan; **OWNER DECISION** = economics, never chosen here. Repository root R = `C:\Users\logan\shafferfineval-app\shafferfineval`. Nothing was edited; no test suite, replay or pilot ran; the store was not opened. One pure-python probe (`.venv\Scripts\python.exe -B`, module imports only) produced the fixture numbers quoted below.

## 0. Governing facts that shape the order

- FACT `R\pit_replay.py:1043` `spec = _REGISTRY[plan.norm_key]` and `:1959` `if plan.norm_key not in _REGISTRY:` -- the engine normalises only through `candidate_v2_registry()`, whose BODY is digested (`R\pit_frozen_spec.py:388`). Any new registry entry moves FROZEN_DIGEST.
- FACT `R\pit_replay_manifest.py:916` `if not freeze.get("intact"):` and `R\pit_replay.py:1669` `require_gate()` -- a drifted freeze refuses the pilot before a writer exists, and `R\test_pit_frozen_spec.py:66-67` asserts `v["intact"]`. So a commit that moves a digested body is red until the cut (Stage E) lands in the same commit.
- FACT `R\pit_replay.py:1052` `return pit_normalization.normalize(spec, value, as_of_date=as_of)` -- the benchmark plan has `cohort_use=""` (`:444-451`) and never passes `benchmark=`; `R\pit_normalization.py:742` `if not is_finite(benchmark):` returns `benchmark_unavailable`. The MARGIN path is engine glue; the registry entry (`:1849-1862`), band and floor (`:908-909`) and FORMULAS_V1 text (`R\pit_factor_spec.py:1292-1294`) are already digested. Stage A therefore moves NO digested body.
- FACT `R\pit_frozen_spec.py:478-491` `manifest()` keys are `"%s:%s" % (module_name, attr)`; the third COMPONENTS element (the description) is not in the manifest and not digested.
- Consequence (PROPOSAL): Stage A ships under v5. Stages B, C, D each need at least one digested body (registry entries, domain dicts, floor/translation bodies, golden witnesses). Under OD-25 option A, ONE v6 cut (Stage E mechanics) is committed together with the first body change, before B's plan flips; B, C, D are then glue under v6. Under option B the Stage E mechanics repeat per stage (v6, v7, v8). Stage E is listed last because its content is the union of the decided bodies, not because it executes last.

## 1. OWNER DECISIONS (all listed in `owner_decisions_needed`; summary)

| id | question (short) | blocks |
|---|---|---|
| OD-01 | target inside its own 50-75 band / EBITDA vector? | ebitda_benchmark |
| OD-02 | which margins feed the registry's cohort-IQR k (whole eligible vector vs band vs registry change)? | ebitda_benchmark |
| OD-03 | band floor: engine enforces spec `min_band_members=3` (`band_too_thin`) vs amend `benchmark_margin_50_75` (v6) vs accept band of 1 | ebitda_benchmark |
| OD-04 | GAP 5: one declared floor per factor (max(spec, normaliser)) in `CohortCache.get`, or keep two-stage refusals | benchmark, fcf, nde, dmc, pe_relative, ev |
| OD-05 | which declaration governs normalisation for Q/V: registry entries (v6) / engine reads spec type / V-specific scorer | fcf, nde, dmc, ev, pe_abs, pe_rel |
| OD-06 | does DEBT_RUNG_EVEBITDA_V1 gate Q's total_debt, or score every v2 rung with the bound reported, or a new leverage policy | nde, dmc |
| OD-07 | EBITDA <= 0 denominator for net_debt_ebitda and fcf_conversion | nde, fcf |
| OD-08 | missing cash: refuse (absence_limits) or 0.0 (v1) | nde, ev |
| OD-09 | period rule for total_debt/cash (instants) and OCF/capex vs the EBITDA period | nde, fcf, dmc, ev |
| OD-10 | negative net debt legitimate and scored? | nde |
| OD-11 | GAP 7: translation constant vs SPECS_V5 rename; `require_traded` / `require_split_check` for market cap | dmc, ev, pe_abs, pe_rel |
| OD-12 | debt_market_cap stays uncapped on the office rung? | dmc |
| OD-13 | EV transform, anchor statistic, scale, floor, direction application, target-side screens | ev |
| OD-14 | EV debt-rung walk (first resolving vs first eligible) and gating of cohort members | ev |
| OD-15 | pe_relative transform and median population | pe_rel |
| OD-16 | near-zero-positive EPS in pe_absolute / the pe_relative median | pe_abs, pe_rel |
| OD-17 | action-gate window start and empty-window semantics (v6 if changed) | pe_abs, pe_rel |
| OD-18 | valuation price selection (roll-back, traded) and multi-listing rule | pe_abs, pe_rel, ev, dmc |
| OD-19 | may V draw on stored sets below MIN_COHORT_N=12? | pe_rel, ev |
| OD-20 | V assembly path (block_score + depends_on guard vs valuation_pillar), the five NULL pit_score columns, discontinuity source | V block |
| OD-21 | cohort mixing of TTM methods / EPS rungs / cash rungs | pe_rel, ev |
| OD-22 | governance: benchmark + Q golden witnesses cut before Stage A or at the v6 cut | ordering |
| OD-23 | governance: keep R_BENCHMARK_AMBIGUOUS / R_PE_CHAIN_NOT_BUILT / R_NO_V2_NORMALIZATION as history in ENGINE_REASONS | vocabulary |
| OD-24 | the six pending-v5 items at the v6 cut | v6 content |
| OD-25 | cut cadence: one v6 cut vs one cut per stage | B, C, D |

## 2. Stage A -- ebitda_benchmark MARGIN path (under v5; NOT decision-free: needs OD-01, OD-02, OD-03; OD-04/OD-22/OD-23 shape the plumbing)

What the repo already decides (FACT): scored quantity margin excess = ebitda_margin - cohort_mean_margin (`R\pit_factor_spec.py:1292`), cohort selected by EBITDA dollars in the 50-75 band (`R\pit_normalization.py:908` `BENCHMARK_BAND_V1: tuple[float, float] = (0.50, 0.75)`), eligibility EBITDA_AND_REVENUE (`R\pit_factor_spec.py:1227`, rule `:445-453`), min_peer_count 12 / min_band_members 3 (`:793`, `:795`), BENCHMARK_ANCHORED with cohort IQR and fallback 0.06 (`R\pit_normalization.py:1858-1859`), direction HIGHER_IS_BETTER adopted from the registry (`R\pit_factor_blocks.py:660`; probe confirms). Target margin already exists: `R\pit_replay.py:858` `out.margin = float(out.ebitda) / float(out.revenue_at_ebitda_period)`.

What it does not decide (FACT): target inclusion (stored sets include the target, `R\pit_coverage.py:678`; frozen core excludes it, `R\company_scoring.py:374`); the population for `cohort_values` (`R\pit_normalization.py:536` `if n < spec.min_cohort:` refuses below 8 with NO fallback -- probe: band-only cohort_values of 3 -> `cohort_too_small_for_dispersion`; 12 margins -> k = IQR 0.055); the band floor (`:932` `if len(pairs) < floor:` floors PAIRS, `:940` `if not band:` accepts a band of 1; `R\test_pit_normalization.py:165` asserts `n_band == 2` passes).

Steps A1-A9 (see `steps`): third cohort mode `COHORT_BENCHMARK` (`R\pit_replay.py:437-438`); plan flip (`:444-451`); `RAW_FIELD["ebitda_benchmark"] -> ("margin","margin")`, `SOURCE_CONCEPTS -> ("oi","da","rev@P")` (`:942`, `:956`); `CohortCache.get` collects `(prim.ebitda, prim.margin)` pairs (`:997-1004`); a helper `benchmark_anchor()` computes M_s via `pit_normalization.benchmark_margin_50_75` and enforces `spec.min_band_members` (OD-03) -> `pit_factor_spec.REASON_NO_BAND`; `normalise()` gains `benchmark=` kwarg and a third branch passing `benchmark=` + `cohort_values=` (OD-02) (`:1027-1052`); row codes `bm`, `nb`, `np`, `as` beside `ne/nv/nc/k/ks` (`:1384-1402`); `benchmark_level` stays the rung (`:1497`, `:1528`; DDL comment `R\pit_intermediates.py:412` `benchmark_level TEXT, -- Industry | Sector Fallback`); text/tests re-pinned; `ENGINE_VERSION -> "pit_replay/1.2"` (`:171`).

Fixture for the new engine-local test (probe, independent of OD-01 because it hands 12 pairs to the function): `eb = [100*i for i in 1..12]`, `mg = [0.09+0.01*i]` -> `benchmark_margin_50_75` = (0.17, 3, note "... 50-75 percentile of 12 (650 to 925) = 0.1700"); `normalize(EBITDAExcessLevel, 0.30, benchmark=0.17, cohort_values=mg)` -> score 98.24541388014812, scale 0.055 (`cohort_iqr`), n_cohort 12, anchor 0.17, anchor_source `cohort_p50_p75_mean_margin`; band-only cohort_values (n=3) -> `cohort_too_small_for_dispersion`; twelve identical margins -> scale 0.06, scale_source `fixed`; no benchmark -> `benchmark_unavailable`; 2 pairs -> `(None, 0, ...)`. Existing fixture `R\test_pit_normalization.py:61-62` gives (0.31, 2).

Census after A: 7 COMPUTABLE / 0 REFUSED_AMBIGUOUS / 6 NOT_COMPUTABLE; refused rows in `R\test_pit_replay.py:1089` 7 -> 6; reachable stays 0.60 (`:222`, E already reachable); `validate()` stays [] (EBITDAExcessLevel is registered, RAW_FIELD entry exists; the direction guard `:2003-2012` covers only PERCENTILE_RANK).

## 3. Stage B -- fcf_conversion, net_debt_ebitda, debt_market_cap (PERCENTILE_RANK as declared; needs OD-05..OD-12, OD-04, OD-25)

FACT: the three specs are the v1 objects by reference (`R\pit_factor_spec.py:1450` `SPECS_V4 ... _to_v4`; `:1244-1251` REPLACED_BY_VERSION), declare PERCENTILE_RANK (`:1018`, `:1039`, `:1103`), min_peer_count 3, and FORMULAS_V1 states the arithmetic (`:1315`, `:1318`, `:1319-1320`). The registry has no entry (`R\pit_normalization.py:1951` seven keys; `R\test_pit_normalization.py:562` `len(...) == 7`). Direction: `R\pit_factor_blocks.py:653-658` fcf HIGHER, nde LOWER, dmc LOWER; `R\pit_normalization.py:1494` `higher_is_better: bool = True` default, so the two leverage entries must set False or `R\pit_replay.py:2011` reports "registry direction and FACTOR_DIRECTION_V1 disagree". `percentile_to_score` (`R\statlib.py:158-167`) already gives the lowest ratio +100 under lower-is-better (probe), so direction is inside the transform, applied after the domain refusals -- consistent with `R\pit_factor_blocks.py:622-627`.

FACT: every needed tag is already in the fact index (`R\pit_coverage.py:329-332` union of v1/v2 ladder tags; `:399` `AND qtrs IN (0, 4)`); instants resolve at qtrs=0 (`:479`); COMBINE_SUM rungs need all components at one period (`:483-495`). The engine's precedent for a second leg is the level-at-P loop (`R\pit_replay.py:919-926`). total_debt v2 rung keys (`R\pit_policy.py:780-826`) equal the keys of DEBT_RUNG_EVEBITDA_V1 (`R\pit_valuation_spec.py:2048-2055`); `quantity_for` (`R\pit_policy.py:925-940`) returns quantity/bound/rung_index. Consumer rule (2) (`:733-739`) names both Q debt factors and forbids a clean leverage percentile over a rung mixture. debt_market_cap needs a listing and a market cap: `R\pit_rawprice.py:1147-1157 market_cap_as_of_detail(... class_policy=CLASS_POLICY_STRICT, require_split_check=False, require_traded=False ...)`; eligibility `DEBT_AND_MARKET_CAP` (`R\pit_factor_spec.py:569-578`) is SURVIVOR_ONLY and its `positive_screen="market_cap > 0"` is NOT applied by `eligible_peers` (`:2466-2472` "AVAILABILITY only ... apply them in the cohort builder").

Steps B1-B11: Primitives fields + reason codes; level-at-P resolvers for instants and durations (period rule OD-09); derived ratios with domain refusals BEFORE direction (OD-07, OD-08, OD-10); registry entries `FCFConversionRank` / `NetDebtEBITDARank` / `DebtMarketCapRank` (OD-05 A; digested -> cut) or the engine branch (OD-05 B); domain bodies `LEVERAGE_DOMAIN_V1` / `FCF_DOMAIN_V1` / `MARKET_CAP_DOMAIN_V1` in the `INTEREST_COVERAGE_DOMAIN_V1` shape (`R\pit_factor_spec.py:1376-1395`) with `validate()` code cross-checks like `R\pit_replay.py:1982-1992`; golden witnesses + `_golden_eval` synthesising instants (`:1897-1918`); rung as DATA on the row (`source_tag` = rung key, `dr`/`db` codes, per-date tally by bound; `pit_valuation_spec.debt_gate` only under OD-06 B); market cap per target and per priced cohort member through `market_cap_as_of_detail` with the GAP 7 constant asserted (OD-11); `listing_id` written and `pit_listing` seeded (`R\pit_replay.py:686-689`, `:722-724`, `R\pit_store.py:469`); plan flips (`:515-534`); GAP 5 floor 4 for ranks (OD-04); text and test re-pins.

Golden expectations from FORMULAS_V1 arithmetic: total_debt 1000, cash 200, EBITDA 400 -> net_debt_ebitda 2.0; OCF 500, capex 200 (filed positive, `R\pit_policy.py:629`), EBITDA 400 -> fcf_conversion 0.75; total_debt 100, cash 300, EBITDA 400 -> -0.5 (if OD-10 A); EBITDA 0 -> the OD-07 refusal code; total_debt 1000 / market_cap 5000 -> 0.2. Census after B: 10/0/3; refused rows 3; `R\test_pit_normalization.py:518-522, 562` set and count 7 -> 10; reachable stays 0.60.

## 4. Stage C -- ev_ebitda_supplement (needs OD-13, OD-14, OD-11, OD-08, OD-09, OD-18, OD-19, OD-20, OD-21, OD-05)

FACT: no executable transform exists: SUBFACTORS says BENCHMARK_ANCHORED at `cohort_median_multiple` (`R\pit_valuation_spec.py:463-466`); FORMULAS_V1 says only "anchor = cohort median" (`R\pit_factor_spec.py:1309-1311`); SPECS_V4['ev_ebitda'] says min_peer 12 / min_band 3 with `graph_node="cohort_mean_ev_ebitda"` (`:949`, `:954`); `SUBFACTOR_DIRECTION_V1['ev_ebitda_supplement'] = LOWER_IS_BETTER` (`R\pit_factor_blocks.py:676`) while pe_* are EMBEDDED_IN_TRANSFORM (`:674-675`), so the convex transform would double-apply for EV. Screens `ebitda > 0 and enterprise_value > 0` and band `(0.1, 300)` (`R\pit_factor_spec.py:497-498`) are declared for peers and applied nowhere. `debt_gate` (`R\pit_valuation_spec.py:2223-2240`) "EV/EBITDA calls this before it forms a multiple"; `resolve_concept` returns the FIRST non-stale rung (`R\pit_coverage.py:465-471`), so an UNDETERMINED first rung refuses even where SUM2 also resolves (OD-14). `eligible_peers` RAISES for a ceilinged factor when `rung is None` (`R\pit_factor_spec.py:2400-2408`) -- the engine must refuse `R_NO_PEER_SET` before calling it.

Steps C1-C6: registry/scorer per OD-13 and OD-05 (digested); Primitives `enterprise_value`, `ev_ebitda` from B's total_debt/cash/market_cap with `debt_gate` and value screens before the multiple; cohort mode for a median anchor (target in/out per OD-13) with per-member multiples from Primitives; V assembly per OD-20 (probe: `block_score(V, {pe_relative: 10.0})` = 10.0 while `presence({'pe_relative': True}).state` = ABSENT; `assemble()` calls `score_v2` without `reasons`, `R\pit_factor_blocks.py:372`; five columns written `None, None, None, None, None` at `R\pit_replay.py:1546`); plan flip; census 11/0/2; reachable 0.60 -> 0.85 (`R\test_pit_replay.py:221-226`).

## 5. Stage D -- pe_absolute, pe_relative (needs OD-15..OD-21, OD-11, OD-05, OD-19)

FACT chain: TTM via `pit_eps.ttm_eps_as_of_detail` (`R\pit_eps.py:1240-1262`; negative TTM is available=True with `pe_leg` unavailable) or the bulk `pit_eps_cohort.load_ttm` + `EntityTtm.select` (`R\pit_eps_cohort.py:131-212`); raw price `pit_rawprice.raw_close_as_of(conn, listing_id, date, *, roll_back_days=0, require_traded=False)` (`R\pit_rawprice.py:610`); basis carried by `pit_price_basis.normalised_pe` (`:676-713`) which asserts the raw basis (`:686`) and refuses when the gate is unusable (`:693-700`) -- gate refuses an EMPTY WINDOW (`:448-449` `if not rows: return ActionGate(factor=None, status=GATE_NO_EVENTS ...`) whereas `R\pit_shares.py:1224-1227, 1240` uses a listing-level `COUNT(*)`; window starts at `eps_period_end` (`:692`). Transform `valuation_score_detail` (`R\pit_valuation_spec.py:934-985`, refuses m <= 0 at `:958-959`) with `PE_ABSOLUTE_ANCHOR_V1 = 20.0` (`:712`); probe: 100x -> -53.9905, 5x -> +31.256, 100x vs median 28 -> -37.33. Eligibility PRICED_EPS_V2 (`R\pit_factor_spec.py:542-556`, price required, screen `earnings_per_share_pit > 0` admits near-zero-positive); sic2 ceiling with `VALUATION_PEER_SET_INSUFFICIENT` (`:2222-2224`). Listing from `scored_universe_as_of` (`R\pit_identity.py:1243`, `GROUP BY e.entity_id, e.cik, l.listing_id` `:1290` -- may return several).

Steps D1-D8: per-date TTM/price/listing loaders; `pe` on the score-date basis through `normalised_pe` (gate semantics OD-17; a change moves `ACTION_GATE_VERSION`'s body -> v6 and `R\test_pit_price_basis.py:176-189`); pe_absolute scorer per OD-05/OD-15 (the convex transform is not a `normalize()` type: `R\pit_normalization.py:871-888`); pe_relative cohort + median + transform per OD-15/16/19/21 with the `depends_on` guard (`R\pit_valuation_spec.py:641`); `earnings_discontinuity_state` per OD-20; plan flips; `R_PE_CHAIN_NOT_BUILT` kept as history (OD-23; precedent `R\pit_replay.py:316-323`); KNOWN_LIMITATIONS listing sentence (`:567-569`) removed; census 13/0/0; refused-row test block (`R\test_pit_replay.py:1080-1113`) rewritten since no plan is refused.

## 6. Stage E -- the v6 seal-and-cut (mechanics, mirrors the v5 procedure; executes with the FIRST body-moving commit)

FACT: `R\pit_frozen_spec.py:516` `if FROZEN_DIGEST not in ("PENDING", now):` tolerates a pending state; `:1087-1098` proves record/digest identity and key-set equality; `:1148-1173` pin FREEZE_V4 and "five distinct digests"; `:1180-1193` the by-body needs list; `R\pit_replay_manifest.py:1278` chain tuple stops at FREEZE_V4 while `:305-306` already names FREEZE_V5; `R\test_pit_replay_manifest.py:572` `len(lineage) == 5`; `R\test_pit_frozen_spec.py:71` `== 61`, `:105`, `:141-144`.

Steps E1-E9: FREEZE_V5 record in FREEZE_V4's shape (`:232-300`; digest 58722f4c..., n_components 61, STATUS_SUPERSEDED, a new REASON_* naming why, rows 0, may_execute False, superseded_by "spec_freeze_v6"); `__all__` (`:60-75`); `SPEC_FREEZE_VERSION`/`FROZEN_AT` (`:303`, `:309`); `ENGINE_MUST_DERIVE_FROM` (`:317`) and `pit_replay.FACTOR_SPEC_VERSION` (`R\pit_replay.py:193`) move together ONLY if SPECS_V5 exists (OD-11 B); COMPONENTS appended, never removed (`:355-439`); `FROZEN_DIGEST = "PENDING"` (`:502`) and `_FROZEN_MANIFEST = {}` (`:548`), then ONE `manifest()` in a fresh `python -B` interpreter pasted back and `digest(_FROZEN_MANIFEST)` recorded; `validate()` extended (FREEZE_V5 pins, live-name over FREEZE_V5, six digests, needs list, new content pins, OD-24 statuses); manifest chain tuple; test re-pins; docs (MODEL-LINEAGE v6 section, STILL_BLOCKED `:741-754`, `owner_confirmation_pending_v5` `:862-880`); verification: `pit_frozen_spec.main()` 0 problems, `python -B pit_replay.py --validate`, the ten suites, `test_pit_replay --slow` (oracle `R\test_pit_replay.py:1265-1300` compares rows byte-for-byte across arms, so every new code must be deterministic), "freeze unchanged by the run" (`:1171`).

## 7. GAP 5 and GAP 7 closures (where they belong)

- GAP 5 (OD-04): PROPOSAL `FactorPlan.cohort_floor` computed at import as max(spec.min_peer_count, registry scale.min_cohort if cohort-sourced, MIN_RANK_PEERS+1 for COHORT_RANK, BENCHMARK_MIN_COHORT_V1 for COHORT_BENCHMARK) and enforced once in `CohortCache.get` on `n_values`, refusal `insufficient_peers` (`R\pit_store.py:111`); a `validate()` assertion that no computable plan's floor is below any downstream stage's floor. For the benchmark that is 12 on n_values plus the separate band test (3). Digested as a body only if the owner wants it in COMPONENTS. Lands in Stage A (A7) and is reused by B/C/D.
- GAP 7 (OD-11): PROPOSAL `SPEC_PRIMITIVE_PRICE_BASIS_V1 = {"price_adjusted": PRICE_ROLE_REQUIRED_BASIS[PRICE_ROLE_VALUATION]}` beside `R\pit_price_basis.py:171-174`, added to COMPONENTS at the cut, consumed by ONE engine price resolver that calls `assert_price_basis(PRICE_ROLE_VALUATION, basis)` (`:207-215`). First consumer is Stage B8 (debt_market_cap), then C and D. The alternative (SPECS_V5 rename) moves FACTOR_SPEC_VERSION_V5 / ENGINE_MUST_DERIVE_FROM / pit_replay.FACTOR_SPEC_VERSION together.

## 8. Test re-pin ledger (per flip)

- A: `R\test_pit_replay.py:158-161` (7/0/6), `:243-245` (replace the `indistinguishable from ebitda_scale` pin with verdict/cohort_use pins), `:1089` (6), `:1101-1103` (`"ebitda_benchmark" not in by_key`), `:829-835` add `BENCH=1` case, new benchmark test with the fixture above; `:246-248` still >= 6 (8 entries -> 7 after rewrites).
- B: `:158-161` (10/0/3), `:1089` (3), `:1108-1111` (only ev_ebitda_supplement), `:841` unchanged spelling plus present cases; `R\test_pit_normalization.py:518-522, 562` (10); `R\pit_replay.py:306-315` REASON_WHY text "seven specs" rewritten.
- C: `:158-161` (11/0/2), `:221-226` (0.85), `:1089` (2), `:838-839` MASK_NONE still valid for an empty V.
- D: `:158-161` (13/0/0), `:1080-1113` rewritten, `:1104-1107` retired, seed_parents `pit_listing_rows > 0`.
- E: `R\test_pit_frozen_spec.py:66-71, 101-106, 141-144, 164-166, 585-601`; `R\test_pit_replay_manifest.py:570-592`; `R\test_pit_replay.py:171-172` and `R\test_pit_factor_spec.py:645-653` only if SPECS_V5 exists.

Stable pins (FACT): `R\test_pit_replay.py:511-530` block mask `E=1,V=0,G=1,Q=0` on fixed inputs; `:1165-1180` 13 rows per entity-date; `:197-206` golden perturbation; `R\test_pit_valuation_spec.py:99-133, 169-172` (SUBFACTORS unchanged; the `price_adjusted` pin reads the v1 spec via DEFAULT_SPEC_VERSION `R\pit_factor_spec.py:211`).

---

# Owner decisions, in full

## OD-01

**Question.** Is the TARGET company inside the EBITDA vector whose p50/p75 define its 50-75 band, and inside the band whose mean margin is M_s?

**Blocks:** ebitda_benchmark

**What the repository already says.** Stored peer sets include the target (R\pit_coverage.py:678 `record["members"] = members.get(record["peer_set_id"], [])`); the engine declares two conventions only (R\pit_replay.py:437 `COHORT_RANK = "rank_population_excludes_target"`, :438 `COHORT_DISPERSION = "dispersion_sample_includes_target"`); the frozen core excludes the target (R\company_scoring.py:374 `others = [c for c in universe if c.ticker != target.ticker]`); neither BY_KEY_V4['ebitda_benchmark'], FORMULAS_V1 (R\pit_factor_spec.py:1292-1294) nor the D1 record states it.

**Options:**
- EXCLUDE from both (frozen-core convention; M_s differs per target so it is computed per row, not cached per peer set)
- INCLUDE in both (a cohort property shared by all members, like COHORT_DISPERSION; one M_s per (peer_set_id, factor), cacheable)
- INCLUDE in the vector for p50/p75, EXCLUDE from the band mean (hybrid; not described anywhere in the repo)

## OD-02

**Question.** Which population supplies cohort_values for the registry's cohort-IQR scale k (min_cohort = 8, fallback 0.06 only on zero dispersion), and does it include the target?

**Blocks:** ebitda_benchmark

**What the repository already says.** R\pit_normalization.py:1858-1859 `scale=cohort_scale(multiplier=1.0, fallback_fixed=DEFAULT_EXCESS_MARGIN_SCALE_K)`, :392 default `min_cohort: int = MIN_DISPERSION_COHORT` (= 8, :311); :536 `if n < spec.min_cohort:` returns REASON_COHORT_TOO_SMALL_SCALE with no fallback; :556-561 the fallback fires only on zero dispersion; the audit R2 text names 'cohort_values for the registry scale' without a population (docs/V4-GOVERNANCE-AUDIT-2026-09-22.md:230).

**Options:**
- Margins of the WHOLE EBITDA_AND_REVENUE-eligible vector (n >= 12 by the spec floor, so the IQR resolves; probe: 12 margins -> k = IQR 0.055, source cohort_iqr)
- Margins of the BAND only (probe: a band of 3 -> 'cohort_too_small_for_dispersion' and the 0.06 fallback does NOT fire; the factor would refuse on nearly every row)
- Change the registry ScaleSpec (lower min_cohort or fallback on too-small cohorts) -- a digested-body change, v6

## OD-03

**Question.** Band floor: FORMULAS_V1 (digested) says '|band| >= BENCHMARK_MIN_COHORT_V1' and the spec says min_band_members = 3, but benchmark_margin_50_75 floors the PAIR vector at 3 and accepts a band of 1. Which governs and where is it enforced?

**Blocks:** ebitda_benchmark

**What the repository already says.** R\pit_factor_spec.py:1294 `"|band| >= BENCHMARK_MIN_COHORT_V1 (benchmark_margin_50_75); BENCHMARK_ANCHORED"`; :795 `min_band_members=3,`; :3153 `if item.min_band_members and item.min_peer_count < 4 * item.min_band_members:`; R\pit_normalization.py:932 `if len(pairs) < floor:` and :940 `if not band:`; R\test_pit_normalization.py:165 `n_band == 2 and n_band < len(A_EBITDA), n_band)`; pit_factor_spec.benchmark_band enforces both floors (:2544 `if n < min_vector:`, :2553 `if len(band) < min_band:`) but returns the DOLLAR mean.

**Options:**
- Engine enforces spec.min_band_members (3) on n_band after calling benchmark_margin_50_75, refusing pit_factor_spec.REASON_NO_BAND ('band_too_thin'); function and digested constants untouched
- Amend benchmark_margin_50_75 so BENCHMARK_MIN_COHORT_V1 floors the BAND (matches FORMULAS_V1 text; test_pit_normalization.py:165 'n_band == 2' must change; the constant's meaning changes -> treat as v6)
- Accept a band as small as 1 (contradicts the spec body's min_band_members=3 and pit_coverage.py:80-82)

## OD-04

**Question.** GAP 5: is ONE declared floor per factor (max of spec.min_peer_count, the registry scale's min_cohort, MIN_RANK_PEERS+1 or BENCHMARK_MIN_COHORT_V1 by transform) evaluated once in CohortCache.get on n_values with one refusal reason, or does the two-stage behaviour (eligibility floor, then the normaliser refuses with its own reason) stay? Is the floor table a digested body?

**Blocks:** ebitda_benchmark, fcf_conversion, net_debt_ebitda, debt_market_cap, pe_relative, ev_ebitda_supplement

**What the repository already says.** R\pit_normalization.py:319 `MIN_RANK_PEERS = 4`, :311 `MIN_DISPERSION_COHORT = 8`; R\pit_factor_spec.py:1019 `min_peer_count=3,` (Q ranks), :793 `min_peer_count=12,` (benchmark), :949 (ev_ebitda 12); R\pit_coverage.py:88 `MIN_COHORT_N = 12` upstream; R\pit_replay.py:1390 `if record["availability"] != pit_store.AVAIL_COMPLETE:` checks eligibility only; audit v4-16 NOT FIXED and recommendation 3 (docs/V4-GOVERNANCE-AUDIT-2026-09-22.md:187, :231).

**Options:**
- One declared floor per plan (FactorPlan.cohort_floor), refusal 'insufficient_peers', plus the separate band test for the benchmark; validate() asserts no floor is below a downstream stage's floor; optionally digested as PEER_FLOOR_V1
- Keep two stages and only add the validate() assertion / documentation (interest_coverage runs this way today: spec 3, normaliser refuses < 4 as cohort_too_small_to_rank)
- Raise the specs' min_peer_count to the normaliser floors via SPECS_V5 (eligibility change -> FACTOR_SPEC_VERSION_V5, ENGINE_MUST_DERIVE_FROM, pit_replay.FACTOR_SPEC_VERSION move together)

## OD-05

**Question.** Which declaration governs normalisation for the three Q ranks and the three V subfactors: candidate_v2_registry() entries (digested -> v6), an engine branch reading pit_factor_spec.normalization_type / pit_valuation_spec.SUBFACTORS for keys without a registry entry, or registry for Q and a V-specific scorer (pit_valuation_spec.valuation_score_detail) with validate()'s 'computable needs a norm_key' rule amended?

**Blocks:** fcf_conversion, net_debt_ebitda, debt_market_cap, ev_ebitda_supplement, pe_absolute, pe_relative

**What the repository already says.** R\pit_replay.py:1024 `_REGISTRY = pit_normalization.candidate_v2_registry()`, :1043 `spec = _REGISTRY[plan.norm_key]`, :1959 `if plan.norm_key not in _REGISTRY:`; :312 `"normalization_type, but a type is not a registered transform with its "`; R\pit_frozen_spec.py:388 registry body digested; R\pit_normalization.py:871-888 normalize() knows five types only; audit R2 (docs:244) left it open.

**Options:**
- A: registry entries FCFConversionRank (higher True), NetDebtEBITDARank and DebtMarketCapRank (higher False), plus V entries; normalise() unchanged; v6
- B: normalise() dispatches on the spec/subfactor declaration when no registry entry exists; registry untouched but R_NO_V2_NORMALIZATION's rationale is abandoned
- C: A for Q; V scored by pit_valuation_spec directly with norm_key None and validate() amended (the convex transform is not a pit_normalization type)

## OD-06

**Question.** Does DEBT_RUNG_EVEBITDA_V1 (the hard EV/EBITDA rung gate) govern total_debt for net_debt_ebitda and debt_market_cap? If not, does the Q build score every v2 rung with the rung distribution reported beside the rank (consumer rule 2a), rank within rung groups (2b), or need a new leverage policy measured on net_debt/EBITDA ordering?

**Blocks:** net_debt_ebitda, debt_market_cap

**What the repository already says.** R\pit_valuation_spec.py:2188 `"""May this rung's debt figure enter the SCORED EV/EBITDA factor?"""`; :2048-2055 the map; R\pit_policy.py:733-739 consumer rule (2) names net_debt_ebitda and debt_market_cap and says a mixed-rung percentile 'may not be presented as a clean leverage percentile'; R\pit_factor_spec.py:1028-1032 the spec note restates the flattery; the owner's rule: the debt-rung policy must not be weakened for coverage.

**Options:**
- A: EV/EBITDA only; Q scores every resolved rung, source_tag = rung key, rung/bound codes on the row, per-date tally by bound (coverage ~38% of base, ~94% on lower_bound rungs that flatter both lower-is-better factors)
- B: extend the gate: only the two DEBT_ELIGIBLE rungs score; REJECTED/UNDETERMINED refuse with debt_rung_rejected_measured / debt_rung_undetermined (coverage falls; the evidence was measured on EV/EBITDA ordering, not leverage)
- C: a Q-specific DEBT_RUNG_LEVERAGE_V1 measured the way gate 2 measured EV/EBITDA -- new measurement before any rung is admitted

## OD-07

**Question.** Domain rule when EBITDA <= 0 for net_debt_ebitda and for fcf_conversion: refuse by a named code (v1 precedent, INTEREST_COVERAGE_DOMAIN_V1 shape), score a signed ratio, or refuse zero only / add a magnitude band?

**Blocks:** net_debt_ebitda, fcf_conversion

**What the repository already says.** R\company_scoring.py:744-747 `"""NetDebt / EBITDA. Negative is possible and is financially strong. Requires positive EBITDA: dividing by a negative or zero EBITDA inverts the meaning of the ratio, so it is reported unavailable instead.` ; R\pit_factor_spec.py:1318 and :1315 FORMULAS_V1 say nothing about sign; no statement anywhere for fcf_conversion (v1 has no such factor; R\pit_coverage.py:580 gives only the arithmetic).

**Options:**
- A: refuse EBITDA <= 0 with a named code (e.g. ebitda_non_positive_denominator) for BOTH, declared in one digested domain dict with applies_to (net_debt_ebitda, fcf_conversion); Q renormalises for the ~37.8% of the universe with EBITDA <= 0
- B: score any finite EBITDA != 0 (ordering among negative-EBITDA rows inverts, as InterestCoverageRank's note already accepts for negative OI)
- C: refuse EBITDA == 0 only and add a band like EBITDA_GROWTH_BASE_POLICY_V1.max_abs_rate so a near-zero EBITDA cannot produce a huge ratio -- no precedent

## OD-08

**Question.** A missing cash fact in net_debt (and in enterprise_value): refuse the row with a named reason, or substitute 0.0 as v1 does?

**Blocks:** net_debt_ebitda, ev_ebitda_supplement

**What the repository already says.** R\company_scoring.py:738 `return float(total_debt) - (float(cash) if is_finite(cash) else 0.0)`; R\pit_policy.py:993-996 absence_limits headline: a row that does not resolve means 'this ladder found nothing' and NOTHING MORE; R\pit_factor_spec.py:557-567 DEBT_CASH_EBITDA lists cash as a REQUIRED primitive.

**Options:**
- A: refuse (no_cash_at_period); consistent with pit_policy.absence_limits and with DEBT_CASH_EBITDA requiring cash; cash coverage is near-universal (cash_vector pct_ge_3 99.9)
- B: v1 behaviour, net_debt = total_debt - 0.0 when cash is absent

## OD-09

**Question.** Period rule: are total_debt and cash taken at the balance sheet whose period_end == the EBITDA period (qtrs=0) and OCF/capex at (P, qtrs=4) -- the revenue@P / interest@P pattern -- or newest non-stale per concept (what eligible_peers and the census count), or a hybrid (instants newest, durations matched)?

**Blocks:** net_debt_ebitda, fcf_conversion, debt_market_cap, ev_ebitda_supplement

**What the repository already says.** R\pit_replay.py:909-913 comment: interest expense is a LEVEL at that period, 'the revenue@P pattern, because a ratio is one period's numbers'; R\pit_factor_spec.py:561 and :630 the Q rules match only (operating_income, depreciation_amortisation); R\pit_coverage.py:716-721 the census resolves OCF/capex/total_debt newest non-stale and never resolves cash; the v4 precedent moved a same-period rule into the eligibility rule (OPERATING_INCOME_AND_INTEREST_MATCHED_V2, :591-612).

**Options:**
- A: LEVEL AT P for all four; refusals no_total_debt_at_ebitda_period / no_cash_at_ebitda_period / no_ocf_at_ebitda_period / no_capex_at_ebitda_period; n_eligible - n_values measures the eligibility over-count as it does for interest_coverage
- B: newest non-stale per concept via pit_coverage.resolve_concept (a balance sheet up to 6 months newer than the annual EBITDA; an OCF/capex year that can differ from the EBITDA year)
- C: hybrid: instants newest non-stale, durations matched to P

## OD-10

**Question.** Negative net debt (cash > debt): legitimate and scored as-is (ranks best under lower-is-better), or floored/flagged?

**Blocks:** net_debt_ebitda

**What the repository already says.** R\company_scoring.py:744 `"""NetDebt / EBITDA. Negative is possible and is financially strong.`; nothing in v2 specs or docs contradicts it.

**Options:**
- A: legitimate, scored (v1: 'Negative is possible and is financially strong'); under PERCENTILE_RANK with higher_is_better=False the most negative ratio scores +100 (probe)
- B: floor at zero or treat net cash as a distinct state -- no precedent

## OD-11

**Question.** GAP 7: what is the single declared translation from the spec primitive 'price_adjusted' to the valuation price basis, where does it live, and what are require_traded / require_split_check for market_cap_as_of_detail?

**Blocks:** debt_market_cap, ev_ebitda_supplement, pe_absolute, pe_relative

**What the repository already says.** R\pit_price_basis.py:171-174 `PRICE_ROLE_REQUIRED_BASIS: dict[str, str] = { PRICE_ROLE_VALUATION: PRICE_RAW_AS_TRADED, ...` (digested, R\pit_frozen_spec.py:419); R\pit_factor_spec.py:511, :492, :1037 name price_adjusted; R\pit_valuation_spec.py:2290-2292 'The FROZEN pit_factor_spec is still NOT edited; the divergence is recorded'; R\pit_rawprice.py:1153-1154 `require_split_check: bool = False, require_traded: bool = False,`; audit v4-17 NOT FIXED (docs:188, :232).

**Options:**
- A: a digested constant in pit_price_basis (SPEC_PRIMITIVE_PRICE_BASIS_V1 = {'price_adjusted': PRICE_ROLE_REQUIRED_BASIS[PRICE_ROLE_VALUATION]}) applied by one engine price resolver, added to COMPONENTS at v6; frozen spec bodies untouched; require_split_check True (consistent with normalised_pe's refusal of no_events_recorded) and require_traded True
- B: SPECS_V5 with PRICED_EPS_V3 / EV_EBITDA_SIX_V2 / DEBT_AND_MARKET_CAP_V2 naming price_raw_as_traded (moves FACTOR_SPEC_VERSION_V5, ENGINE_MUST_DERIVE_FROM, pit_replay.FACTOR_SPEC_VERSION, REPLACED_BY_VERSION and the v4 test pins)
- C: both; and/or the function defaults (require_split_check False, require_traded False) with the split_check status recorded on the row

## OD-12

**Question.** debt_market_cap on the office rung: keep it uncapped as the spec deliberately declares, or extend the sic2 ceiling used for the valuation factors since it also uses a market value?

**Blocks:** debt_market_cap

**What the repository already says.** R\pit_factor_spec.py:287-290 `#: `debt_market_cap` is deliberately NOT here. It uses a market value and is a #: DEBT-pillar factor, and the owner's ruling was about the valuation peer #: group; giving it a ceiling on my own authority would be a modelling decision #: nobody made.`; :292 `VALUATION_FACTORS: frozenset[str] = frozenset({"ev_ebitda", "pe_ratio"})`.

**Options:**
- A: keep uncapped (as declared)
- B: sic2 ceiling like VALUATION_FACTORS -- a spec change requiring a successor spec version

## OD-13

**Question.** ev_ebitda_supplement: which transform (convex extreme_valuation_transform_v1 at a cohort anchor with direction embedded; benchmark_anchored tanh with a declared k and LOWER_IS_BETTER applied; percentile rank), which anchor statistic (cohort median / winsorized mean 5-95 / 50-75 band mean), which peer floor (12 / 3 / 4 or 8 by transform), target in or out of the anchor population, and which target-side screens (refuse EV <= 0; refuse outside [0.1, 300] or let the transform floor handle it)?

**Blocks:** ev_ebitda_supplement

**What the repository already says.** R\pit_valuation_spec.py:463-466 BENCHMARK_ANCHORED at ANCHOR_COHORT_MEDIAN; R\pit_factor_spec.py:1309-1311 FORMULAS_V1 '... anchor = cohort median' and no transform; :949 `min_peer_count=12,` with the band rationale and :954 `graph_node="cohort_mean_ev_ebitda",` (winsorized mean, R\pit_derive.py:642); :497-498 screens and band for PEERS, not applied by eligible_peers (:2466-2472); R\pit_factor_blocks.py:676 `"ev_ebitda_supplement": LOWER_IS_BETTER,` vs :674-675 EMBEDDED_IN_TRANSFORM for pe_*; R\pit_normalization.py:749 the default BENCHMARK_ANCHORED scale is the margin k 0.06; measured P(N>=3) 34.2% vs P(N>=12) 7.0% (R\pit_factor_spec.py:2650).

**Options:**
- Convex transform at the cohort anchor (sign embedded; SUBFACTOR_DIRECTION_V1 relabelled EMBEDDED_IN_TRANSFORM -- a digested body change)
- benchmark_anchored_score with a declared scale (cohort IQR of multiples, min 8, or fixed) and LOWER_IS_BETTER applied after
- PERCENTILE_RANK, lower-is-better, MIN_RANK_PEERS 4; anchor: median vs winsorized mean vs band mean; screens as declared in EV_EBITDA_SIX applied as value checks

## OD-14

**Question.** EV debt-rung walk: gate the ladder's FIRST resolving rung (strict; UNDETERMINED/REJECTED -> unavailable) or walk to the first ELIGIBLE rung; and is the gate applied to cohort MEMBERS' multiples before the anchor is formed?

**Blocks:** ev_ebitda_supplement

**What the repository already says.** R\pit_valuation_spec.py:2223-2226 'THE GATE THAT BITES. `EV/EBITDA` calls this before it forms a multiple'; R\pit_coverage.py:465-471 the FIRST rung with a non-stale fact wins; R\pit_policy.py:741 '(3) THE RUNG IS DATA, NOT DIAGNOSTICS'; the owner's rule: the policy must not be weakened for coverage.

**Options:**
- Strict: first non-stale rung is gated; debt_rung_undetermined / debt_rung_rejected_measured refuse the subfactor
- First-eligible walk (the map is unchanged but the ladder walk differs; coverage changes)
- Members: gate every member the same way vs availability-only membership as EV_EBITDA_SIX declares

## OD-15

**Question.** pe_relative: which transform and anchor -- convex extreme_valuation_transform_v1 with anchor = cohort median P/E (what _build_pillar executes and the design record describes), benchmark_anchored tanh with a declared k, or PERCENTILE_RANK (SPECS_V4['pe_ratio']) -- and is the median over the cohort EXCLUDING or INCLUDING the target?

**Blocks:** pe_relative

**What the repository already says.** R\pit_valuation_spec.py:2461 `relative_detail = (valuation_score_detail(pe["pe"], float(median))` (worked example, gated at FACTOR_SPEC_VERSION_V2 :2459); R\pit_factor_spec.py:1308 `"pe_relative": "pe as pe_absolute; anchor = cohort median pe (ANCHOR_COHORT_MEDIAN); BENCHMARK_ANCHORED",`; :980 `normalization_type=pit_normalization.PERCENTILE_RANK,` for pe_ratio; docs/SHAFFER-EQUITY-V2-CANDIDATE.md:121 'quadratic beyond a knot at 2x the peer median'.

**Options:**
- T1: convex transform at the cohort median (probe: P/E 100 vs median 28 -> -37.33); direction embedded
- T2: tanh benchmark_anchored_score with k = cohort IQR of P/E (min 8) or a fixed multiple; LOWER_IS_BETTER applied
- T3: PERCENTILE_RANK of P/E, lower-is-better, MIN_RANK_PEERS 4; median population excludes (COHORT_RANK) or includes (COHORT_DISPERSION) the target

## OD-16

**Question.** Near-zero-positive EPS (0 < EPS <= $0.01): does it enter pe_absolute (scoring at the -100 floor) and the pe_relative cohort median?

**Blocks:** pe_absolute, pe_relative

**What the repository already says.** R\pit_eps.py:373 `EPS_NEAR_ZERO_ABS = 0.01`; :464-465 `record["pe"] = price / float(ttm_eps)` / `record["extreme"] = leg == PE_AVAILABLE_EXTREME`; R\pit_factor_spec.py:513 `positive_screen="earnings_per_share_pit > 0",`; pit_eps_cohort reports N_PE and N_PE_incl_extreme separately (R\pit_eps_cohort.py:28).

**Options:**
- Include both (PRICED_EPS_V2's screen 'earnings_per_share_pit > 0' admits it; price_earnings computes the multiple with extreme=True)
- Exclude both with the named leg 'available_extreme_valuation', matching pit_eps_cohort's N_PE definition
- Include in pe_absolute only, exclude from the median

## OD-17

**Question.** Action-gate semantics for the P/E: (i) does the share-basis window start at the EPS period_end (as coded) or at the vintage's filing/available date; (ii) does an EMPTY window refuse (as coded: no_events_recorded) or only a listing with NO action history at all (pit_shares' any_row semantics)? A change moves a digested-version body (ACTION_GATE_VERSION) -> v6.

**Blocks:** pe_absolute, pe_relative

**What the repository already says.** R\pit_price_basis.py:448-449 `if not rows: return ActionGate(factor=None, status=GATE_NO_EVENTS, ...`; :692 `gate = pit_safe_split_factor(conn, listing_id, eps_period_end, as_of, as_of)`; :693-697 'no_events_recorded lands here too, and deliberately'; R\pit_shares.py:1224-1227 `"SELECT COUNT(*) AS n FROM pit_corporate_action WHERE listing_id = ?"` and :1240 `status = SPLIT_CHECKED if (any_row and any_row["n"]) else SPLIT_NO_EVENTS_RECORDED`; R\test_pit_price_basis.py:186 covers only the no-rows listing.

**Options:**
- Keep as coded: window (eps_period_end, as_of], empty window = refused
- Window from max component available date; empty window with listing history = factor 1.0; no action rows at all = refused (listing-level COUNT(*))
- Keep the period_end start but adopt the listing-level semantics

## OD-18

**Question.** Valuation price selection: roll_back_days and require_traded for raw_close_as_of at a month-end as-of, and which listing carries the price and listing_id when scored_universe_as_of returns more than one for an entity?

**Blocks:** pe_absolute, pe_relative, ev_ebitda_supplement, debt_market_cap

**What the repository already says.** R\pit_rawprice.py:610 `def raw_close_as_of(conn, listing_id, date, *, ... roll_back_days: int = 0, require_traded: bool = False)`; R\pit_identity.py:178 `DEFAULT_PRICE_MAX_AGE_DAYS = 10`, :1284-1288 volume > 0 within the window, :1290 `GROUP BY e.entity_id, e.cik, l.listing_id`; R\pit_replay.py:567-569 'a listing id guessed from the entity would be an invention'.

**Options:**
- roll_back_days = DEFAULT_PRICE_MAX_AGE_DAYS (10), require_traded True, mirroring the universe gate; multi-listing: a declared rule (most recent volume>0 bar, ties by symbol) recorded as a body
- Function defaults (roll_back_days 0, require_traded False: exact session or refusal); multi-listing: refuse as ambiguous with a named reason
- Engineering default with the choice written only into resolved_inputs_json

## OD-19

**Question.** May pe_relative / ev_ebitda_supplement draw on a finer stored peer set below pit_coverage.MIN_COHORT_N = 12 (down to the spec floor before the sic2 ceiling), or does the engine's one-stored-cohort-per-entity rule stand (so pe_ratio's min_peer_count 3 never binds)?

**Blocks:** pe_relative, ev_ebitda_supplement

**What the repository already says.** R\pit_coverage.py:668 `if info is None or info[2] < MIN_COHORT_N:`; audit v4-16 'GAP 5 is three-stage, not two' (docs:375).

**Options:**
- Keep the finest stored set with >= 12 members (engine rule today)
- Walk finer stored sets down to the spec floor before the sic2 ceiling (changes cohorts for many entities)

## OD-20

**Question.** V assembly: keep the generic pit_factor_blocks.block_score over SUBFACTOR_WEIGHTS with an explicit depends_on guard, or route V through pit_valuation_spec.presence()/valuation_pillar() (dependency rule, PRESENT_FULL/DEGRADED/ABSENT, VALUATION_PEER_SET_INSUFFICIENT precedence, quality rule UNDECIDED); which values fill valuation_presence_state / valuation_data_quality / valuation_quality_rule / valuation_subfactors_json / earnings_discontinuity_state; and may earnings_discontinuity_state come from pit_eps_ttm.sign_crossing or only from eps_transition_from_pair?

**Blocks:** pe_absolute, pe_relative, ev_ebitda_supplement

**What the repository already says.** Probe: block_score(V, {pe_relative: 10.0}) = 10.0 while presence({'pe_relative': True}).state = ABSENT; R\pit_valuation_spec.py:641 `if item.depends_on is not None and not survived.get(item.depends_on, False):`, :2776 validate() pin; R\pit_factor_blocks.py:372 `scored = pit_score_signature.score_v2(` without reasons; R\pit_replay.py:1546 `None, None, None, None, None,`; R\pit_valuation_spec.py:1901 '-- earnings_discontinuity_state: EARNINGS_SIGN_CROSS or NULL'; docs/MODEL-LINEAGE.md:724 eps_transition_from_pair 'is now the only supported entry point'.

**Options:**
- Generic block_score + depends_on guard; presence state from subfactor_mask; discontinuity from the TTM row's sign_crossing
- Full valuation_pillar path; pass reasons={'valuation': pillar.reason} into score_v2 (assemble gains a reasons kwarg); data_quality NULL with rule UNDECIDED; discontinuity only via eps_transition_from_pair (NULL when the basis is unresolved)
- Generic block_score only; the five columns stay NULL until the quality rule is decided

## OD-21

**Question.** May the pe_relative cohort mix TTM methods (annual_figure vs four_quarters) and EPS rungs (diluted vs basic), and the EV cohort mix cash rungs (restricted-cash tag is an upper bound), with the method/rung distribution reported beside the anchor -- or must members be restricted to the target's method and rung?

**Blocks:** pe_relative, ev_ebitda_supplement

**What the repository already says.** R\pit_eps.py:1039 'method is stored so a consumer can group by it'; :303 the basic rung 'FLATTERS the score ... must report the rung distribution'; R\pit_policy.py:560 `note="Includes restricted cash: an upper bound on the first rung."`.

**Options:**
- Mix, recording method / concept_key / cash rung on each row and the distribution on the cohort record
- Restrict members to the target's method and the diluted rung (coverage cost unmeasured)

## OD-22

**Question.** Governance: FORMULA_GOLDEN_V1 is digested and has no ebitda_benchmark (or Q) witness. Does Stage A wait for a cut that adds a witness (audit R3 standard), or ship under v5 with an engine-local test and add the witnesses at the v6 cut?

**Blocks:** (governance)

**What the repository already says.** R\pit_frozen_spec.py:384 `("pit_factor_spec", "FORMULA_GOLDEN_V1", "golden witnesses for FORMULAS_V1"),`; R\pit_replay.py:1907 `if "ebitda" in case:` -- _golden_eval synthesises only EBITDA triples and OI/interest; R\pit_factor_spec.py:1398-1432 carries no benchmark or Q case.

**Options:**
- Cut first (v6 adds a benchmark witness; Stage A then builds against v6) -- Stage E moves before Stage A
- Build Stage A under v5 with the engine-local fixture test; add the benchmark witness together with the Q witnesses at the v6 cut (_golden_eval extended in the same commit)

## OD-23

**Question.** Governance: do R_BENCHMARK_AMBIGUOUS, R_PE_CHAIN_NOT_BUILT and R_NO_V2_NORMALIZATION stay in ENGINE_REASONS with their REASON_WHY rewritten as history (precedent: R_PRIMITIVE_UNAVAILABLE kept for v2/v3 rows), or move into RETIRED_REASONS so validate() refuses any plan that emits them?

**Blocks:** (governance)

**What the repository already says.** R\pit_replay.py:261 `RETIRED_REASONS: Tuple[str, ...] = ("no_assets_at_lag1", "no_assets_at_lag2")`; :320-323 'it stays in the vocabulary because rows stamped under v2/v3 carry it, and validate() refuses a plan that uses it without a declaring spec'; :2000 `if plan.refusal_reason in RETIRED_REASONS:`.

**Options:**
- Keep as history (rows in the disposable pilot carry them; every reason must stay in ENGINE_REASONS for test_pit_replay's vocabulary check)
- Retire into RETIRED_REASONS

## OD-24

**Question.** At the v6 cut, are the six pending-v5 items (max_abs_rate 10.0; MATCHED_V2 rule; refusal vocabulary; PERCENTILE_RANK for coverage; negative-OI ordering; single-factor Q availability) confirmed, left pending, or changed? Confirming changes the AWAITING_OWNER_CONFIRMATION status strings INSIDE two digested bodies and a test pin.

**Blocks:** (governance)

**What the repository already says.** R\pit_frozen_spec.py:862-880 `"owner_confirmation_pending_v5": (` lists the six; R\pit_factor_spec.py:1351 and :1378 carry AWAITING_OWNER_CONFIRMATION inside digested bodies; R\test_pit_frozen_spec.py:589-591 pins both.

**Options:**
- Confirm all six (status strings updated; pending list retired; test_pit_frozen_spec.py:589-591 re-pinned)
- Leave pending; v6 carries them unchanged
- Change any (e.g. tie negative-OI rows at the cohort floor; a within-block coverage floor) -- an economics change

## OD-25

**Question.** Cut cadence: ONE v6 cut with every V/Q body decided up front (then B, C, D are glue under v6), or a cut per stage (v6 at B, v7 at C, v8 at D), or benchmark now without a cut and one cut for the rest?

**Blocks:** fcf_conversion, net_debt_ebitda, debt_market_cap, ev_ebitda_supplement, pe_absolute, pe_relative

**What the repository already says.** R\pit_replay_manifest.py:916 `if not freeze.get("intact"):` refuses; R\pit_replay.py:1669 `require_gate()` before any writer; docs/POST-REBOOT-CHECKLIST.md:72 'No edits to spec_freeze_v1/v2/v3/v4 in place -- a change is a v5.'; the benchmark's registry entry, band and formula are already digested (R\pit_normalization.py:1849-1862, :908-909; R\pit_factor_spec.py:1292-1294).

**Options:**
- One cut before Stage B wiring: every intermediate state stays gate-testable; requires OD-05..OD-21 decided first and _golden_eval extended in the same commit
- A cut per stage: smaller decisions per cut, but four sealed records, four rounds of validate()/test re-pins, and no gate-testable state between a body change and its cut
- Hybrid: Stage A under v5 (no body moves), one cut for B+C+D


---

# Contradictions found

- Band floor: FORMULAS_V1 (R\pit_factor_spec.py:1294 '|band| >= BENCHMARK_MIN_COHORT_V1 (benchmark_margin_50_75)') and the COMPONENTS description (R\pit_frozen_spec.py:391 'the smallest band that may carry a benchmark') vs the function (R\pit_normalization.py:932 `if len(pairs) < floor:`, :940 `if not band:`) which floors PAIRS and accepts a band of 1; R\test_pit_normalization.py:165 asserts n_band == 2 passes; the spec body says min_band_members=3 (:795).
- pe_relative transform declared three ways: SPECS_V4['pe_ratio'] PERCENTILE_RANK (R\pit_factor_spec.py:980); SUBFACTORS / FORMULAS_V1 BENCHMARK_ANCHORED at cohort median (R\pit_valuation_spec.py:438-439; R\pit_factor_spec.py:1308); the worked example uses the convex transform at the median (R\pit_valuation_spec.py:2461) and the design record says 'knot at 2x the peer median' (docs/SHAFFER-EQUITY-V2-CANDIDATE.md:121).
- pe_absolute: SUBFACTORS normalization_type BENCHMARK_ANCHORED (R\pit_valuation_spec.py:413) vs FORMULAS_V1 'S = extreme_valuation_transform_v1(pe, PE_ABSOLUTE_ANCHOR_V1)' (R\pit_factor_spec.py:1305-1306); the convex transform is not one of normalize()'s five types (R\pit_normalization.py:871-888).
- EV anchor statistic: cohort median (SUBFACTORS :466, FORMULAS_V1 :1311) vs winsorized mean 5-95 (spec graph_node :954, R\pit_derive.py:642) vs the 50-75 band rationale carried by min_peer_count 12 / min_band_members 3 on the ev_ebitda spec (:949-951).
- Direction for EV: SUBFACTOR_DIRECTION_V1['ev_ebitda_supplement'] = LOWER_IS_BETTER (R\pit_factor_blocks.py:676) while pe_* are EMBEDDED_IN_TRANSFORM (:674-675); using the convex transform for EV would double-apply the sign.
- GAP 7: spec vocabulary 'price_adjusted' (PRICED_EPS inherited by PRICED_EPS_V2 :511/:542-556, EV_EBITDA_SIX :492, DEBT_AND_MARKET_CAP :571, pit_derive market_cap node :518) vs PRICE_ROLE_REQUIRED_BASIS raw as-traded (R\pit_price_basis.py:171-174) and SUBFACTORS' PRICE_RAW_AS_TRADED; docs/MODEL-LINEAGE.md:683 says EV/EBITDA 'previously named price_adjusted' but only the SUBFACTOR body changed.
- FORMULAS_V1['pe_absolute'] cites 'one share basis (pit_price_basis.PAIR_PATHS)' (R\pit_factor_spec.py:1304-1305), but PAIR_PATHS governs two-period EPS pairs (R\pit_price_basis.py:266-268); the single-P/E basis mechanism is normalised_pe (:676-713).
- Action gate: docstrings and MODEL-LINEAGE describe refusal for LISTINGS without action rows (R\pit_price_basis.py:371-376 'no_events_recorded ... 1,903 of 2,576 listings carry any corporate action'), but the code refuses on an EMPTY WINDOW (:448-449 `if not rows:`), unlike pit_shares.split_factor_between's listing-level COUNT(*) (R\pit_shares.py:1224-1227, :1240); the window starts at eps_period_end (:692) while the share side anchors at measured_at.
- Missing cash: v1 substitutes 0.0 (R\company_scoring.py:738) vs pit_policy.absence_limits 'an absent value is not a zero' (R\pit_policy.py:993-996, :1007).
- Debt rungs for Q: DEBT_RUNG_EVEBITDA_V1 is EV/EBITDA-scoped by name and docstring (R\pit_valuation_spec.py:2188), yet pit_policy's consumer rule (2) names net_debt_ebitda and debt_market_cap and forbids a clean leverage percentile over a rung mixture (R\pit_policy.py:733-739); no executable rule exists for Q.
- GAP 5: spec min_peer_count 3 for the rank factors (R\pit_factor_spec.py:1019) vs MIN_RANK_PEERS 4 (R\pit_normalization.py:319); spec 12 vs MIN_DISPERSION_COHORT 8 (:311); pit_coverage.MIN_COHORT_N 12 upstream (R\pit_coverage.py:88, :668) makes 'insufficient_peers' a third stage; audit v4-16 NOT FIXED.
- benchmark_level column: DDL comment 'Industry | Sector Fallback' (R\pit_intermediates.py:412) and the engine writes the peer-set rung there (R\pit_replay.py:1497, :1528) -- not a slot for the numeric M_s; NormResult.anchor/anchor_source are dropped by the engine today (:1399-1402).
- Eligibility rules declare value screens the engine never applies: EV_EBITDA_SIX positive_screen/value_band (R\pit_factor_spec.py:497-498), DEBT_AND_MARKET_CAP 'market_cap > 0' (:574), PRICED_EPS 'earnings_per_share_pit > 0' (:513); eligible_peers says 'AVAILABILITY only ... apply them in the cohort builder' (:2466-2472).
- V assembly: pit_factor_blocks.block_score renormalises over present subfactors without the dependency/standalone rule (probe: pe_relative alone scores 10.0) while pit_valuation_spec.presence() makes it ABSENT (:641, :652); assemble() calls score_v2 without reasons (R\pit_factor_blocks.py:372) so VALUATION_PEER_SET_INSUFFICIENT never reaches pit_score notes.
- Status prose diverges per flip and is not test-pinned: module docstring 'ONE is REFUSED' (R\pit_replay.py:61-73), KNOWN_LIMITATIONS 'Seven of thirteen' (:572-582), REASON_WHY 'declares seven specs' (:307), STILL_BLOCKED 'ENGINE computes 6 of 13' (R\pit_frozen_spec.py:743), MODEL-LINEAGE 'Census 6 / 1 / 6' (docs:1298); only the census counts are pinned (test_pit_replay.py:158-161).
- pit_replay_manifest.freeze_state enumerates FREEZE_V5 (R\pit_replay_manifest.py:305-306) but its validate() chain tuple stops at FREEZE_V4 (:1278) and then requires the last chain record to name the live freeze (:1284) -- a v6 cut fails there until extended.
- MODEL-LINEAGE names EPS transition states EARNINGS_SIGN_CROSS_POSITIVE/NEGATIVE (docs:525) while the code uses earnings_sign_cross_loss_to_profit / profit_to_loss and a single discontinuity_state 'EARNINGS_SIGN_CROSS' (R\pit_valuation_spec.py:1024).
- FORMULA_GOLDEN_V1 carries no witness for ebitda_benchmark or any Q/V key (R\pit_factor_spec.py:1398-1432) although the audit's R3 asked for one per scored factor, and _golden_eval can only synthesise EBITDA triples and OI/interest (R\pit_replay.py:1907-1914).
- docs/HISTORICAL-DATA-MAP.md:41 still says prices/corporate actions are 'PLANNED ... tables exist, empty', while pit_rawprice / pit_price_basis quote measured coverage (1,270 splits, 226 spin-offs across 1,903 of 2,576 listings) -- stale text, not verified against the store.

# Open questions (measurements not taken)

- Store measurements not taken (no read-only query was needed for this plan): how many EBITDA_AND_REVENUE-eligible vectors clear the registry scale's min_cohort = 8; typical n_band under the 12-vector floor; how often a target's own EBITDA sits inside its band (sizes OD-01/02/03); the n_eligible - n_values gap for margin (SQL eligibility vs in-memory revenue@P).
- Unmeasured for Stage B: entities with total_debt AND cash at the same balance-sheet period_end as their EBITDA period, and OCF/capex at P (sizes OD-09); row counts by tag for cash/debt/OCF/capex/shares; post-build coverage cannot be projected from pilot_sizing_derived.json (all three keys were 0 complete under no_v2_normalization_declared).
- Unmeasured for Stage D: how many (listing, EPS period_end, as_of) P/Es the empty-window GATE_NO_EVENTS behaviour would refuse; whether a split effective between an EPS period end and its filing date is restated in the filed figure for all issuers (basis for OD-17's window start); the '1,962 of the 7,102 peers' figure exists only in PRICED_EPS_V2.why (R\pit_factor_spec.py:551).
- Whether pit_normalization's registry validation accepts a BENCHMARK_ANCHORED entry with an anchor_source outside its own vocabulary (ANCHOR_COHORT_MEDIAN lives in pit_valuation_spec, :396) -- read only partially (R\pit_normalization.py:1590-1612); decides whether C1's registry option needs a vocabulary addition.
- Whether the v5 cut actually passed through an interim FROZEN_DIGEST = 'PENDING' state: commit 609cefe records only the final state; the Stage E procedure is reconstructed from verify()/validate()'s PENDING handling (R\pit_frozen_spec.py:516, :1087-1098).
- Byte growth per flipped row in a real pilot (the 630.19 vs 565.05 B figures are the 1.0-era capacity budget) and pit_replay_rss fixed-cost growth from the new Primitives fields and per-member share audits inside CohortCache; oracle parity (n_eligible_peer_calls) with price-conditional cohorts was not exercised.
- Whether pit_derive's golden trace places the target's own EBITDA inside sector_ebitda_vector (cohort_membership_flag is True in the golden values, R\pit_derive.py:2214, but the vector's membership rule is not stated).
- Whether the owner wants the interest_coverage refusal string to change from 'cohort_too_small_to_rank' to 'insufficient_peers' as a side effect of OD-04 option A (a live-row reason change under the same model version).
- Whether the empty-V case after Stage C/D keeps MASK_NONE as the only V spelling on refused rows, and whether pe_relative's cohort should be keyed per target (exclude-target medians break the (peer_set_id, factor) CohortCache key exactly as OD-01 A does for the benchmark).

---

# Steps (the implementation blueprint, per stage)

### A1  (A)  `pit_replay.py`

economics_changing=True; needs_owner_decision=OD-01

**What.** Add a third cohort mode beside COHORT_RANK/COHORT_DISPERSION (lines 437-438) whose NAME encodes the target policy chosen in OD-01, and extend the FactorPlan.cohort_use comment (411-414) to name three modes.

**Tests.** test_pit_replay.py: no existing pin on cohort_use; add check(FACTOR_PLAN['ebitda_benchmark'].cohort_use == pit_replay.COHORT_BENCHMARK)

```
COHORT_RANK = "rank_population_excludes_target"
COHORT_DISPERSION = "dispersion_sample_includes_target"
COHORT_BENCHMARK = "benchmark_pairs_<includes|excludes>_target"   # per OD-01
```

### A2  (A)  `pit_replay.py`

economics_changing=False; needs_owner_decision=OD-01,OD-02,OD-03

**What.** Flip FACTOR_PLAN['ebitda_benchmark'] (lines 444-451) from VERDICT_REFUSED_AMBIGUOUS to VERDICT_COMPUTABLE with cohort_use=COHORT_BENCHMARK and no refusal_reason; keep norm_key 'EBITDAExcessLevel', spec_key, needs_cohort=True, mask_code 'BENCH'.

**Tests.** test_pit_replay.py:158-161 -> 7 COMPUTABLE / 0 REFUSED_AMBIGUOUS / 6 NOT_COMPUTABLE, test_pit_replay.py:243-245 -> replace the 'indistinguishable from ebitda_scale' pin with back['factors']['ebitda_benchmark']['verdict'] == 'COMPUTABLE', test_pit_replay.py:1089 -> len(rows) == len(refused) == 6; :1101-1103 -> 'ebitda_benchmark' not in by_key, test_pit_replay.py:221-226 reachable stays 0.60

```
"ebitda_benchmark": FactorPlan(
    key="ebitda_benchmark", block=_B.BLOCK_E, role=_B.ROLE_SCORING,
    verdict=VERDICT_COMPUTABLE, norm_key="EBITDAExcessLevel",
    spec_key="ebitda_benchmark", needs_cohort=True,
    cohort_use=COHORT_BENCHMARK, mask_code="BENCH",
    note=("input margin = EBITDA / revenue AT P; bar M_s = mean margin of the "
          "50-75 EBITDA band (benchmark_margin_50_75); BENCHMARK_ANCHORED (D1)")),
```

### A3  (A)  `pit_replay.py`

economics_changing=False; needs_owner_decision=-

**What.** RAW_FIELD line 942 `"ebitda_benchmark": ("ebitda", "ebitda"),` -> `("margin", "margin")` (refusals R_NO_EBITDA / R_NO_REVENUE_AT_P already set at 852-858); SOURCE_CONCEPTS line 956 `("oi", "da")` -> `("oi", "da", "rev@P")`, identical to ebitda_efficiency's line 957.

**Tests.** pit_replay.validate() (1963) still finds a RAW_FIELD entry; no test pins the old tuple

```
RAW_FIELD["ebitda_benchmark"] = ("margin", "margin")
SOURCE_CONCEPTS["ebitda_benchmark"] = ("oi", "da", "rev@P")
```

### A4  (A)  `pit_replay.py`

economics_changing=False; needs_owner_decision=OD-02

**What.** CohortCache.get (985-1017): after the unchanged eligible_peers call (994-996, version=FACTOR_SPEC_VERSION -> EBITDA_AND_REVENUE), for COHORT_BENCHMARK collect per-member (ebitda, margin) PAIRS from Primitives where both are finite; record['pairs'], record['values'] = margins (the dispersion sample per OD-02), n_values = len(pairs). Existing branch unchanged for other modes.

**Tests.** new: a 12-member synthetic Primitives cohort yields n_values == 12 and pairs keyed by entity

```
if plan.cohort_use == COHORT_BENCHMARK:
    pairs: Dict[int, Tuple[float, float]] = {}
    for member in eligible.members:
        prim = self.primitives.get(int(member))
        if prim is not None and _finite(prim.ebitda) and _finite(prim.margin):
            pairs[int(member)] = (float(prim.ebitda), float(prim.margin))
    record["pairs"] = pairs
    record["values"] = {e: m for e, (_, m) in pairs.items()}
    record["n_values"] = len(pairs)
```

### A5  (A)  `pit_replay.py`

economics_changing=True; needs_owner_decision=OD-01,OD-02,OD-03

**What.** New helper benchmark_anchor(pairs, entity_id) placed before normalise (1027): build the (ebitda, margin) lists per OD-01, call pit_normalization.benchmark_margin_50_75, enforce spec.min_band_members (OD-03) with pit_factor_spec.REASON_NO_BAND; normalise() gains keyword benchmark=None and a third branch passing benchmark= and cohort_values= (OD-02) so its existing signature and the tests at 795-826 keep working.

**Tests.** new (fixture eb=[100..1200], mg=[0.10..0.21]): benchmark_margin_50_75 -> (0.17, 3); normalise('ebitda_benchmark', 0.30, {12 margins}, target, day, benchmark=0.17) -> score 98.24541388014812, scale 0.055, scale_source 'cohort_iqr', n_cohort 12, anchor 0.17, anchor_source 'cohort_p50_p75_mean_margin', new: cohort_values of 3 -> reason 'cohort_too_small_for_dispersion'; 12 identical margins -> scale 0.06, scale_source 'fixed'; benchmark=None -> 'benchmark_unavailable'; 2 pairs -> (None, 0), new: band of 2 from a 12-pair vector -> reason 'band_too_thin' (OD-03 A)

```
def benchmark_anchor(pairs, entity_id):
    items = [(e, p) for e, p in pairs.items() if <OD-01 policy on int(e) == int(entity_id)>]
    m_s, n_band, note = pit_normalization.benchmark_margin_50_75(
        [p[0] for _, p in items], [p[1] for _, p in items])
    min_band = pit_factor_spec.spec("ebitda_benchmark", FACTOR_SPEC_VERSION).min_band_members
    if m_s is not None and n_band < min_band:
        return None, n_band, len(items), pit_factor_spec.REASON_NO_BAND
    return m_s, n_band, len(items), None

# in normalise(..., benchmark=None):
if plan.cohort_use == COHORT_BENCHMARK:
    return pit_normalization.normalize(
        spec, value, benchmark=benchmark,
        cohort_values=list(cohort_values.values()),   # OD-02 population
        as_of_date=as_of)
```

### A6  (A)  `pit_replay.py`

economics_changing=False; needs_owner_decision=-

**What.** Row loop (1384-1402): for COHORT_BENCHMARK call benchmark_anchor before normalise; on a band refusal write reason verbatim; on success write compact codes bm (M_s), nb (n_band), np (pairs in the vector), as (result.anchor_source) beside ne/nv/nc/k/ks. benchmark_level stays the rung on pillar (1497) and score (1528) rows.

**Tests.** new: resolved_inputs_json on a scored benchmark row carries ne, nv, nc, k, ks, bm, nb, np, as, scope, fd, ev, p, test_pit_replay.py:1265-1300 oracle: codes must be identical across batched/unbatched arms (deterministic by construction)

```
if plan.cohort_use == COHORT_BENCHMARK:
    m_s, n_band, n_pairs, why = benchmark_anchor(record["pairs"], entity_id)
    resolved["nb"], resolved["np"] = n_band, n_pairs
    if why:
        reason = why
    else:
        result = normalise(key, raw, record["values"], entity_id, day, benchmark=m_s)
        ... (score / norm_method / reason / nc / k / ks as today)
        if result.anchor is not None:
            resolved["bm"] = result.anchor
            resolved["as"] = result.anchor_source
```

### A7  (A)  `pit_replay.py`

economics_changing=True; needs_owner_decision=OD-04

**What.** GAP 5 closure per OD-04: add FactorPlan.cohort_floor (397-434) computed at import from spec.min_peer_count, the registry scale's min_cohort when cohort-sourced, MIN_RANK_PEERS + 1 for COHORT_RANK and BENCHMARK_MIN_COHORT_V1 for COHORT_BENCHMARK; enforce once in CohortCache.get on n_values with reason pit_store.REASON_NO_PEERS ('insufficient_peers'); validate() (1921+) asserts no computable plan's floor is below a downstream floor. Under OD-04 B only the validate() assertion lands.

**Tests.** new: FACTOR_PLAN['ebitda_benchmark'].cohort_floor == 12; ranks == 5 under OD-04 A, test_pit_replay.py:795-826 rank/dispersion tests unchanged (normalise() itself still refuses below its own floors), interest_coverage rows that today read 'cohort_too_small_to_rank' would read 'insufficient_peers' under OD-04 A -- a reason-string change on live rows, name it in the run summary

```
def _declared_floor(plan):
    spec = pit_factor_spec.spec(plan.spec_key, FACTOR_SPEC_VERSION)
    reg = _REGISTRY.get(plan.norm_key)
    floors = [spec.min_peer_count]
    if reg is not None and reg.scale is not None and reg.scale.source != pit_normalization.SCALE_FIXED:
        floors.append(reg.scale.min_cohort)
    if plan.cohort_use == COHORT_RANK:
        floors.append(pit_normalization.MIN_RANK_PEERS + 1)
    if plan.cohort_use == COHORT_BENCHMARK:
        floors.append(pit_normalization.BENCHMARK_MIN_COHORT_V1)
    return max(floors)   # ebitda_benchmark -> 12 on n_values
```

### A8  (A)  `pit_replay.py`

economics_changing=False; needs_owner_decision=OD-23

**What.** Bookkeeping that must move with the flip: REASON_WHY[R_BENCHMARK_AMBIGUOUS] (282-305) rewritten as history and kept in ENGINE_REASONS per OD-23; KNOWN_LIMITATIONS 'Seven of thirteen ...' (572-582) -> 'Six of thirteen ... ebitda_benchmark is computed under the MARGIN reading (D1)'; module docstring 'ONE is REFUSED' (61-73); ENGINE_VERSION (171) -> 'pit_replay/1.2' with a 1.2 note at 169-170. Also pit_frozen_spec.STILL_BLOCKED['the_diagnostic_replay_itself'] (741-754, undigested prose) and docs/MODEL-LINEAGE.md:1298 'Census 6 / 1 / 6'.

**Tests.** test_pit_replay.py:217-219 every ENGINE_REASONS entry keeps a >= 60-char why, test_pit_replay.py:246-248 len(known_limitations) >= 6 still holds (8 -> 8 entries, one rewritten)

```
ENGINE_VERSION = "pit_replay/1.2"
"Six of thirteen feature keys are UNAVAILABLE by construction on every row: pe_absolute and pe_relative for the unbuilt P/E chain, and ev_ebitda_supplement, fcf_conversion, net_debt_ebitda and debt_market_cap for a missing registered transform. ebitda_benchmark is computed under the MARGIN reading (D1) ..."
```

### A9  (A)  `test_pit_replay.py`

economics_changing=False; needs_owner_decision=-

**What.** Add a benchmark section next to the cohort tests (around 795-826) exercising the fixture, the four refusal shapes, the E mask with BENCH=1 (829-835), the resolved codes, and validate() == []; re-pin the census, plan-record and refused-row assertions listed in A2.

**Tests.** expected values as computed by the probe (independent of OD-01 because 12 pairs are handed to the function)

```
eb = {i: 100.0 * i for i in range(1, 13)}; mg = {i: round(0.09 + 0.01 * i, 4) for i in range(1, 13)}
m_s, n_band, n_pairs, why = pit_replay.benchmark_anchor({i: (eb[i], mg[i]) for i in eb}, 1)
check("M_s is the mean margin of the 3-peer 50-75 band", close(m_s, 0.17) and n_band == 3 and why is None)
res = pit_replay.normalise("ebitda_benchmark", 0.30, mg, 1, "2019-06-28", benchmark=m_s)
check("the excess is scored against the bar with the cohort IQR", close(res.score, 98.24541388014812, 1e-9) and close(res.scale, 0.055) and res.scale_source == "cohort_iqr" and res.n_cohort == 12)
check("E mask reads BENCH=1 when the benchmark resolves", pit_replay._mask(pit_factor_blocks.BLOCK_E, {"ebitda_benchmark": 1.0}) == "BENCH=1,GROW=0,EFF=0,ACC=0")
```

### B1  (B)  `pit_replay.py`

economics_changing=False; needs_owner_decision=OD-07,OD-08,OD-09

**What.** Primitives (735-769): add total_debt_rung / total_debt_period / total_debt / total_debt_bound, cash_rung / cash_period / cash, ocf_rung / ocf_period / operating_cash_flow, capex_rung / capex_period / capex, net_debt, net_debt_ebitda, fcf_conversion, listing_id, market_cap, debt_market_cap; add reason constants (244-266) and ENGINE_REASONS / REASON_WHY entries (272-330) for the OD-07/08/09/11 codes and, if OD-06 B, reuse pit_valuation_spec.REASON_DEBT_RUNG_REJECTED / _UNDETERMINED verbatim.

**Tests.** test_pit_replay.py:217-219 every new reason needs a >= 60-char why

```
total_debt_rung: Optional[str] = None
total_debt_period: Optional[str] = None
total_debt: Optional[float] = None
cash: Optional[float] = None
operating_cash_flow: Optional[float] = None
capex: Optional[float] = None
net_debt_ebitda: Optional[float] = None
fcf_conversion: Optional[float] = None
listing_id: Optional[int] = None
market_cap: Optional[float] = None
debt_market_cap: Optional[float] = None
```

### B2  (B)  `pit_replay.py`

economics_changing=True; needs_owner_decision=OD-07,OD-08,OD-09,OD-10

**What.** resolve_primitives (795-936): add a level-at-period resolver for instants (qtrs=0) and durations (qtrs=4) mirroring the interest@P loop at 919-926, handling COMBINE_SUM rungs (all components at the period, as pit_coverage.resolve_concept 483-495 does); period rule per OD-09 (A: at the EBITDA period; B: pit_coverage.resolve_concept newest non-stale). Then net_debt_ebitda and fcf_conversion with the domain refusals BEFORE any direction (OD-07, OD-08, OD-10); capex enters as the filed positive outflow (pit_policy.py:629-630).

**Tests.** FORMULA_GOLDEN_V1 cases (Stage E body): debt 1000 / cash 200 / EBITDA 400 -> 2.0; OCF 500 / capex 200 / EBITDA 400 -> 0.75; debt 100 / cash 300 / EBITDA 400 -> -0.5 (OD-10 A); EBITDA 0 -> the OD-07 refusal code, _golden_eval (1897-1918) extended to synthesise (eid, tag, 0) instants and (eid, tag, 4) durations

```
def _level_at(index, entity_id, ladder, period, qtrs, day):
    for rung in ladder.rungs:
        if not rung.eligible(day):
            continue
        maps = [index.get((entity_id, t, qtrs)) or {} for t in rung.tags]
        if all(period in m for m in maps):
            return rung.key, sum(float(m[period]) for m in maps)
    return None, None
# net_debt_ebitda
if out.ebitda is None: out.why["net_debt_ebitda"] = R_NO_EBITDA
elif out.total_debt is None: out.why["net_debt_ebitda"] = R_NO_TOTAL_DEBT_AT_P
elif out.cash is None: out.why["net_debt_ebitda"] = R_NO_CASH_AT_P          # OD-08 A
elif float(out.ebitda) <= 0.0: out.why["net_debt_ebitda"] = R_EBITDA_NON_POSITIVE  # OD-07 A
else: out.net_debt_ebitda = (out.total_debt - out.cash) / float(out.ebitda)
```

### B3  (B)  `pit_replay.py`

economics_changing=False; needs_owner_decision=OD-06

**What.** RAW_FIELD (941-949) and SOURCE_CONCEPTS (954-962) entries for the three keys; row loop source_period/source_tag (1424-1431) so net_debt_ebitda and debt_market_cap rows carry source_tag = prim.total_debt_rung and resolved codes dr (rung_index) / db (bound) via pit_policy.quantity_for (925-940); run_date summary tallies rows by bound per date (consumer rule 2a).

**Tests.** test_pit_replay.py:1265-1300 oracle: dr/db deterministic, new: a net_debt_ebitda row on a lower_bound rung carries db == 'lower_bound' and source_tag == the rung key

```
RAW_FIELD.update({"fcf_conversion": ("fcf_conversion", "fcf_conversion"),
                  "net_debt_ebitda": ("net_debt_ebitda", "net_debt_ebitda"),
                  "debt_market_cap": ("debt_market_cap", "debt_market_cap")})
SOURCE_CONCEPTS.update({"fcf_conversion": ("oi", "da", "ocf@P", "capex@P"),
                        "net_debt_ebitda": ("oi", "da", "debt@P", "cash@P"),
                        "debt_market_cap": ("debt", "px", "sh")})
q = pit_policy.quantity_for("total_debt", prim.total_debt_rung, LADDER_VERSION)
resolved["dr"], resolved["db"] = q["rung_index"], q["bound"]
```

### B4  (B)  `pit_normalization.py`

economics_changing=True; needs_owner_decision=OD-05

**What.** Under OD-05 A: three PERCENTILE_RANK entries appended to candidate_v2_registry() after InterestCoverageRank (before the return at 1951), anchor_source ANCHOR_COHORT_RANK, higher_is_better True / False / False, notes naming the domain policy body. DIGESTED BODY -> lands only with the Stage E cut. Under OD-05 B this step is replaced by a normalise() branch reading pit_factor_spec.spec(key, FACTOR_SPEC_VERSION).normalization_type and FACTOR_DIRECTION_V1.

**Tests.** test_pit_normalization.py:518-522 set -> add FCFConversionRank, NetDebtEBITDARank, DebtMarketCapRank; :562 len == 10, pit_replay.validate() 2003-2012 direction guard passes only with higher_is_better=False on the two leverage entries, pit_replay.py:306-315 REASON_WHY[R_NO_V2_NORMALIZATION] 'declares seven specs' rewritten

```
FactorSpec(
    feature_key="NetDebtEBITDARank",
    normalization_type=PERCENTILE_RANK,
    economic_question="how many years of earnings would clear the debt, versus peers?",
    anchor_source=ANCHOR_COHORT_RANK,
    higher_is_better=False,          # FACTOR_DIRECTION_V1: LOWER_IS_BETTER
    notes=("DOMAIN POLICY pit_factor_spec.LEVERAGE_DOMAIN_V1, executed upstream in "
           "pit_replay.resolve_primitives; total_debt rung on the row (pit_policy v2 consumer rule)",),
),
```

### B5  (B)  `pit_factor_spec.py`

economics_changing=True; needs_owner_decision=OD-06,OD-07,OD-08,OD-09,OD-10,OD-11

**What.** Domain bodies after INTEREST_COVERAGE_DOMAIN_V1 (1376-1395): LEVERAGE_DOMAIN_V1 (applies_to net_debt_ebitda; rung rule per OD-06; EBITDA <= 0 per OD-07; missing cash per OD-08; period per OD-09; negative net debt per OD-10; direction LOWER_IS_BETTER applied AFTER), FCF_DOMAIN_V1 (capex sign; EBITDA <= 0; period), MARKET_CAP_DOMAIN_V1 (strict class policy, market_cap > 0, price basis, split/traded flags per OD-11); FORMULA_GOLDEN_V1 (1398-1432) gains the B2 witnesses; pit_replay.validate() cross-checks each 'REFUSED:<code>' against the engine's constants like 1982-1992. DIGESTED (new COMPONENTS entries at the cut).

**Tests.** test_pit_factor_spec.py:645-653 pattern: add 'names every state' checks for the new bodies, test_pit_frozen_spec.py perturbation (204-205) must see the new bodies (add them to COMPONENTS in E3)

```
LEVERAGE_DOMAIN_V1: dict[str, Any] = {
    "policy_version": "leverage_domain_v1",
    "status": "<OWNER DECISION OD-06..OD-10, date>",
    "applies_to": ("net_debt_ebitda",),
    "ordinary": "ebitda > 0; net_debt = total_debt - cash, both <period rule OD-09>",
    "non_positive_ebitda": "REFUSED:<OD-07 code>",
    "missing_cash": "REFUSED:<OD-08 code>",
    "negative_net_debt": "<OD-10>",
    "debt_rung_rule": "<OD-06: all v2 rungs with bound on the row | DEBT_RUNG_EVEBITDA_V1 gate>",
    "direction": "LOWER_IS_BETTER applied AFTER this rule (pit_factor_blocks: orientation only)",
}
```

### B6  (B)  `pit_replay.py`

economics_changing=False; needs_owner_decision=OD-05,OD-25

**What.** Flip the three Q plans (515-534) to VERDICT_COMPUTABLE with norm_key (B4), needs_cohort=True, cohort_use=COHORT_RANK, refusal_reason None; keep R_NO_V2_NORMALIZATION in the vocabulary as history (OD-23) since ev_ebitda_supplement still uses it until Stage C.

**Tests.** test_pit_replay.py:158-161 -> 10 / 0 / 3, test_pit_replay.py:1089 -> 3; :1108-1111 -> only ev_ebitda_supplement == R_NO_V2_NORMALIZATION, test_pit_replay.py:841 Q mask spelling unchanged; add {'interest_coverage': 1.0, 'net_debt_ebitda': 1.0} -> 'FCF=0,ICOV=1,NDE=1,DMC=0', reachable stays 0.60 (Q already reachable)

```
"net_debt_ebitda": FactorPlan(
    key="net_debt_ebitda", block=_B.BLOCK_Q, role=_B.ROLE_SCORING,
    verdict=VERDICT_COMPUTABLE, norm_key="NetDebtEBITDARank",
    spec_key="net_debt_ebitda", needs_cohort=True,
    cohort_use=COHORT_RANK, mask_code="NDE",
    note="input (total_debt - cash) / ebitda under LEVERAGE_DOMAIN_V1; rung on the row"),
```

### B7  (B)  `pit_replay.py`

economics_changing=True; needs_owner_decision=OD-06

**What.** Under OD-06 B only: call pit_valuation_spec.debt_gate(prim.total_debt_rung) (2223-2240) before forming net_debt_ebitda / debt_market_cap and record its code verbatim; validate() asserts the v2 total_debt ladder's rung keys == set(DEBT_RUNG_EVEBITDA_V1) so a ladder edit cannot bypass the gate.

**Tests.** new: a LongTermDebtNoncurrent rung refuses with 'debt_rung_rejected_measured'; SUM2 scores

```
why = pit_valuation_spec.debt_gate(out.total_debt_rung)
if why:
    out.why["net_debt_ebitda"] = why      # 'debt_rung_rejected_measured' | 'debt_rung_undetermined'
# validate():
keys = {r.key for r in pit_policy.ladder_set(LADDER_VERSION)["total_debt"].rungs}
if keys != set(pit_valuation_spec.DEBT_RUNG_EVEBITDA_V1): problems.append(...)
```

### B8  (B)  `pit_replay.py`

economics_changing=False; needs_owner_decision=OD-11,OD-12,OD-18

**What.** debt_market_cap: in run_date (1188-1232) load listing per entity from pit_identity.scored_universe_as_of (multi-listing rule OD-18), resolve market cap for every priced entity in `needed` (targets AND cohort members, because CohortCache reads Primitives) through pit_rawprice.market_cap_as_of_detail(conn, entity_id, day, listing_id=..., class_policy=CLASS_POLICY_STRICT, require_traded=<OD-11>, require_split_check=<OD-11>), memoised per (entity, date); apply the value screen market_cap > 0 (eligible_peers does not, 2466-2472); assert the GAP 7 basis via pit_price_basis.assert_price_basis(PRICE_ROLE_VALUATION, PRICE_RAW_AS_TRADED); write listing_id on the feature row (1440 `entity_id, None, day, key, plan.role,`) and score row (1525) for price-dependent keys; seed pit_listing in seed_parents (675-727, FK at pit_store.py:469).

**Tests.** golden: debt 1000 / market_cap 5000 -> 0.2 (witness needs a synthetic market cap path; otherwise engine-local), test_pit_replay.py:1165-1180 slice: seed_parents['pit_listing_rows'] > 0; KNOWN_LIMITATIONS listing sentence (567-569) removed, pit_replay_rss: fixed cost grows with per-entity share audits -- measure before any pilot

```
mc = pit_rawprice.market_cap_as_of_detail(main_conn_ro, entity_id, day, listing_id=listing_id,
        class_policy=pit_rawprice.CLASS_POLICY_STRICT,
        require_traded=MARKET_CAP_REQUIRE_TRADED, require_split_check=MARKET_CAP_REQUIRE_SPLIT_CHECK)
if mc["market_cap"] is None: out.why["debt_market_cap"] = mc["reason"]
elif mc["market_cap"] <= 0.0: out.why["debt_market_cap"] = R_MARKET_CAP_NON_POSITIVE
elif out.total_debt is None: out.why["debt_market_cap"] = R_NO_TOTAL_DEBT_AT_P
else: out.market_cap, out.debt_market_cap = mc["market_cap"], out.total_debt / mc["market_cap"]
```

### B9  (B)  `pit_price_basis.py`

economics_changing=True; needs_owner_decision=OD-11

**What.** GAP 7 declared once beside PRICE_ROLE_REQUIRED_BASIS (171-174) per OD-11 A, consumed by the engine's single valuation-price resolver (B8, C2, D2); added to COMPONENTS at the cut (E3). Under OD-11 B this becomes PRICED_EPS_V3 / EV_EBITDA_SIX_V2 / DEBT_AND_MARKET_CAP_V2 in pit_factor_spec with SPECS_V5.

**Tests.** test_pit_price_basis.py: add check(SPEC_PRIMITIVE_PRICE_BASIS_V1['price_adjusted'] == PRICE_RAW_AS_TRADED), test_pit_valuation_spec.py:169-172 unchanged (reads the v1 spec)

```
#: GAP 7 -- the spec vocabulary's 'price_adjusted' is satisfied ONLY by the
#: valuation role's required basis. Digested (spec_freeze_v6).
SPEC_PRIMITIVE_PRICE_BASIS_V1: dict[str, str] = {
    "price_adjusted": PRICE_ROLE_REQUIRED_BASIS[PRICE_ROLE_VALUATION],
}
```

### B10  (B)  `pit_replay.py`

economics_changing=False; needs_owner_decision=-

**What.** validate() (1921-2057): extend the index-tag guard (2014-2021) to the cash, total_debt (v2), operating_cash_flow, capex and shares_outstanding ladders; add the domain-code cross-checks for the B5 bodies; KNOWN_LIMITATIONS (572-582) and STILL_BLOCKED ('lacks a registered transform for fcf_conversion, net_debt_ebitda, debt_market_cap and EV/EBITDA', pit_frozen_spec.py:747-750) rewritten; pit_coverage.py:809-811 census note if OD-09 changes the period rule.

**Tests.** test_pit_replay.py:135-136 validate() clean

```
for concept in ("cash", "total_debt", "operating_cash_flow", "capex", "shares_outstanding"):
    for rung in ladders[concept].rungs:
        for tag in rung.tags:
            if tag not in census:
                problems.append("the fact index does not load %s, which %s reads" % (tag, concept))
```

### C1  (C)  `pit_normalization.py`

economics_changing=True; needs_owner_decision=OD-13,OD-05

**What.** EV transform per OD-13 and OD-05: either a registry entry EVEBITDASupplement (BENCHMARK_ANCHORED with a declared ScaleSpec in multiple units and an anchor_source the registry validator accepts -- ANCHOR_COHORT_MEDIAN today lives in pit_valuation_spec, not in pit_normalization's vocabulary; or PERCENTILE_RANK higher_is_better=False), or a V-specific scorer using pit_valuation_spec.valuation_score_detail(multiple, anchor) with SUBFACTOR_DIRECTION_V1['ev_ebitda_supplement'] relabelled EMBEDDED_IN_TRANSFORM (pit_factor_blocks.py:676, digested). DIGESTED either way -> cut.

**Tests.** test_pit_normalization.py:518-522, 562 (11 if a registry entry), test_pit_frozen_spec.py:204-205 perturbation covers the new body

```
# OD-13 option 'tanh' shape:
FactorSpec(feature_key="EVEBITDASupplement", normalization_type=BENCHMARK_ANCHORED,
           anchor_source=<median anchor constant>, higher_is_better=False,
           scale=cohort_scale(multiplier=1.0, fallback_fixed=<OD-13 k>), ...)
# OD-13 option 'convex' shape (engine side):
score = pit_valuation_spec.valuation_score_detail(prim.ev_ebitda, anchor)["score"]
```

### C2  (C)  `pit_replay.py`

economics_changing=True; needs_owner_decision=OD-13,OD-14,OD-08,OD-09

**What.** Primitives enterprise_value / ev_ebitda from B's market_cap, total_debt (rung), cash: pit_valuation_spec.debt_gate BEFORE the multiple (rung walk per OD-14), then the value screens ebitda > 0, enterprise_value > 0 and the [0.1, 300] band (EV_EBITDA_SIX 497-498) as target-side checks per OD-13, each a named refusal; RAW_FIELD / SOURCE_CONCEPTS entries.

**Tests.** golden (if a witness is added): mc 5000 + debt 1000 - cash 200 = 5800 / EBITDA 400 -> 14.5, new: LongTermDebtNoncurrent rung -> 'debt_rung_rejected_measured'; None rung -> 'debt_rung_undetermined'

```
why = pit_valuation_spec.debt_gate(out.total_debt_rung)
if why: out.why["ev_ebitda_supplement"] = why
elif out.market_cap is None or out.cash is None: ...
elif float(out.ebitda) <= 0.0: out.why[...] = R_EBITDA_NON_POSITIVE
else:
    ev = out.market_cap + out.total_debt - out.cash
    if ev <= 0.0: out.why[...] = R_EV_NON_POSITIVE
    else:
        m = ev / float(out.ebitda)
        if not (0.1 <= m <= 300.0): out.why[...] = R_EV_EBITDA_OUTSIDE_BAND   # OD-13
        else: out.enterprise_value, out.ev_ebitda = ev, m
```

### C3  (C)  `pit_replay.py`

economics_changing=True; needs_owner_decision=OD-13,OD-19,OD-21

**What.** Cohort for the anchor: CohortCache.get with spec_key 'ev_ebitda' (rung ceiling sic2 -> reason VALUATION_PEER_SET_INSUFFICIENT from pit_factor_spec.refusal_reason 2222-2224; the engine must refuse R_NO_PEER_SET before calling eligible_peers when cohort is None because 2400-2408 RAISES on rung None); per-member ev_ebitda from Primitives; a fourth cohort mode for a median anchor (target in/out per OD-13) with the anchor written as bm/as codes like the benchmark; peer floor per OD-04/OD-19.

**Tests.** new: sic2-ceiling refusal carries 'VALUATION_PEER_SET_INSUFFICIENT' on the ev row, test_pit_replay.py:838-839 MASK_NONE still the empty-V spelling

```
COHORT_MEDIAN_ANCHOR = "median_anchor_<includes|excludes>_target"
if plan.cohort_use == COHORT_MEDIAN_ANCHOR:
    peers = [v for e, v in cohort_values.items() if <OD-13 policy>]
    anchor = statlib.median(peers)
```

### C4  (C)  `pit_replay.py`

economics_changing=True; needs_owner_decision=OD-20

**What.** V assembly per OD-20: either keep block_score (1454-1462) plus an explicit depends_on guard (pe_relative unavailable when pe_absolute is), or build three SubfactorResult objects, call pit_valuation_spec.valuation_pillar, feed for_score_signature()['factor_score'] into block_scores[BLOCK_V] and pillar.reason into score_v2 (pit_factor_blocks.assemble 339-386 gains a reasons kwarg); fill the five columns written None at 1546 (valuation_presence_state, valuation_data_quality NULL under QUALITY_RULE_UNDECIDED, valuation_quality_rule, valuation_subfactors_json, earnings_discontinuity_state).

**Tests.** new: pe_relative alone -> V ABSENT under the pillar path (probe: block_score alone would give 10.0), test_pit_replay.py:511-530 arithmetic on fixed inputs unchanged

```
pillar = pit_valuation_spec.valuation_pillar(v_results)
block_scores[pit_factor_blocks.BLOCK_V] = pillar.for_score_signature()["factor_score"]
...
pillar.presence.state, None, None,
json.dumps(pillar.as_dict()["subfactors"], separators=(",", ":")),
discontinuity_state,
```

### C5  (C)  `pit_replay.py`

economics_changing=False; needs_owner_decision=OD-13,OD-25

**What.** Flip FACTOR_PLAN['ev_ebitda_supplement'] (494-502) to COMPUTABLE with needs_cohort=True; R_NO_V2_NORMALIZATION now emitted by no live plan (kept in vocabulary per OD-23); KNOWN_LIMITATIONS / STILL_BLOCKED / docstring updated.

**Tests.** test_pit_replay.py:158-161 -> 11 / 0 / 2, test_pit_replay.py:221-226 reachable 0.60 -> 0.85 (V's 0.25 becomes reachable), test_pit_replay.py:1089 -> 2; :1108-1111 retired

```
"ev_ebitda_supplement": FactorPlan(
    key="ev_ebitda_supplement", block=_B.BLOCK_V, role=_B.ROLE_SCORING,
    verdict=VERDICT_COMPUTABLE, norm_key=<C1>, spec_key="ev_ebitda",
    needs_cohort=True, cohort_use=COHORT_MEDIAN_ANCHOR, mask_code="EVEBITDA"),
```

### D1  (D)  `pit_replay.py`

economics_changing=False; needs_owner_decision=OD-16,OD-21

**What.** Per-run TTM loader: pit_eps_cohort.load_ttm(main_conn_ro) once (pit_eps_cohort.py:181-212) and EntityTtm.select(day) per entity (131-178; mirrors ttm_eps_as_of_detail incl. the 15-month staleness), recording concept_key, period_end, available_date, method, sign_case, pe_leg on Primitives; negative TTM is available with pe_leg 'unavailable_loss_making' (pit_eps.py:1252) -> pe_absolute UNAVAILABLE with that reason verbatim.

**Tests.** new: a synthetic EntityTtm with a negative TTM yields pe_leg 'unavailable_loss_making' and no multiple

```
ttm_table = pit_eps_cohort.load_ttm(main_conn_ro)     # once per run
picked = ttm_table[entity_id].select(day) if entity_id in ttm_table else None
out.ttm_eps, out.pe_leg, out.eps_period_end = (picked["ttm_eps"], picked["pe_leg"], picked["period_end"]) if picked else (None, None, None)
```

### D2  (D)  `pit_replay.py`

economics_changing=True; needs_owner_decision=OD-18,OD-11

**What.** Raw price per listing via pit_rawprice.raw_close_as_of(conn, listing_id, day, roll_back_days=<OD-18>, require_traded=<OD-18>) (pit_rawprice.py:610); basis asserted through the GAP 7 constant (B9) and pit_price_basis.assert_price_basis(PRICE_ROLE_VALUATION, basis) (207-215); refusal reasons recorded verbatim (no_bar_on_date, unusable ratio, ...).

**Tests.** new: an adjusted basis handed to the resolver raises PriceBasisError (TypeError)

```
px = pit_rawprice.raw_close_as_of(main_conn_ro, listing_id, day,
                                  roll_back_days=VALUATION_PRICE_ROLL_BACK_DAYS,
                                  require_traded=VALUATION_PRICE_REQUIRE_TRADED)
if not px["available"]: out.why["pe_absolute"] = px["reason"]
else: out.price_raw = px["raw_close"]   # price_basis 'raw_as_printed_unadjusted'
```

### D3  (D)  `pit_price_basis.py`

economics_changing=True; needs_owner_decision=OD-17

**What.** P/E on the score-date basis through normalised_pe(conn, price_raw=, price_basis=PRICE_RAW_AS_TRADED, eps_pit=, eps_period_end=, as_of=, listing_id=) (676-713). If OD-17 changes the gate: pit_safe_split_factor (409-453) decides GATE_NO_EVENTS on a listing-level COUNT(*) (pit_shares.py:1224-1227 pattern) and/or the window start moves to the vintage's available date; ACTION_GATE_VERSION's body is digested -> v6; test_pit_price_basis.py:176-189 gains an empty-window case.

**Tests.** test_pit_price_basis.py:186 unchanged under OD-17 A; new empty-window test under B/C

```
npe = pit_price_basis.normalised_pe(main_conn_ro, price_raw=out.price_raw,
        price_basis=pit_price_basis.PRICE_RAW_AS_TRADED, eps_pit=out.ttm_eps,
        eps_period_end=out.eps_period_end, as_of=day, listing_id=out.listing_id)
if npe.pe is None: out.why["pe_absolute"] = npe.reason   # e.g. REFUSED_SHARE_BASIS_UNKNOWN
else: out.pe = npe.pe; resolved codes bf (basis_factor), gs (gate.status)
```

### D4  (D)  `pit_replay.py`

economics_changing=True; needs_owner_decision=OD-05,OD-16

**What.** pe_absolute score = pit_valuation_spec.valuation_score_detail(prim.pe, PE_ABSOLUTE_ANCHOR)['score'] (934-985; refuses m <= 0 with pit_eps.PE_UNAVAILABLE_LOSS); near-zero-positive EPS per OD-16; direction EMBEDDED (pit_factor_blocks.py:674) so no orientation step; wiring shape per OD-05 (registry entry with a new type would move NORMALIZATION_TYPES, or validate() 1956-1962 amended to allow norm_key None for V with a V scorer).

**Tests.** expected (PINNED_CURVE, probe): pe 100 -> -53.99049042384845; pe 5 -> +31.256122562826963; pe 3000 -> -100.0, test_pit_replay.py:1956-1971 validate rule per OD-05

```
detail = pit_valuation_spec.valuation_score_detail(prim.pe, pit_valuation_spec.PE_ABSOLUTE_ANCHOR)
score, reason = detail["score"], detail["reason"]
resolved["u"], resolved["br"] = detail["u"], detail["branch"]
```

### D5  (D)  `pit_replay.py`

economics_changing=True; needs_owner_decision=OD-15,OD-16,OD-19,OD-21,OD-04

**What.** pe_relative: cohort via CohortCache with spec_key 'pe_ratio' (PRICED_EPS_V2: requires_price through _priced_and_defensible 2316-2341; sic2 ceiling; VALUATION_PEER_SET_INSUFFICIENT), member P/Es from Primitives (near-zero members per OD-16, mixed methods per OD-21), anchor = median per OD-15 (target in/out), transform per OD-15 (convex at the median: probe 100 vs 28 -> -37.33175842435203), depends_on guard (pe_absolute unavailable -> pe_relative unavailable with a named reason).

**Tests.** new: pe_relative refused when pe_absolute is (presence rule), new: sic2-ceiling refusal on the pe_relative row

```
if prim.pe is None:
    reason = R_PE_RELATIVE_NEEDS_ABSOLUTE      # depends_on, pit_valuation_spec.py:641
else:
    record = cohort_cache.get(peer_set_id, rung, members, "pe_relative")
    anchor = statlib.median([v for e, v in record["values"].items() if <OD-15 policy>])
    score = pit_valuation_spec.valuation_score_detail(prim.pe, anchor)["score"]   # OD-15 T1
```

### D6  (D)  `pit_replay.py`

economics_changing=True; needs_owner_decision=OD-20

**What.** earnings_discontinuity_state on the score row (last SCORE_COLUMNS entry, 1076-1090; value at 1546) per OD-20: from the TTM row's sign_crossing / pit_valuation_spec.eps_transition, or only via eps_transition_from_pair on a basis-resolved pair (NULL when unresolved).

**Tests.** new: a synthetic TTM with sign_crossing=1 writes 'EARNINGS_SIGN_CROSS'; else NULL

```
discontinuity = (pit_valuation_spec.EARNINGS_SIGN_CROSS
                 if picked and picked.get("sign_crossing") else None)   # OD-20 option A
```

### D7  (D)  `pit_replay.py`

economics_changing=False; needs_owner_decision=OD-25

**What.** Flip pe_absolute / pe_relative plans (486-493) to COMPUTABLE (pe_relative needs_cohort=True); R_PE_CHAIN_NOT_BUILT kept as history (OD-23; REASON_WHY 324-330 rewritten); KNOWN_LIMITATIONS listing sentence (567-569) removed and 'Six/Seven of thirteen' paragraph deleted; module docstring 33-73 rewritten; STILL_BLOCKED and MODEL-LINEAGE updated.

**Tests.** test_pit_replay.py:158-161 -> 13 / 0 / 0, test_pit_replay.py:1080-1113 rewritten: no plan is refused, so the refused-row block asserts an empty `refused` list and the vocabulary check moves to a synthetic-refusal fixture, test_pit_replay.py:1104-1107 retired; :246-248 still >= 6 (entries after deletions: 6)

```
"pe_absolute": FactorPlan(key="pe_absolute", block=_B.BLOCK_V, role=_B.ROLE_SCORING,
    verdict=VERDICT_COMPUTABLE, norm_key=<OD-05>, spec_key="pe_ratio", needs_cohort=False, mask_code="PE"),
```

### E1  (E)  `pit_frozen_spec.py`

economics_changing=False; needs_owner_decision=OD-25

**What.** Seal v5: add FREEZE_V5 after FREEZE_V4 (232-300) in FREEZE_V4's field shape -- digest '58722f4c00b676fee76bcc10ba341f44ce18c540a2fbc90625570a51786a5b59', n_components 61, status STATUS_SUPERSEDED, a new REASON_* constant (e.g. REASON_ENGINE_LACKED_VQ_PATHS) beside 89/91, what_it_fixed, content_defects_found_under_it (the benchmark band/floor discrepancy, the three-way pe_relative declaration, GAP 5/7 open), rows_it_ever_produced_in_main_store 0, may_execute_replay False, superseded_by 'spec_freeze_v6', never_repair_in_place; add 'FREEZE_V5' and the reason to __all__ (60-75).

**Tests.** test_pit_frozen_spec.py:101-106 chain -> add FREEZE_V5['superseded_by'] == 'spec_freeze_v6' and SPEC_FREEZE_VERSION == 'spec_freeze_v6', test_pit_frozen_spec.py:141-144 -> 'six freezes, six distinct digests', add a V5-verbatim pin (digest 58722f4c..., 61 components)

```
FREEZE_V5: dict[str, Any] = {
    "freeze_version": "spec_freeze_v5",
    "model_version": "equity_shaffer_v2_pit_SURVIVOR_ONLY_DIAGNOSTIC",
    "frozen_at": "2026-09-22",
    "digest": "58722f4c00b676fee76bcc10ba341f44ce18c540a2fbc90625570a51786a5b59",
    "n_components": 61,
    "status": STATUS_SUPERSEDED,
    "status_reason": REASON_ENGINE_LACKED_VQ_PATHS,
    "rows_it_ever_produced_in_main_store": 0,
    "may_execute_replay": False,
    "superseded_by": "spec_freeze_v6",
    "never_repair_in_place": "Its digest stays 58722f4c...; the V/Q bodies arrive as spec_freeze_v6 beside it.",
}
```

### E2  (E)  `pit_frozen_spec.py`

economics_changing=False; needs_owner_decision=OD-11,OD-04

**What.** Live constants: SPEC_FREEZE_VERSION (303) -> 'spec_freeze_v6'; FROZEN_AT (309) -> the cut date; ENGINE_MUST_DERIVE_FROM (317) moves to 'factor_spec_v5' ONLY if SPECS_V5 exists (OD-11 B / OD-04 C), and then pit_replay.FACTOR_SPEC_VERSION (pit_replay.py:193) moves in the same commit (validate 1974-1978 and require_gate 644-649 refuse otherwise); FROZEN_MODEL_VERSION (307) keeps SURVIVOR_ONLY_DIAGNOSTIC in the name.

**Tests.** test_pit_frozen_spec.py:592-601 engine-derives and live-name checks, test_pit_replay.py:171-172 only if the spec version moves, test_pit_factor_spec.py:645-653 only if SPECS_V5 exists (spec_versions()[-1], 11-by-reference count)

```
SPEC_FREEZE_VERSION = "spec_freeze_v6"
FROZEN_AT = "<cut date>"
ENGINE_MUST_DERIVE_FROM = "factor_spec_v4"   # or v5 only with SPECS_V5
```

### E3  (E)  `pit_frozen_spec.py`

economics_changing=False; needs_owner_decision=OD-25

**What.** COMPONENTS (355-439): append, never remove -- the new domain bodies (B5), PEER_FLOOR_V1 if OD-04 digests it, SPEC_PRIMITIVE_PRICE_BASIS_V1 (B9), any V transform/scale body (C1), FACTOR_SPEC_VERSION_V5 / SPECS_V5 if OD-11 B; the registry, FORMULA_GOLDEN_V1, SUBFACTOR_DIRECTION_V1 and ACTION_GATE_VERSION are already digested and move by body. Fix the freeze count text in the v5 block comment.

**Tests.** test_pit_frozen_spec.py:66-71 -> new component count, test_pit_frozen_spec.py:204-205 perturbation covers every new key

```
    # ---- v6: the V/Q domain bodies, the peer floor and the GAP 7 translation
    ("pit_factor_spec", "LEVERAGE_DOMAIN_V1", "net_debt_ebitda domain policy"),
    ("pit_factor_spec", "FCF_DOMAIN_V1", "fcf_conversion domain policy"),
    ("pit_factor_spec", "MARKET_CAP_DOMAIN_V1", "debt_market_cap / EV market-cap policy"),
    ("pit_price_basis", "SPEC_PRIMITIVE_PRICE_BASIS_V1", "spec primitive -> price basis (GAP 7)"),
```

### E4  (E)  `pit_frozen_spec.py`

economics_changing=False; needs_owner_decision=-

**What.** Record the manifest: set FROZEN_DIGEST = 'PENDING' (502) and _FROZEN_MANIFEST = {} (548); in a FRESH `.venv\Scripts\python.exe -B` interpreter call pit_frozen_spec.manifest() ONCE, paste its rendering into _FROZEN_MANIFEST and digest(_FROZEN_MANIFEST) into FROZEN_DIGEST; validate() 1087-1098 then proves the record and digest came from one manifest and the keys equal COMPONENTS.

**Tests.** test_pit_frozen_spec.py:164-166 len(_FROZEN_MANIFEST) == len(COMPONENTS) and digest(_FROZEN_MANIFEST) == FROZEN_DIGEST, test_pit_frozen_spec.py:66-69 intact and recorded

```
FROZEN_DIGEST = "PENDING"    # step 1; verify() tolerates it (516)
# step 2, fresh interpreter:
#   import pit_frozen_spec as F; man = F.manifest(); print(F.digest(man)); print(man)
FROZEN_DIGEST = "<sha256 of the recorded manifest>"
_FROZEN_MANIFEST = { ... rendered values, sorted ... }
```

### E5  (E)  `pit_frozen_spec.py`

economics_changing=False; needs_owner_decision=OD-24

**What.** validate() extensions (1148-1224): FREEZE_V5 digest / n_components 61 / status / rows 0 / may_execute False / superseded_by 'spec_freeze_v6' pins; the live-name pin (1166-1170) over (V1..V5); 'six distinct digests' (1171-1173); the by-body needs list (1180-1193) extended with the new keys; content pins for the new bodies (e.g. LEVERAGE_DOMAIN_V1['direction'] mentions LOWER_IS_BETTER; every REFUSED: code is an engine reason); OD-24 status handling for the six pending items (862-880); STILL_BLOCKED text (741-754) rewritten to the post-wiring state.

**Tests.** test_pit_frozen_spec.py:726 validate() reports 0 problems, test_pit_frozen_spec.py:585-591 pending-v5 pins per OD-24

```
if FREEZE_V5["digest"] != "58722f4c00b676fee76bcc10ba341f44ce18c540a2fbc90625570a51786a5b59":
    problems.append("FREEZE_V5's digest was edited; a frozen record is never repaired in place")
if FREEZE_V5["n_components"] != 61: problems.append("FREEZE_V5 had 61 components")
if len({FREEZE_V1["digest"], FREEZE_V2["digest"], FREEZE_V3["digest"], FREEZE_V4["digest"], FREEZE_V5["digest"], digest()}) != 6:
    problems.append("the six freezes must have six distinct digests")
```

### E6  (E)  `pit_replay_manifest.py`

economics_changing=False; needs_owner_decision=-

**What.** validate() chain tuple at 1278 stops at FREEZE_V4 while freeze_state (305-306) already enumerates FREEZE_V5; add 'FREEZE_V5' so the 'last sealed record must name the LIVE freeze' check (1284) passes after the cut.

**Tests.** test_pit_replay_manifest.py:570-574 -> len(lineage) == 6; add a lineage[4] block (spec_freeze_v5, STATUS_SUPERSEDED, rows 0, superseded_by 'spec_freeze_v6'); :591 lineage[3]['superseded_by'] == 'spec_freeze_v5' unchanged

```
chain = [getattr(pit_frozen_spec, n)
         for n in ("FREEZE_V1", "FREEZE_V2", "FREEZE_V3", "FREEZE_V4", "FREEZE_V5")
         if hasattr(pit_frozen_spec, n)]
```

### E7  (E)  `pit_factor_spec.py`

economics_changing=True; needs_owner_decision=OD-22,OD-07

**What.** Golden witnesses for the cut: FORMULA_GOLDEN_V1 (1398-1432) gains the benchmark witness (12-pair fixture -> M_s 0.17, n_band 3) and the B2 Q witnesses; pit_replay._golden_eval (1897-1918) extended in the SAME commit to synthesise instants/durations and a benchmark pair vector, or validate() reports 'engine gives None'.

**Tests.** test_pit_replay.py:197-206 golden perturbation still names a disagreeing witness, pit_replay.validate() 2022-2032 passes on every witness

```
{"factor": "ebitda_benchmark", "pairs": tuple((100.0 * i, round(0.09 + 0.01 * i, 4)) for i in range(1, 13)),
 "expect_anchor": 0.17, "expect_band": 3},
{"factor": "net_debt_ebitda", "total_debt": 1000.0, "cash": 200.0, "ebitda": 400.0, "expect": 2.0},
{"factor": "fcf_conversion", "operating_cash_flow": 500.0, "capex": 200.0, "ebitda": 400.0, "expect": 0.75},
{"factor": "net_debt_ebitda", "total_debt": 1000.0, "cash": 200.0, "ebitda": 0.0, "expect": "REFUSED:<OD-07 code>"},
```

### E8  (E)  `MODEL-LINEAGE.md`

economics_changing=False; needs_owner_decision=-

**What.** Docs: a spec_freeze_v6 section recording every OD-* decision verbatim with date, the FREEZE_V5 seal, the census progression (6/1/6 -> 7/0/6 -> 10/0/3 -> 11/0/2 -> 13/0/0), the reachable weight 0.60 -> 0.85, and the surviving limitations (survivorship in the model version string; lower_bound debt rungs; no replay authorisation). Update the 'Executability' paragraph at 1290-1302 and docs/POST-REBOOT-CHECKLIST.md 'a change is a v5' wording.

**Tests.** none (prose); STILL_BLOCKED is undigested but is the owner-facing status

```
## spec_freeze_v6 -- V/Q wiring
Census 13 / 0 / 0 under pit_replay/1.x; reachable weight E+V+G+Q = 0.85. Owner decisions OD-01..OD-25 recorded above. No replay authorisation exists.
```

### E9  (E)  `pit_frozen_spec.py`

economics_changing=False; needs_owner_decision=-

**What.** Verification sequence for the cut commit (no pilot, no replay): pit_frozen_spec.main() -> 0 problems; `python -B pit_replay.py --validate` -> []; the ten suites (test_pit_replay, test_pit_valuation_spec, test_pit_price_basis, test_pit_eps, test_pit_prices, test_pit_rawprice, test_pit_shares, test_pit_factor_spec, test_pit_normalization, test_pit_frozen_spec) plus test_pit_replay_manifest; then test_pit_replay --slow (the 50-target streaming oracle 1265-1300 and the slice 1165-1180) on a disposable pilot DB only, confirming 'the freeze is unchanged by the run'; pit_replay_rss before any sizing pilot because Primitives grew and per-member share audits entered CohortCache.

**Tests.** all suites green; freeze intact; oracle rows identical across arms

```
python -B pit_frozen_spec.py && python -B pit_replay.py --validate && for t in test_pit_*.py; do python -B $t || exit 1; done
```


---

# Verification notes: citations

Verdicts: {'CONFIRMED': 61, 'REFUTED': 4}

- **REFUTED A8** — Anchors confirmed: :282-305 REASON_WHY[R_BENCHMARK_AMBIGUOUS]; :572-582 "Seven of thirteen feature keys are UNAVAILABLE by construction on every row"; :64 "ONE is REFUSED: `ebitda_benchmark`."; :169-171 `ENGINE_VERSION = "pit_replay/1.1"`; pit_frozen_spec.py:741-754 STILL_BLOCKED (not in COMPONENTS); docs/MODEL-LINEAGE.md:1298 "Census 6 / 1 / 6". REFUTED count: KNOWN_LIMITATIONS (pit_replay.py:554-588) has SEVEN entries, not eight (probe len 7: feature_available_date / source_tag_changed / final_score / listing_id / fact_max_age_days / 'Seven of thirteen' / peer-set model_version). '8 -> 8 entries, one rewritten' should read '7 -> 7'; test :247 `>= 6` still holds.
- **REFUTED D4** — Transform anchors confirmed: pit_valuation_spec.py:934-982 valuation_score_detail, :958-959 `if not math.isfinite(m) or m <= 0: record["reason"] = pit_eps.PE_UNAVAILABLE_LOSS if m <= 0 else "not_finite"`, :712 `PE_ABSOLUTE_ANCHOR_V1 = 20.0`; pit_factor_blocks.py:674 `"pe_absolute": EMBEDDED_IN_TRANSFORM,`; probe pe 100 -> -53.99049042384845, 5 -> 31.256122562826963, 3000 -> -100.0 exactly. REFUTED anchor: 'test_pit_replay.py:1956-1971 validate rule' -- test_pit_replay.py has 1380 lines; the rule is pit_replay.py:1956-1971 (`if plan.norm_key not in _REGISTRY:`).
- **REFUTED D7** — Anchors confirmed: :486-493 pe plans `refusal_reason=R_PE_CHAIN_NOT_BUILT`; :324-330 REASON_WHY; :567-569 listing sentence; :33-73 docstring; test :1080-1113, :1104-1107. REFUTED: 'entries after deletions: 6' -- KNOWN_LIMITATIONS has 7 entries today (probe), so removing the listing sentence AND the 'Seven of thirteen' paragraph leaves 5, and test_pit_replay.py:246-248 `len(back["known_limitations"]) >= 6` would FAIL unless re-pinned (or one entry is rewritten rather than deleted).
- **REFUTED E1** — FREEZE_V4 at :232-300 confirmed (`"digest": "912268b3..."`, `"n_components": 47`, `"superseded_by"` -> spec_freeze_v5 at :164 validate); FROZEN_DIGEST :502 = "58722f4c00b676fee76bcc10ba341f44ce18c540a2fbc90625570a51786a5b59"; 61 COMPONENTS (count of tuples 355-439 = 61; test :71); :172 `STATUS_SUPERSEDED = "FROZEN_SUPERSEDED"`. REFUTED anchors: `__all__` is at :67-81, not 60-75 (59-65 are imports); the REASON_* constants are at :173 `REASON_DIGEST_COVERED_VERSION_STRINGS = "DIGEST_COVERED_VERSION_STRINGS_NOT_BODIES"` and :220 `REASON_FROZE_UNRESOLVED_SEMANTICS = "FROZE_UNRESOLVED_SEMANTIC_CHOICES_D3"`, not 'beside 89/91' (those lines are the lineage comment block).

Missing / additions:

- A8 / §8 / D7: an eighth KNOWN_LIMITATIONS entry does not exist -- pit_replay.py:554-588 holds SEVEN strings (probe len 7); '8 -> 8', '8 entries -> 7' and 'entries after deletions: 6' are all wrong.
- D7: after deleting the listing sentence (:567-569) and the 'Seven of thirteen' paragraph (:572-582), 7 - 2 = 5 entries remain, so test_pit_replay.py:247 `len(back["known_limitations"]) >= 6` breaks; the plan lists no re-pin for it.
- D4: 'test_pit_replay.py:1956-1971' does not exist (the test file is 1380 lines); the validate rule is pit_replay.py:1956-1971.
- E1: no REASON_* constant sits at pit_frozen_spec.py:89/91 (comment block); REASON_DIGEST_COVERED_VERSION_STRINGS is :173 and REASON_FROZE_UNRESOLVED_SEMANTICS is :220; `__all__` is :67-81, not :60-75.
- OD-09 / plan §3: OPERATING_INCOME_AND_INTEREST_MATCHED_V2 is pit_factor_spec.py:606-624, not :591-612 (591-593 is NET_INCOME_AND_EQUITY's why).
- OD-06: 'coverage ~38% of base' on total_debt rungs has no file:line anywhere in the plan and is not in the cited modules (only the 94% lower-bound share is, pit_factor_spec.py:1029-1030); a store measurement, unverifiable read-only.
- OD-07 / §3: '~37.8% of the universe with EBITDA <= 0' is uncited in OD-07; it is at docs/MODEL-LINEAGE.md:1289 ('37.8% EBITDA ≤ 0 is the FY2014 n=2,756 valuation-leg measurement').
- B8: 'pit_replay_rss: fixed cost grows with per-entity share audits' carries no anchor and was not verified.
- A7: 'interest_coverage rows that today read cohort_too_small_to_rank' is a pilot-DB fact; only the constant (pit_normalization.py:190) and the audit tally (docs:187 '93 / 62') are verifiable without opening a store.
- OD-02: 'the audit R2 text ... (docs:230)' -- docs:230 is numbered recommendation 2 (ebitda_benchmark MARGIN reading); 'R2' is docs:244 (which declaration governs V/Q normalisation). Content at :230 is as quoted.
- OD-24: the plan never says which dict holds 'owner_confirmation_pending_v5' (pit_frozen_spec.py:862-880); it is a key of SCORE_ASSEMBLY_GAP (:827), read as F.SCORE_ASSEMBLY_GAP at test_pit_frozen_spec.py:585, not of STILL_BLOCKED.
- OD-25: docs/POST-REBOOT-CHECKLIST.md 'a change is a v5' is line 74, not 72 (72 is blank, 71 is '## Never').
- D3: pit_safe_split_factor is defined at pit_price_basis.py:387; the cited 409-453 is only its window query through the returns.
- B5: the 'names every state' check pattern is test_pit_factor_spec.py:654, one line past the cited 645-653.

# Citation check -- corrections (HEAD 609cefe, read-only; one python probe, no store opened)

Verdict counts: 54 CONFIRMED, 5 REFUTED (A8, D4, D7, E1 on anchors/counts; none UNVERIFIABLE as a whole). Every REFUTED item is a wrong count or a wrong line/file, not a wrong mechanism.

## Substantive corrections (change what the plan asserts)

1. **KNOWN_LIMITATIONS has 7 entries, not 8.** `pit_replay.py:554-588` (probe `len(R.KNOWN_LIMITATIONS)` = 7): feature_available_date / source_tag_changed / final_score NULL / listing_id NULL / fact_max_age_days / \"Seven of thirteen ...\" / peer-set model_version.
   - A8 tests: \"(8 -> 8 entries, one rewritten)\" -> **7 -> 7, one rewritten**; `test_pit_replay.py:246-248` `>= 6` still holds.
   - §8 ledger: \"8 entries -> 7 after rewrites\" -> **7 -> 7**.
   - **D7 tests: \"still >= 6 (entries after deletions: 6)\" is wrong.** Deleting the listing sentence (:567-569) AND the \"Seven of thirteen\" paragraph (:572-582) leaves **5**, so `test_pit_replay.py:247` `len(back[\"known_limitations\"]) >= 6` FAILS. Either re-pin to `>= 5` or rewrite one of the two entries instead of deleting it. Add this re-pin to the Stage D ledger.

2. **D4 tests cite the wrong file.** \"test_pit_replay.py:1956-1971 validate rule per OD-05\" -> **`pit_replay.py:1956-1971`** (`if plan.norm_key not in _REGISTRY:` ...). test_pit_replay.py has 1380 lines.

3. **E1 anchors.** `__all__` is `pit_frozen_spec.py:67-81` (not 60-75; 59-65 are imports). The REASON_* constants are not \"beside 89/91\" (comment block); they are `:173 REASON_DIGEST_COVERED_VERSION_STRINGS = \"DIGEST_COVERED_VERSION_STRINGS_NOT_BODIES\"` and `:220 REASON_FROZE_UNRESOLVED_SEMANTICS = \"FROZE_UNRESOLVED_SEMANTIC_CHOICES_D3\"`; `STATUS_SUPERSEDED = \"FROZEN_SUPERSEDED\"` is `:172`. Place the new constant beside :220.

## Line-number corrections (content confirmed, anchor off)

| where | plan cites | actual |
|---|---|---|
| OD-09, §3 | `pit_factor_spec.py:591-612` OPERATING_INCOME_AND_INTEREST_MATCHED_V2 | `:606-624` (`rule_id=\"operating_income_and_interest_matched_v2\"` at :607); 591-593 is NET_INCOME_AND_EQUITY |
| OD-25, E8 | `docs/POST-REBOOT-CHECKLIST.md:72` | `:74` \"- No edits to spec_freeze_v1/v2/v3/v4 in place — a change is a v5.\" |
| D3 | `pit_price_basis.py:409-453` pit_safe_split_factor | def at `:387`; 409-453 is the window query through the returns |
| OD-06 | `pit_policy.py:733-739` consumer rule (2) | `:734-740` |
| OD-14 | `pit_valuation_spec.py:2223-2226` | `:2224-2226` |
| B7, C2 | `pit_valuation_spec.py:2223-2240` debt_gate | `:2221-2238` |
| OD-13, B8 | `pit_factor_spec.py:2466-2472` AVAILABILITY-only note | `:2469-2474` |
| D2 | `pit_price_basis.py:207-215` assert_price_basis | def at `:203`, body to :215 |
| B5 | `test_pit_factor_spec.py:645-653` 'names every state' pattern | `:654` |
| §3 | `statlib.py:158-167` percentile_to_score | `:156-165` |
| D1 | `pit_eps_cohort.py:181-212` load_ttm | `:183-209` |
| OD-18 | `pit_identity.py:1284-1288` volume>0 window | `:1286-1289` |
| OD-02 | \"audit R2 text (docs:230)\" | docs:230 is recommendation **2.**; **R2** is docs:244 |
| OD-24 | `pit_frozen_spec.py:862-880` (dict unnamed) | key of `SCORE_ASSEMBLY_GAP` (:827), read as `F.SCORE_ASSEMBLY_GAP[...]` at test_pit_frozen_spec.py:585 |

## Uncited figures (verify or cite before relying on them)

- OD-06 \"coverage ~38% of base\" -- no anchor; store measurement.
- OD-07 / §3 \"~37.8% EBITDA <= 0\" -- cite `docs/MODEL-LINEAGE.md:1289`.
- B8 \"pit_replay_rss: fixed cost grows ...\" -- no anchor.
- A7 \"rows that today read cohort_too_small_to_rank\" -- pilot-DB fact; cite `docs/V4-GOVERNANCE-AUDIT-2026-09-22.md:187` (\"`cohort_too_small_to_rank` 93 / 62\") and `pit_normalization.py:190`.

## Probe confirmations worth keeping in the plan (all reproduced exactly)

- `benchmark_margin_50_75(eb, mg)` = (0.17, 3, '... 50-75 percentile of 12 (650 to 925) = 0.1700'); 2 pairs -> (None, 0, 'only 2 peers ... need 3').
- `normalize(EBITDAExcessLevel, 0.30, benchmark=0.17, cohort_values=mg)` -> 98.24541388014812, scale 0.05499999999999999 (`close(0.055)` needed, not `==`), 'cohort_iqr', n_cohort 12, anchor 0.17, 'cohort_p50_p75_mean_margin'; 3 values -> 'cohort_too_small_for_dispersion'; 12 identical -> 0.06 'fixed'; benchmark=None -> 'benchmark_unavailable'.
- `valuation_score_detail`: 100 -> -53.99049042384845; 5 -> 31.256122562826963; 3000 -> -100.0; (100, 28) -> -37.33175842435203.
- `block_score(V, {pe_relative: 10.0})` = 10.0 while `presence({'pe_relative': True}).state` = ABSENT.
- v2 total_debt rung keys == `set(DEBT_RUNG_EVEBITDA_V1)`; bounds exact/exact/lower_bound x4.
- Census {COMPUTABLE 6, REFUSED_AMBIGUOUS 1, NOT_COMPUTABLE 6}; reachable 0.6; `pit_replay.validate()` = []; `pit_frozen_spec.verify()` intact, 61 components, digest 58722f4c...; `pit_frozen_spec.validate()` = [].
- Masks: E with benchmark -> 'BENCH=1,GROW=0,EFF=0,ACC=0'; Q -> 'FCF=0,ICOV=1,NDE=1,DMC=0'; MASK_NONE 'V[PE=0,PEPeer=0,EVEBITDA=0]'.
- `pit_shares.SPLIT_NO_EVENTS_RECORDED == pit_price_basis.GATE_NO_EVENTS` (\"no_events_recorded\"); `ANCHOR_COHORT_MEDIAN` exists only in pit_valuation_spec (:399), absent from pit_normalization; `pit_eps.STALENESS_QTRS = 4` = the 15-month budget (pit_eps.py:1229-1236).

Scratch: `C:\\Users\\logan\\AppData\\Local\\Temp\\claude\\vqplan\\probe_cite.py`. No repository file was touched.

---

# Verification notes: refute

Verdicts: {'CONFIRMED': 57, 'REFUTED': 8}

- **REFUTED OD-08** — Already decided in substance by DIGESTED bodies; only the refusal code's spelling is open. pit_factor_spec.py:557-567 DEBT_CASH_EBITDA `primitives=("total_debt", "cash", "operating_income", "depreciation_amortisation")` with why 'Debt and cash are REQUIRED here because they are what the factor measures'; :490-498 EV_EBITDA_SIX lists "cash"; FORMULAS_V1 :1318 `net_debt = total_debt - cash` and :1309-1311 `enterprise_value = market_cap + total_debt - cash` (SPECS_V4 and FORMULAS_V1 are in COMPONENTS, pit_frozen_spec.py:381, :383); pit_policy.py:993-996 headline 'a ... row that does not resolve means this ladder found nothing, and NOTHING MORE'. Option B (company_scoring.py:738 `(float(cash) if is_finite(cash) else 0.0)`) would invent a value the digested formula does not contain -- a v6 change, not an engine choice.
- **REFUTED OD-10** — Already decided: the digested FORMULAS_V1 (pit_factor_spec.py:1318 `"net_debt_ebitda": "net_debt_ebitda = net_debt / ebitda; net_debt = total_debt - cash; PERCENTILE_RANK"`) contains no floor or state split, v1 says company_scoring.py:744 `"""NetDebt / EBITDA. Negative is possible and is financially strong.`, and probe `percentile_rank_score(-0.5, [2.0, 1.0, 0.5, 3.0], higher_is_better=False).score` == 100.0. Option B would add a rule the digested formula does not state -> a v6 formula change. Option A is the status quo, not a choice to make.
- **REFUTED OD-12** — Already decided and recorded: pit_factor_spec.py:287-291 `#: `debt_market_cap` is deliberately NOT here. ... Its ceiling is therefore unchanged and that is a declared choice rather than an oversight.` and :292 `VALUATION_FACTORS: frozenset[str] = frozenset({"ev_ebitda", "pe_ratio"})`; probe: BY_KEY_V4['debt_market_cap'].max_admissible_rung == '' while BY_KEY_V4['ev_ebitda'].max_admissible_rung == 'sic2', and SPECS_V4 is digested (pit_frozen_spec.py:381). Option A is the status quo; option B is a successor-spec change. Not an open engine decision.
- **REFUTED OD-19** — The premise '(so pe_ratio's min_peer_count 3 never binds)' is false: eligible_peers applies the spec floor to the ELIGIBLE subset, not the stored set -- pit_factor_spec.py:2478-2482 `if len(kept) < item.min_peer_count: return refuse(refusal_reason(factor_key, spec_version), f"{len(kept)} of {len(offered)} peers satisfy {rule.rule_id}; {factor_key} requires {item.min_peer_count} ...`. A stored set of >= 12 (pit_coverage.py:668 `if info is None or info[2] < MIN_COHORT_N:`) filtered by PRICED_EPS_V2 (price + EPS) can leave fewer than 3 eligible, so 3 binds on n_eligible. The walk-finer-sets question itself remains open (audit docs:375 'GAP 5 is three-stage, not two').
- **REFUTED A9** — The claim 'independent of OD-01 because 12 pairs are handed to the function' is wrong: A5's helper filters `items` by `int(e) == int(entity_id)` BEFORE calling benchmark_margin_50_75, so under EXCLUDE only 11 pairs enter. Probe: excluding entity 8 (in the band) -> (0.16333333333333333, 3, '... 50-75 percentile of 11 (600 to 950) = 0.1633'); excluding entity 1 -> (0.17, 3, '... of 11 (700 to 950)'). The pinned (0.17, 3) for target 1 survives only because entity 1 lies outside the band under every option. Either state that dependence in the test or pick a target inside the band so the test discriminates OD-01. The normalise() pins (98.245..., 0.055, cohort_iqr, 12) and the mask probe `_mask(BLOCK_E, {'ebitda_benchmark': 1.0}) == 'BENCH=1,GROW=0,EFF=0,ACC=0'` are correct.
- **REFUTED D1** — pit_eps_cohort cannot supply `method` as written: load_ttm's SELECT (pit_eps_cohort.py:196-198) is `"SELECT entity_id, concept_key, period_end, ttm_eps, available_date, pe_leg, sign_case FROM pit_eps_ttm"` and EntityTtm.select returns only `{"concept_key", "period_end", "available_date", "ttm_eps", "pe_leg", "sign_case"}` (:170-172) -- no `method`, no `sign_crossing`. Recording method (and OD-21's method distribution, D6's sign_crossing) requires extending pit_eps_cohort (undigested), which the step does not say. The rest holds: pit_eps.py:1250-1252 'A NEGATIVE TTM is `available = True` with `pe_leg = unavailable_loss_making`'; staleness is 15 months (pit_eps STALENESS_QTRS 4, pit_policy.py:1076 `MAX_AGE_ANNUAL_MONTHS = 15`, CONCEPT_MAX_AGE_MONTHS :1086-1088 overrides only shares_outstanding).
- **REFUTED D7** — The test-impact claim ':246-248 still >= 6 (entries after deletions: 6)' is wrong. Probe `len(pit_replay.KNOWN_LIMITATIONS) == 7` (entries at :554-582: feature_available_date, source_tag_changed, final_score, listing_id, fact_max_age_days, 'Seven of thirteen', peer-set lineage). Removing the listing sentence (:566-569) and the 'Seven of thirteen' paragraph (:572-582) leaves 5, and test_pit_replay.py:247 `len(back["known_limitations"]) >= 6,` FAILS. Either keep a rewritten entry or re-pin the threshold. The plan flips themselves (pit_replay.py:486-493; REASON_WHY :324-330 'This engine has not wired the pit_eps TTM join') are as described.
- **REFUTED E7** — The proposed benchmark witness shape breaks validate() rather than being reported by it: pit_replay.py:2023-2025 `for case in pit_factor_spec.FORMULA_GOLDEN_V1: got, why = _golden_eval(case, ladders); want = case["expect"]` -- a case with `expect_anchor`/`expect_band` and no `expect` raises KeyError; and _golden_eval (:1897-1918) returns `getattr(prim, RAW_FIELD[case["factor"]][0])` from a single-entity resolve_primitives, so a cohort anchor M_s (not a Primitives attribute) cannot be witnessed by extending _golden_eval alone -- the loop needs a second evaluator and case-shape dispatch. The Q witnesses (net_debt_ebitda / fcf_conversion with 'expect') fit the existing loop once _golden_eval synthesises (eid, tag, 0) instants and (eid, tag, 4) durations. test_pit_replay.py:197-206 mutates `FORMULA_GOLDEN_V1[0]["expect"]` and is unaffected.

Missing / additions:

- pit_eps_cohort.load_ttm / EntityTtm.select must be extended to carry `method` and `sign_crossing` (pit_eps_cohort.py:196-198 SELECT lacks both; :170-172 select() returns neither) before D1 can record method, OD-21 can report the method distribution, or D6 option A can read sign_crossing.
- A distinct refusal code for the engine-side peer floor (A7/OD-04 A): pit_factor_spec.py:312 `REASON_TOO_FEW = pit_store.REASON_NO_PEERS` already emits 'insufficient_peers' from eligible_peers (:2478-2480), so the plan's floor refusal would be indistinguishable in reason tallies.
- D7 must re-pin test_pit_replay.py:247 (`>= 6`): KNOWN_LIMITATIONS has 7 entries today (probe), and removing the listing sentence and the 'Seven of thirteen' paragraph leaves 5.
- validate()'s golden loop (pit_replay.py:2023-2032) reads `case["expect"]` for every case and _golden_eval returns a Primitives attribute; a cohort-anchor witness (E7 / OD-22) needs a second evaluator and case-shape dispatch, not an _golden_eval extension.
- The registry seed is never passed through pit_normalization._validate (:1552; only register_factor calls it at :1675). Any appended entry (B4, C1) should be validated in a test, or candidate_v2_registry() should call _validate on each spec, so a wrong anchor/scale is caught at import.
- ACTION_GATE_VERSION is a version string (pit_price_basis.py:323), not a body; if OD-17 changes pit_safe_split_factor, the string must be bumped by convention or the freeze stays silent (the v4-audit principle at pit_frozen_spec.py:377-378).
- OD-01 evidence omitted from the decision record: the DIGESTED registry rationale (pit_normalization.py:1853-1856, :1861 'measured collapse: rank(margin - M_s) == rank(margin), exactly') and its measurement (test_pit_normalization.py section 9(b) `excess = [m - m_a for m in A_MARGIN] + ...`) presuppose one cohort-constant M_s, i.e. the INCLUDE convention.
- Any v6 rewrite of FORMULAS_V1 for pe_relative / ev_ebitda_supplement / the Q keys must keep the pit_derive prefix: pit_factor_spec._FORMULA_NODE maps them to nodes and test_pit_factor_spec.py:649-651 asserts `FS.FORMULAS_V1[k].startswith(pit_derive.NODES[n].formula)`.
- seed_parents' docstring and `pit_listing_why` text (pit_replay.py:686-689, :722-724 'listing_id is NULL on every written row') must be rewritten when B8 seeds pit_listing; the plan changes the count but not the recorded why.
- pit_factor_spec.ebitda_benchmark_cohort (:2565-2587) already chains eligible_peers -> benchmark_band with both spec floors; A5 re-implements the floor logic in the engine. State whether the spec-side chain is superseded or reused, so two floor implementations do not diverge.
- pit_frozen_spec.validate() :1206-1208 pins `ENGINE_MUST_DERIVE_FROM != _fs.FACTOR_SPEC_VERSION_V4` -- under OD-11 B / OD-04 C (SPECS_V5) this pin moves too; E2 lists the engine and manifest pins but not this one.
- B8's market-cap path repeats the per-entity share audit that _priced_and_defensible already runs inside eligible_peers (pit_factor_spec.py:2316-2341); memoising the audit across the two call sites is unplanned.

# Refutation pass -- corrections to the plan (R = `C:\Users\logan\shafferfineval-app\shafferfineval`, HEAD 609cefe, spec_freeze_v5 digest 58722f4c, 61 components)

Labels: **FACT** = read at the cited line; **PROBE** = one `.venv\Scripts\python.exe -B` process importing modules only (no store, no tests); **CORRECTION** = what the plan should say instead; **OWNER DECISION** = economics, not chosen here. Nothing was edited; the store was not opened.

## 1. Refuted items (evidence-backed)

| id | claim refuted | evidence |
|---|---|---|
| OD-08 | \"refuse vs substitute 0.0\" is an open decision | FACT the DIGESTED bodies already require cash: `pit_factor_spec.py:557-567` DEBT_CASH_EBITDA `primitives=(\"total_debt\", \"cash\", ...)` \"Debt and cash are REQUIRED here\"; `:1318` `net_debt = total_debt - cash`; `:1309-1311` `enterprise_value = market_cap + total_debt - cash`; `pit_policy.py:993-996` absence headline. Option B is a v6 body change. Only the refusal code's spelling is open. |
| OD-10 | negative net debt needs a decision | FACT `pit_factor_spec.py:1318` (digested) `\"net_debt_ebitda = net_debt / ebitda; net_debt = total_debt - cash; PERCENTILE_RANK\"` has no floor; `company_scoring.py:744` \"Negative is possible and is financially strong\"; PROBE `percentile_rank_score(-0.5, [2,1,0.5,3], higher_is_better=False).score == 100.0`. Option A is the status quo; B is a v6 formula change. |
| OD-12 | debt_market_cap ceiling is open | FACT `pit_factor_spec.py:287-291` \"Its ceiling is therefore unchanged and that is a declared choice rather than an oversight\"; PROBE `BY_KEY_V4['debt_market_cap'].max_admissible_rung == ''` inside digested SPECS_V4. Already decided; B is a successor-spec change. |
| OD-19 | \"pe_ratio's min_peer_count 3 never binds\" | FACT `pit_factor_spec.py:2478` `if len(kept) < item.min_peer_count:` -- the floor is applied to the ELIGIBLE subset, which PRICED_EPS_V2 can shrink below 3 even from a stored set of >= 12. The walk-finer question stays open; its stated premise is wrong. |
| A9 | fixture \"independent of OD-01 because 12 pairs are handed to the function\" | A5's helper filters by `entity_id` first. PROBE: excluding entity 8 (in the band) -> `(0.16333..., 3, '... of 11 (600 to 950)')`; excluding entity 1 -> `(0.17, 3, '... of 11 (700 to 950)')`. The pinned (0.17, 3) survives for target 1 only because entity 1 is outside the band under every option. |
| D1 | \"recording concept_key, period_end, available_date, method, sign_case, pe_leg\" from `load_ttm` / `EntityTtm.select` | FACT `pit_eps_cohort.py:196-198` `SELECT entity_id, concept_key, period_end, ttm_eps, available_date, pe_leg, sign_case FROM pit_eps_ttm`; `:170-172` select() returns no `method` and no `sign_crossing`. Extending pit_eps_cohort is unplanned work (also blocks OD-21 reporting and D6 option A). |
| D7 | \":246-248 still >= 6 (entries after deletions: 6)\" | PROBE `len(pit_replay.KNOWN_LIMITATIONS) == 7`; removing `:566-569` and `:572-582` leaves 5; `test_pit_replay.py:247` `len(back[\"known_limitations\"]) >= 6` fails. Re-pin or keep a rewritten entry. (A8/§8's \"8 entries\" is the same miscount; A8's conclusion still holds at 7.) |
| E7 | benchmark witness `{\"expect_anchor\", \"expect_band\"}` handled by extending `_golden_eval` | FACT `pit_replay.py:2023-2025` `want = case[\"expect\"]` for every case (KeyError, not a reported problem); `_golden_eval` returns `getattr(prim, RAW_FIELD[...][0])` from a single-entity `resolve_primitives` -- a cohort anchor is not a Primitives attribute. Needs a second evaluator and case-shape dispatch in validate(). |

## 2. Confirmed with corrections (line, count or wording)

- **OD-01 (open)** -- add to the evidence table: the digested registry rationale presupposes a cohort-constant bar (`pit_normalization.py:1853-1856` \"margin - M_s is a cohort-constant shift\", `:1861` \"measured collapse: rank(margin - M_s) == rank(margin), exactly\"), and the measurement behind it (`test_pit_normalization.py` §9(b) `excess = [m - m_a for m in A_MARGIN] + [m - m_b for m in B_MARGIN]`) applies ONE M_s per cohort to band members too -- the INCLUDE convention. The frozen core (`company_scoring.py:374`, `build_ebitda_peer_cohort` fed `others`) and pit_derive (`pit_derive.py:586-594`, \"FAITHFUL to the production core\") exclude. Evidence points both ways; still an OWNER DECISION.
- **OD-03** -- precedent for option 1 exists: `pit_factor_spec.py:2565-2587` `ebitda_benchmark_cohort` already enforces `min_vector=item.min_peer_count, min_band=item.min_band_members` (dollar mean). Say whether A5 supersedes or reuses it.
- **OD-04 / A7** -- the proposed `insufficient_peers` is ALREADY the eligibility floor's string (`pit_factor_spec.py:312` `REASON_TOO_FEW = pit_store.REASON_NO_PEERS`, `:2478-2480`); the two stages would merge in reason tallies. Choose a distinct code or state the merge. The `MIN_RANK_PEERS + 1` arithmetic relies on the target being in `n_values`, which holds because stored sets include the target (`pit_coverage.py:668-678`). Digesting `PEER_FLOOR_V1` in Stage A contradicts \"Stage A under v5\".
- **OD-05 / B4 / C1** -- `pit_normalization._validate` (`:1552`) is called only by `register_factor` (`:1675`); appended seed entries are never validated at import. Its BENCHMARK_ANCHORED rule (`:1577-1581`) rejects only ANCHOR_NONE/ANCHOR_ZERO, so `cohort_median_multiple` would pass -- C1's \"validator accepts\" worry is moot; the binding constraint is `normalize()` needing `benchmark=` from the engine.
- **OD-07** -- \"~37.8% of the universe\" should read \"37.8% EBITDA <= 0 in the FY2014 n=2,756 valuation-leg measurement\" (`docs/MODEL-LINEAGE.md:1287-1288`).
- **OD-14** -- members are gated by construction under C2 (every entity in `needed` forms `ev_ebitda` only after `debt_gate`; CohortCache reads Primitives, `pit_replay.py:999-1004`); the \"availability-only membership\" option would need a separate ungated member multiple.
- **OD-15 / D5** -- only T2 (tanh) is consistent with the DIGESTED `FORMULAS_V1['pe_relative']` (`pit_factor_spec.py:1308` \"BENCHMARK_ANCHORED\"); T1 and T3 both need the v6 body change.
- **OD-17 / D3** -- `ACTION_GATE_VERSION` is a version STRING (`pit_price_basis.py:323`), not a body; a gate-semantics change moves nothing in the digest unless the string is bumped by convention (`pit_frozen_spec.py:377-378`).
- **C3** -- the engine already refuses `R_NO_PEER_SET` before any eligible_peers call when there is no stored set (`pit_replay.py:1378-1380` `elif plan.needs_cohort and cohort is None: ... reason = R_NO_PEER_SET`), and a stored cohort always carries a rung (`pit_coverage.py:672-674`). No new guard. `refusal_reason` is at `pit_factor_spec.py:2209-2218`, not `:2222-2224`.
- **C4 / OD-20** -- `pit_score_signature.score_v2` already has `reasons: Optional[Mapping[str, str]] = None` (`:731-733`); only `pit_factor_blocks.assemble` (`:372`) omits it.
- **E1** -- no REASON_* constant sits at `:89/:91` (comments); they are at `:98, :100, :173, :220`.
- **E2** -- `pit_frozen_spec.validate()` `:1206-1208` also pins `ENGINE_MUST_DERIVE_FROM == FACTOR_SPEC_VERSION_V4`; it moves with any SPECS_V5.
- **B10** -- `census_tags` already unions v1+v2 ladder tags (`pit_coverage.py:329-332`); the extended guard is a tautology today (harmless).
- **B8** -- `_priced_and_defensible` (`pit_factor_spec.py:2316-2341`) already runs the per-entity share audit inside eligible_peers; B8 repeats it per entity-date.

## 3. Probe results relied on (PROBE, one process, imports only)

`benchmark_margin_50_75(eb, mg) -> (0.17, 3, '... of 12 (650 to 925) = 0.1700')`; `normalize(EBITDAExcessLevel, 0.30, benchmark=0.17, cohort_values=mg)` -> 98.24541388014812 / 0.05499999999999999 / cohort_iqr / n 12 / anchor 0.17 / cohort_p50_p75_mean_margin; 3 band margins -> `cohort_too_small_for_dispersion`; 12 identical -> 0.06 `fixed`; benchmark None -> `benchmark_unavailable`; 2 pairs -> (None, 0). `valuation_score_detail`: 100 -> -53.99049042384845, 5 -> 31.256122562826963, 3000 -> -100.0, 6000 -> -100.0 (clamped), (100, 28) -> -37.33175842435203, -5 -> `unavailable_loss_making`. `price_earnings(30.0, 0.005)` -> pe 6000, leg `available_extreme_valuation`, extreme True. `block_score(V, {pe_relative: 10.0})` -> 10.0 while `presence({'pe_relative': True}).state == 'ABSENT'`. `_mask(BLOCK_E, {'ebitda_benchmark': 1.0}) == 'BENCH=1,GROW=0,EFF=0,ACC=0'`. v2 total_debt rung keys == `set(DEBT_RUNG_EVEBITDA_V1)`. `len(KNOWN_LIMITATIONS) == 7`. `STILL_BLOCKED` / `SCORE_ASSEMBLY_GAP` not in COMPONENTS. `MAX_AGE_ANNUAL_MONTHS = 15` with no `eps_ttm` override.

## 4. Owner decisions that remain genuinely open after this pass

OD-01, 02, 03, 04, 05, 06, 07, 09, 11, 13, 14 (target walk), 15, 16, 17, 18, 19 (walk-finer question only), 20, 21, 22, 23, 24, 25. OD-08, OD-10 and OD-12 are already decided by digested or recorded declarations; reopening any of them is a v6 body change and should be labelled as such rather than presented as a free engine choice.

---

# Verification notes: complete

Verdicts: {'CONFIRMED': 36, 'REFUTED': 4}

- **REFUTED D1** — pit_eps_cohort.py:200-203 `"SELECT entity_id, concept_key, period_end, ttm_eps, available_date, " "pe_leg, sign_case FROM pit_eps_ttm"` and EntityTtm.select :172-174 returns `{"concept_key": concept, "period_end": period_end, "available_date": newest[0], "ttm_eps": newest[1], "pe_leg": newest[2], "sign_case": newest[3]}` -- NO `method`, so 'recording ... method' (needed for OD-21's method distribution) cannot come from this loader as written; the column exists (pit_eps.py:592 `method TEXT NOT NULL`). Negative TTM leg confirmed: pit_eps.py:378 `PE_UNAVAILABLE_LOSS = "unavailable_loss_making"`, :1252 docstring. The '15-month staleness' is unverified: pit_policy.py has no 'eps_ttm' entry (grep), select() calls `pit_policy.is_stale(period_end, as_of, pit_eps.STALENESS_QTRS, pit_eps.STALENESS_CONCEPT)`.
- **REFUTED D6** — The snippet `picked.get("sign_crossing")` always yields None: EntityTtm.select returns only concept_key/period_end/available_date/ttm_eps/pe_leg/sign_case (pit_eps_cohort.py:172-174) and load_ttm's SELECT (:200-203) does not read sign_crossing, although the column exists (pit_eps.py:599 `sign_crossing INTEGER NOT NULL DEFAULT 0`). pit_valuation_spec.py:1024 `EARNINGS_SIGN_CROSS = "EARNINGS_SIGN_CROSS"`; :1157 `def eps_transition_from_pair(pair)`; DDL comment :1901 `-- earnings_discontinuity_state: EARNINGS_SIGN_CROSS or NULL`. Option A needs the loader extended (and pit_eps_cohort.validate_against_selector re-run) or a per-entity ttm_eps_as_of_detail call.
- **REFUTED D7** — Plans :486-493 and REASON_WHY :324-330 confirmed; test :1080-1113 confirmed (`by_key["ebitda_benchmark"]` etc. would KeyError on an empty refused list, so the rewrite is required). The '>= 6 (entries after deletions: 6)' claim is wrong: KNOWN_LIMITATIONS has 7 entries today (probe; pit_replay.py:555-582); B8 removes the listing entry (:566-568) and D7 deletes the 'Seven of thirteen' entry (:572-582) -> 5 entries, and test_pit_replay.py:246-248 `len(back["known_limitations"]) >= 6` goes RED unless re-pinned or an entry is added.
- **REFUTED E7** — The proposed benchmark witness shape crashes validate() rather than being reported: pit_replay.py:2024 `want = case["expect"]` is unconditional (KeyError for a dict with expect_anchor/expect_band only), and _golden_eval :1907-1913 `if "ebitda" in case: ... else: oi["2018-12-31"] = float(case["operating_income"])` KeyErrors on a `pairs` witness; also `attr, why_key = RAW_FIELD[case["factor"]]` (:1917) KeyErrors for any Q factor until B3 lands. So E7 needs a declared witness schema (an `expect` key or a validate() branch per shape) in addition to the _golden_eval extension. FORMULA_GOLDEN_V1 digested (pit_frozen_spec.py:384); test_pit_replay.py:197-206 mutates `case["expect"]` of witness 0.

Missing / additions:

- COMMIT ATOMICITY (validate rule): pit_replay.py:1966-1968 `if key in RAW_FIELD and plan.verdict == VERDICT_NOT_COMPUTABLE: problems.append("%s is NOT_COMPUTABLE but declares a raw field" % key)` -- RAW_FIELD entries (B3, C2, D-steps) and the matching plan flips (B6, C5, D7) must land in ONE commit each, or test_validate (test_pit_replay.py:135-136) is red between them. The plan orders them as separate steps without saying so.
- GOLDEN WITNESS SCHEMA: pit_replay.py:2024 `want = case["expect"]` and _golden_eval :1907-1913 (`if "ebitda" in case ... else: case["operating_income"]`) hard-code two shapes. E7's benchmark witness (`pairs`, `expect_anchor`, `expect_band`) and B2's Q witnesses (total_debt/cash/ocf/capex) need (a) a declared witness schema that validate() branches on, (b) the synthetic index to name WHICH ladder tags/rungs it plants (SUM rung with all three components vs `DebtLongtermAndShorttermCombinedAmount`; `PaymentsToAcquirePropertyPlantAndEquipment`) because the rung chosen sets total_debt_rung and, under OD-06 B, whether the witness is gated, and (c) a fixture for the instants at qtrs=0 keyed `(eid, tag, 0)`.
- KNOWN_LIMITATIONS pin: 7 entries today (probe; pit_replay.py:555-582), test_pit_replay.py:246-248 asserts `>= 6`. B8 removes the listing entry (:566-568) and D7 deletes the 'Seven of thirteen' entry (:572-582) -> 5 -> red. Either re-pin the test at D7 or replace the deleted entries with the surviving limitations (lower_bound debt rungs; survivorship; no replay authorisation).
- A7 IMPORT ORDER: `_REGISTRY = pit_normalization.candidate_v2_registry()` (pit_replay.py:1024) is defined AFTER `FACTOR_PLAN` (:441-535) and FactorPlan is `@dataclass(frozen=True)` (:396). A registry-derived `cohort_floor` cannot be computed inside the plan literal; the plan needs a post-pass (`dataclasses.replace`) or `_REGISTRY` moved above the plan, plus `as_dict()` (:422-433) extended so plan_record/summary_json carries the floor.
- REASON COLLISION (adjacent to OD-04): the engine's proposed n_values floor reason `insufficient_peers` (pit_store.py:111) is the SAME string eligible_peers emits for its n_eligible floor (pit_factor_spec.py:312 `REASON_TOO_FEW = pit_store.REASON_NO_PEERS`). A row would carry one code for two stages; either declare that the ne/nv codes disambiguate, or name a distinct engine code -- an OWNER DECISION to add under OD-04.
- BASIS-STRING TRANSLATION (second half of GAP 7): pit_rawprice.PRICE_BASIS_RAW == pit_shares.PRICE_BASIS_RAW == 'raw_as_printed_unadjusted' (probe) is what raw_close_as_of and market_cap_as_of_detail put in `price_basis`; pit_price_basis.assert_price_basis / normalised_pe require 'price_raw_as_traded'. pit_price_basis.py:830-831 only tolerates both spellings. D2/D3 pass `PRICE_RAW_AS_TRADED` as a literal, i.e. the engine asserts the basis for the record instead of checking the record's own basis. SPEC_PRIMITIVE_PRICE_BASIS_V1 (B9) or a `basis_of(record)` helper with a test must declare the mapping.
- TTM LOADER FIELDS (D1/D6): pit_eps_cohort.load_ttm SELECTs 7 columns (:200-203) and EntityTtm.select returns 6 keys (:172-174) -- no `method`, no `sign_crossing` -- although both exist in pit_eps_ttm (pit_eps.py:592, :599). OD-21's method distribution and OD-20 option A require extending the loader (interning two more columns; re-run pit_eps_cohort.validate_against_selector) or N per-entity ttm_eps_as_of_detail calls per date. Neither is in the steps.
- ONE VALUATION PRICE RESOLVER: market_cap_as_of_detail also takes `roll_back_days` (pit_rawprice.py:1156); B8 omits it while D2 passes VALUATION_PRICE_ROLL_BACK_DAYS. OD-18 must govern BOTH the market-cap price (dmc, ev) and the P/E price, and B8/C2/D2 must share one resolver and one memo per (entity, date), or EV's market cap and the P/E can be priced from different sessions.
- ORACLE KEY LIST: test_pit_replay.py:1281-1284 compares a fixed tuple of per-date statistics (`"rows", "n_targets", ... "n_eligible_peer_calls"`). B3's per-date rung/bound tally and any new run_date stat must be appended there or the oracle does not check them.
- B4 REGISTRY ENTRY SHAPE: test_pit_normalization.py:555 `all(s.economic_question and s.rationale for s in registry.values())` -- the proposed FactorSpec snippet has no `rationale=`; candidate_v2_registry() runs no validator at build (probe), so the only guard is that test. Add rationale (and for a BENCHMARK_ANCHORED EV entry, a scale: :557 and validator :1583-1585).
- C1 RELABEL PIN: pit_factor_blocks.validate() :964-965 `if SUBFACTOR_DIRECTION_V1.get("ev_ebitda_supplement") != LOWER_IS_BETTER: problems.append("ev_ebitda_supplement must be LOWER_IS_BETTER")`, consulted by pit_frozen_spec.validate() (`pit_factor_blocks.validate() is not clean`). The convex/EMBEDDED option under OD-13 therefore also edits that pin and its test; the plan lists only the digested body move.
- BENCHMARK REASON FOR AN EMPTY BAND: benchmark_margin_50_75 returns (None, 0, 'no peer fell inside the 50-75 EBITDA band') (:940-941) and (None, 0, 'only N peers ...') (:932-934); A5's helper returns why=None for both, so the row reads `benchmark_unavailable` while a band of 1-2 reads `band_too_thin`. Declare the code for n_band == 0 (and for pairs < BENCHMARK_MIN_COHORT_V1 under OD-04 B) -- part of OD-03.
- OD-02 POPULATION STATEMENT: record["values"] in A4 includes the TARGET's margin (stored sets include the target, pit_coverage.py:678), so under OD-01 EXCLUDE-for-the-anchor the scale sample is still target-inclusive (COHORT_DISPERSION semantics). The plan must state this combination explicitly under OD-02 so the mode name in A1 is not misread.
- STAGE B FIXED COST NOT BUDGETED: CohortCache.get -> eligible_peers('debt_market_cap') runs `_priced_and_defensible` (pit_factor_spec.py:2316-2341: scored_universe_as_of + shares_as_of_any + share_class_audit PER MEMBER PER PEER SET), and B8's market_cap_as_of_detail repeats shares_as_of_any + share_class_audit per needed entity (pit_rawprice.py:1201-1212). The plan says 'measure before any pilot' but names no memo shared between the two audits nor a pit_replay_rss run as a gate before B8; add both as steps.
- OD-09 A vs ELIGIBILITY: DEBT_CASH_EBITDA / FCF_AND_EBITDA / DEBT_AND_MARKET_CAP count total_debt/cash/OCF/capex as newest-non-stale singles (`concept_available`, pit_factor_spec.py:2461-2463) while OD-09 A values them AT P; the v4 precedent for the same gap was a successor eligibility rule (OPERATING_INCOME_AND_INTEREST_MATCHED_V2, :591-612) which moves FACTOR_SPEC_VERSION. OD-09 lists no 'SPECS_V5 matched rule' option; add it or record that the interest_coverage 'UPPER BOUND' precedent (:606-611) is adopted for the three Q rules.
- MISSING ENGINE-LOCAL TESTS: (a) `_level_at` on a synthetic index: SUM rung with all three components at P scores, with one component missing falls to the next rung, duration rung at (eid, tag, 4); (b) the row-level rung codes dr/db and source_tag == rung key; (c) a debt_gate refusal per rung under OD-06 B; (d) FactorPlan.as_dict round-trip with the new fields (test_pit_replay.py:235-248); (e) plan_record 'reason_vocabulary' carries every new reason with a >= 60-char why; (f) the new normalise(..., benchmark=) kwarg keeps the existing calls at :795-826 unchanged.
- DOC/PROSE SITES NOT IN THE LEDGER: pit_replay.py:9 (`spec_freeze_v5 at this writing`), docs/POST-REBOOT-REPORT-2026-09-22.md:375-376 (`census 6/1/6`, `pit_replay/1.1`), docs/MODEL-LINEAGE.md:1241, pit_frozen_spec.py:900 (`engine_uses ... from pit_replay/1.1`), docs/D3-EVIDENCE-2026-09-22.md (mentions spec_freeze_v5). None is digested; all read stale after the cut.
- E2 SPEC-VERSION PLUMBING (only if SPECS_V5): pit_factor_spec.spec_versions() :1464-1467 hard-codes four names and SPEC_SETS :1454+ must gain the v5 entry; pit_frozen_spec.validate :1200-1202 pins `ENGINE_MUST_DERIVE_FROM != _fs.FACTOR_SPEC_VERSION_V4`; test_pit_factor_spec.py:650 `spec_versions()[-1] == FS.FACTOR_SPEC_VERSION_V4` and :651-652 the 11-by-reference count. The plan cites the tests but not spec_versions()/SPEC_SETS.

# Completeness review -- what the engineer still lacks to run Stage A and Stage B end to end (HEAD 609cefe, digest 58722f4c, 61 components -- verified by probe)

Labels: **FACT** (read at the cited line), **PROPOSAL** (this review), **OWNER DECISION** (economics, not chosen). No file was edited; no suite, replay or pilot ran; the store was not opened. Two pure-python probes (`.venv\\Scripts\\python.exe -B`, module imports only) produced every number below; `pit_replay.validate()` and `pit_frozen_spec.validate()` both return `[]` today.

## 1. Verified facts that settle open questions in the plan

- FACT `benchmark_margin_50_75([1,2,3,100],[.1,.2,.3,.4])` -> `(0.3, 1, 'M_s = mean margin of 1 peers ...')`: the function accepts a band of ONE (OD-03 premise holds). `pit_factor_spec.REASON_NO_BAND == 'band_too_thin'` (:314). `spec('ebitda_benchmark','factor_spec_v4')` -> min_band_members 3, min_peer_count 12, rule `ebitda_and_revenue_matched_v1`.
- FACT the 12-pair fixture gives `(0.17, 3)`; `normalize(EBITDAExcessLevel, 0.30, benchmark=0.17, cohort_values=mg)` -> 98.24541388014812 / scale 0.055 `cohort_iqr` / n_cohort 12 / anchor_source `cohort_p50_p75_mean_margin`; band-only (3) -> `cohort_too_small_for_dispersion`; identical -> 0.06 `fixed`; no benchmark -> `benchmark_unavailable`.
- FACT the v2 total_debt rung keys equal `set(DEBT_RUNG_EVEBITDA_V1)` (probe True), and every tag of the cash / total_debt / operating_cash_flow / capex / shares_outstanding ladders is already in `pit_coverage.census_tags` (missing: [] each) -- B10's guard passes on day one.
- FACT `valuation_score_detail`: (100,20) -53.99049042384845; (5,20) +31.256122562826963; (3000,20) -100.0; (100,28) -37.33175842435203. `block_score(V,{pe_relative:10})` = 10.0 while `presence({'pe_relative':True}).state` = ABSENT (OD-20 premise holds).
- FACT `FACTOR_DIRECTION_V1['ebitda_benchmark'] == HIGHER_IS_BETTER`, registry `higher_is_better True`; `percentile_rank_score(-0.5, [...], higher_is_better=False).score == 100.0` (OD-10 A premise).
- FACT `pit_score_signature.score_v2` ALREADY takes `reasons=` (:731-733); only `pit_factor_blocks.assemble` (:372) fails to pass it -- OD-20 option B is one kwarg on `assemble`, not a signature change on `score_v2`.
- FACT `pit_rawprice.PRICE_BASIS_RAW == pit_shares.PRICE_BASIS_RAW == 'raw_as_printed_unadjusted'`; `pit_price_basis.PRICE_RAW_AS_TRADED == 'price_raw_as_traded'`; `pit_price_basis.validate()` (:830-831) only tolerates the two spellings.
- FACT `pit_eps_cohort.load_ttm` reads 7 columns and `EntityTtm.select` returns 6 keys -- neither `method` nor `sign_crossing`, though `pit_eps_ttm` has both (pit_eps.py:592, :599).
- FACT `candidate_v2_registry()` calls no validator (probe); the per-spec validator (pit_normalization.py:1555-1600) only runs through `register_factor` (:1608). For BENCHMARK_ANCHORED it refuses only `ANCHOR_NONE`/`ANCHOR_ZERO` and requires a scale, so a median anchor string passes -- but `COHORT_ANCHORS` (:1455) would not call it cohort-scoped.
- FACT `pit_factor_blocks.validate()` :964-965 pins `SUBFACTOR_DIRECTION_V1['ev_ebitda_supplement'] == LOWER_IS_BETTER` and `pit_frozen_spec.validate()` consults it.
- FACT the pilot DB is built from `pit_store.SCHEMA` (pit_replay_meter.py:1354-1357), so `pit_listing` exists there; both `pit_feature.listing_id` (:469) and `pit_score.listing_id` (:538) reference it. `run_pilot` exposes `report[\"seed\"]` (pit_replay.py:1724).

## 2. Corrections to the steps (per_step verdicts)

1. **E7 REFUTED (shape).** `validate()` reads `case[\"expect\"]` unconditionally (pit_replay.py:2024) and `_golden_eval` KeyErrors on a `pairs` witness (:1907-1913) and on any factor not in RAW_FIELD (:1917). PROPOSAL: give every witness an `expect` key (a number, `REFUSED:<code>`, or for the benchmark `{\"anchor\": 0.17, \"band\": 3}`) and branch validate() on `case.get(\"kind\")`; extend `_golden_eval` to plant `(eid, tag, 0)` instants and `(eid, tag, 4)` durations naming the exact tags/rung; the witness for total_debt must say which rung it plants (SUM of three vs the single-tag rungs) because under OD-06 B the gate decides the witness's outcome.
2. **D7 REFUTED (pin).** KNOWN_LIMITATIONS is 7 entries (not 8, as A8 and section 8 say); after B8 and D7 it is 5, below `>= 6` at test_pit_replay.py:246-248. Re-pin at D7 or keep the count with the surviving limitations.
3. **D1 / D6 REFUTED (loader).** `method` and `sign_crossing` are not loaded; extend `load_ttm` + `EntityTtm` (and re-run `validate_against_selector`) or fall back to `ttm_eps_as_of_detail` per entity. OD-20 option A as written always yields NULL.
4. **A7 (ordering).** `_REGISTRY` (:1024) is defined after `FACTOR_PLAN` (:441); the floor must be attached in a post-pass and added to `as_dict()`. Also `insufficient_peers` is already `eligible_peers`' own floor reason (pit_factor_spec.py:312) -- one string, two stages: name it under OD-04.
5. **B4 (snippet).** Add `rationale=` or test_pit_normalization.py:555 fails; nothing else validates a registry entry at build.
6. **B8 / D2 (one resolver).** `market_cap_as_of_detail` takes `roll_back_days` too (:1156); B8 omits it. OD-18 governs both prices; one resolver, one memo per (entity, date), one basis translation (item 7).
7. **B9 / D2 (second spelling).** Declare the `raw_as_printed_unadjusted -> price_raw_as_traded` mapping beside SPEC_PRIMITIVE_PRICE_BASIS_V1 (or a `basis_of(record)` helper with a test); D3's literal `price_basis=PRICE_RAW_AS_TRADED` is otherwise an assertion the engine makes about a record it did not check. Whether this second mapping is digested is part of OD-11.
8. **C1 (omission).** The EMBEDDED_IN_TRANSFORM option also edits pit_factor_blocks.validate() :964-965 (and its test). Under OD-13 that is a second body plus a pin, not one body.
9. **A5 (reason for n_band == 0).** The helper returns `why=None` when M_s is None, so an EMPTY band writes `benchmark_unavailable` while a band of 1-2 writes `band_too_thin`. Decide the code for n_band == 0 inside OD-03.
10. **E1 line cite.** REASON_* constants are at pit_frozen_spec.py:98/:100/:173/:220 (not 89/91). **D4 test cite** is pit_replay.py:1956-1963 (the test file ends at 1380). **E3** 'freeze count text' -- no such text found in the COMPONENTS comments.
11. **B3 + B6, C2 + C5, D-steps + D7 are single commits** because of validate :1966-1968; the plan's 'one v6 cut before B' (OD-25 A) then means: cut commit = E1-E7 + B4 + B5 + B9 (+ C1/PEER_FLOOR/ACTION_GATE bodies if decided) with RAW_FIELD entries and plan flips landing together in the SAME commit as their reason constants, or `test_validate` is red in between.

## 3. Owner decisions surfaced by this review (not chosen here)

- OD-03 sub-question: the refusal code when the band is EMPTY (n_band == 0) and when pairs < BENCHMARK_MIN_COHORT_V1 under a two-stage floor.
- OD-04 sub-question: whether the engine floor may reuse `insufficient_peers` (eligible_peers' own code) or needs a distinct name.
- OD-09: add the option 'successor eligibility rules matched at P (SPECS_V5)' -- the v4 precedent for exactly this gap -- or record adoption of the interest_coverage 'UPPER BOUND' convention (pit_factor_spec.py:606-611) for the three Q rules.
- OD-11: does the basis-string translation (pit_rawprice spelling -> pit_price_basis spelling) go into the digested SPEC_PRIMITIVE_PRICE_BASIS_V1 or stay engine glue?
- OD-13: the EMBEDDED relabel moves SUBFACTOR_DIRECTION_V1 (digested) AND pit_factor_blocks.validate()'s pin; the tanh option needs a scale in multiple units (the registry default is the margin k 0.06, pit_normalization.py:749); the PERCENTILE_RANK option contradicts SUBFACTORS (:463-466) and FORMULAS_V1 (:1309-1311), both digested.
- OD-18 must cover the market-cap price for dmc/ev, not only the P/E price.
- OD-20 A as coded needs the loader change in item 2.3; OD-21's method reporting likewise.

## 4. Fixtures, tests and guards not covered (also in `missing`)

- Synthetic-index tests for `_level_at` (SUM rung complete / incomplete; duration rung), for the rung codes `dr`/`db` and `source_tag == rung key`, for each `debt_gate` refusal under OD-06 B, for the `FactorPlan.as_dict` round trip with the new fields, and for the unchanged `normalise()` calls at test_pit_replay.py:795-826 once `benchmark=` is added.
- The oracle's fixed key tuple (test_pit_replay.py:1281-1284) must gain every new per-date statistic.
- A pit_replay_rss measurement gate BEFORE B8 (the share audit runs in `_priced_and_defensible` per member per peer set AND in `market_cap_as_of_detail` per needed entity), with a memo shared between them named as a step.
- Under SPECS_V5 (OD-04 C / OD-09 / OD-11 B): `spec_versions()` :1464-1467 and `SPEC_SETS` need the v5 entry; pit_frozen_spec.validate :1200-1202 and test_pit_factor_spec.py:650-652 re-pin.

## 5. Prose sites the ledger omits

pit_replay.py:9; pit_frozen_spec.py:900 (undigested `engine_uses ... pit_replay/1.1`); docs/POST-REBOOT-REPORT-2026-09-22.md:375-376; docs/MODEL-LINEAGE.md:1241 and :1298; docs/D3-EVIDENCE-2026-09-22.md (spec_freeze_v5 mentions). None moves the digest; all read stale after the cut.