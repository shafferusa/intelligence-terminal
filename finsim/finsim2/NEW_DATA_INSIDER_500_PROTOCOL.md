# Insider activity on the expanded universe: fresh-sample re-test protocol

This protocol was fixed on 2026-09-28. It was committed before the run. The code is `engine/newinfo.py` (the
unchanged stage-1 engine with a sample filter), and the command is `python -m finsim2 lab --insider-500`. The report
is `NEW_DATA_INSIDER_500.md`.

## Why

In stage 1 (`NEW_DATA_SIGNALS.md`), the insider family (SEC Form 4 open-market buying and selling) was tested on the
original research universe, which has 45 stocks with Form 4 history. It showed no incremental value:
- best Alpha Δ rank IC +0.019 at 6M (t 0.3);
- best Directional Δ Brier +0.00012 at 1W (t 1.2);
- hedge improvement 3.9% at 12M (t 1.4).

Forty-five stocks is a small cross-section for a ranking signal. So the question is whether a real insider effect was
too small to see there. The universe has since grown to about 500 stocks.

## The sample: new stocks only

The re-test uses **only the stocks added by the universe expansion** (`meta.expanded`, 2026-09-27). A stock is
included when it has:
- a CIK;
- Form 4 history;
- signal-level research records (lab records, version `2.1s`).

The 45 stage-1 stocks are excluded. Stage 1 already used them, and re-using them would make this a re-analysis of
the same data, not a replication. About 400 stocks qualify. The run reports the exact count.

## Held fixed (identical to stage 1)

- **Features:** `ins_net_63`, `ins_buy_126`, `ins_officer_buy_126`, `ins_buyers_126`, `ins_sell_z`.
  - Open-market P / S transactions only, original filings only.
  - Point in time: the filing date, used from the next session.
- **Targets and baselines.** Nothing is re-tuned. Each target is judged against the frozen benchmark on identical
  records:

  | Target | Test |
  |---|---|
  | Alpha | Paired Δ cross-sectional rank IC vs the production score |
  | Directional | Gate v2 vs the prior-only model and the current formulation |
  | Hedge | Squared error of the log realised-volatility forecast vs the 63-day / 21-day realised-vol + VIX baseline |

- **Horizons:** 1D, 1W, 1M, 3M, 6M, 12M.
- **Walk-forward:**
  - Eras: fit to 2008 → test 2009–12; to 2012 → test 2013–16; to 2016 → test 2017–20; to 2020 → test 2021–24;
    to 2024 → test 2025–.
  - Plus the 2018 split.
  - Weights and standardisation are frozen before each era.
- **Gates:**
  - G1: walk-forward t ≥ 2 in the right direction.
  - G2: at least 3 of the 4 complete eras, and the split.
  - Benjamini-Hochberg FDR at q = 0.10, over this run's tests only: 1 family × 6 horizons × 3 targets.

  Stored under its own key (`lab:newinfo:b3`), so it neither borrows from nor dilutes the stage-1 correction.
- **Class / hierarchical priors** in the Directional benchmark are computed from this sample's own matured outcomes,
  the same way stage 1 computed them from its sample.

## Decision rule

**No test passes.** Insider activity has no detectable incremental value on 400 stocks either. It stays out of every
Shaffer model, and there is no further free-data insider testing.

**A test passes G1, G2 and the FDR.** This is a discovery on a fresh sample, not a confirmation. Stage 1 found
nothing to confirm. The passing (family, horizon, target) cell would go into live shadow under its own frozen spec
and a fixed live gate. Production is unchanged. Promotion would need the live gate and the user's explicit approval.

**A near miss** (t ≥ 2 without FDR, or FDR without G2) is reported as a near miss and not acted on.

In every case the result is also reported on the combined 45 + 400 sample, for information only: that sample
re-uses the stage-1 stocks, so it is not part of the decision.

## Run log

**2026-09-28 13:01 — first run, invalid, discarded.** The run finished ("no incremental value"), but a coverage
check showed the data behind it were incomplete:
- **The gap.** The expanded stocks' Form 4 history began in May 2026: a median of 27 insider rows per stock,
  against 1,467 for the stage-1 stocks, and `ins_sell_z` was present on 1% of records.
- **The cause.** A state bug in `secevents.refresh_insider`. When the new-issuer record was introduced, the stores
  from before it counted every issuer as covered. That included the stocks added by the expansion, which only had
  their recent filings. So the quarterly history was never read for them. The combined-sample run was stopped.
- **The fix.**
  - Coverage is now judged from the stored rows (`recheck`), with a test.
  - The 2006 → 2026 quarterly history was backfilled for those issuers.
  - An inclusion guard was added before the rerun: a stock counts as having Form 4 history only with insider rows
    before 2025-01-01, and the run is refused when fewer than 80% of the expanded candidates have it.
- **What did not change.** The features, targets, horizons, eras, gates, FDR scope and decision rule are unchanged.
  The rerun is the identical protocol on complete data. The first run's numbers are not reported as a finding.

**2026-09-28 rerun: the result.** Coverage was 370 of 403 expanded stocks (92%) with Form 4 history before 2025, and
`ins_sell_z` was present on 92% of records.
- **Scope.** There were 17 tests, not 18: the hedge (volatility) test runs from 1W, the same as in stage 1.
- **Fresh sample: NO INCREMENTAL VALUE.** None of the 17 tests passed G1, G2 and the FDR.
  - Best Alpha Δ rank IC: −0.017 at 6M (t −0.6).
  - Best Directional Δ Brier: −0.0007 at 12M (t −0.5).
  - Best hedge: +0.8% of the baseline's error at 12M (t 1.2).
  - Adding the insider features made short-horizon ranking worse (1D t −8.2, 1W t −3.9). Stage 1 showed the same
    pattern for every family at 1D.
- **Combined 45 + 370 (information only): NO INCREMENTAL VALUE.**

**Decision (the pre-registered rule).** Insider activity stays out of every Shaffer model, and free-data insider
testing stops here. Report: `NEW_DATA_INSIDER_500.md`.

