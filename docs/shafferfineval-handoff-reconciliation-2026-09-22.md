# ShafferFinEval handoff reconciliation — 2026-09-22

Status: **REPOSITORY-GENERATION MISMATCH. Nothing built. No replay started.**

## Finding

The ShafferFinEval described in the 2026-09-22 handoff (spec_freeze_v1/v2/v3,
EVGQ company model 0.35E+0.25V+0.15G+0.10Q at ±85 with ±15 sector overlay,
PIT store, streaming replay engine, `shafferfineval_pilot.db`) does not exist
in any repository reachable from the cloud session.

The only ShafferFinEval code on GitHub is the **v1 generation** on
`origin/claude/market-signal-engine-328vyy` (last commit `acb3d76`,
2026-09-20 20:11 UTC):

- `company_scoring.py`: `CompanyRawScore = 0.40V + 0.25G + 0.20P + 0.15D`,
  scaled 0.75 to ±75, plus a ±25 sector overlay (VGPD, not EVGQ).
- Model versions: `equity_model_v1`, `sector_model_v1`, `hedge_model_v1`,
  `return_calibration_v1_0.20`, `gpi_v1`.
- Live Yahoo/FRED data, SQLite with 18 tables, no `pit_*` tables.
- The 50th–75th percentile EBITDA cohort exists (`build_ebitda_peer_cohort`)
  but is a **valuation** primitive (winsorized cohort EV/EBITDA → implied
  price → tanh gap). The handoff's `ebitda_benchmark` is an **operating-
  strength** factor in E. Same cohort construction, different economic
  question — the likely origin of the reported two-definition ambiguity.
  Not confirmable without spec_freeze_v3 itself.
- ROA is used in v1's P block (0.35). The handoff's v2 refusal of ROA/ROE is
  not reflected in any pushed code.

Zero hits across all 15 remote branches for: spec_freeze, digests
7a844dca / 5219bdd5 / 2f9bba31, EVGQ, can_execute_replay, pit_feature,
pit_replay_run, ebitda_benchmark, MIN_BLOCK_WEIGHT, PE_ABSOLUTE_ANCHOR,
DEBT_RUNG, CoverageClaim, SURVIVOR_ONLY, V_FACTOR_SUBFACTORS,
eps_transition_from_pair, shafferfineval_pilot.

FinSim exists on `origin/finsim-standalone` and
`origin/claude/finance-simulation-game-y0nkv0`; the two-book split
(Actual Thesis Book / Shaffer Score Test Book) is not present there.

## Environment measured

Cloud Linux VM, not the Windows machine in the handoff: 15 GiB RAM, no swap,
30 GiB free of 252 GiB, Python 3.11.15, SQLite 3.45.1. No pagefile, no pilot
DB, no main PIT store. No system or pagefile changes were made.

## v1 test baseline (branch extracted to scratch, scripts run directly)

Pass: test_company_scoring, test_multi_asset, test_scoring, test_sector_scoring.
Fail (environmental only — universe workbook not checked in): test_hedging,
test_pipeline_growth, test_prediction_ml, test_research, test_terminal.

## Decision (user, 2026-09-22)

Treat every discrepancy as an environment/repository-generation mismatch until
the real v2 source is provided. Do not modify v1 to resemble the handoff,
create freeze records from prose, implement EVGQ, reconstruct PIT modules,
start replay work, or infer missing v2 behavior. When v2 is available, repeat
verification against the actual files; the repository, not the handoff, is
authoritative where they differ.
