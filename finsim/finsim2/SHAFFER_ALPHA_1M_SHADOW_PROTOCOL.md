# The 1M Alpha challenger in live shadow: protocol

This protocol was fixed on 2026-09-28. It was committed together with the frozen weights, before any live row was
recorded. The code is `engine/ahzlive.py` and the frozen model is `frozen/alpha_hz_1m.json`.

**Research only.** The challenger is never shown as a Shaffer Score, never feeds a hedge or a trade, and is never
promoted automatically. A promotion would need this gate and an explicit decision by the user.

## Why

In stage 2 (`SHAFFER_ALPHA_HORIZONS.md`), only one horizon was worth watching: 1M. There, the horizon / sector ridge
(V*) beat production's cross-sectional rank IC:

| Where | Δ rank IC | t | Notes |
|---|---|---|---|
| All complete eras (4 of 4) | +0.0118 | 1.13 | 212 monthly dates |
| Original 45 stocks | −0.0035 | | |
| Post-2018 split | +0.029 | 1.61 | |

This is not validated: G1 needs t ≥ 2, G3 needs the original stocks positive, and it did not pass the FDR. Refitting
on the same history cannot answer the open question. Only data no fit has seen can: does the effect persist forward?

## What is frozen

- **The model:** V* at 1M, fitted once on every matured stage-2 record (449 stocks, 1995 onward).
  - Nested choice from the last era: depth 4 (global → horizon group → horizon → sector), K = 10.
  - Eleven family features (`alphahz.feature_spec`).
- **The file:** `frozen/alpha_hz_1m.json` holds:
  - the weights of every node a 1M score can use;
  - the feature RMS;
  - the backtest expectation (Δ, t, dates, original-stock Δ);
  - a SHA-256 hash of all of the above.
- **Load checks.** Loading fails if the hash does not match, or if the family features have changed since the freeze.
- **No refits.** Any change is a new model, with a new id and a fresh live clock.

## What is recorded

**When.** On the first daily run of each ISO week (the same day as the live cross-sectional panel), and for every
research stock (at least six years of prices).

**What the ledger holds.** Model `alpha-hz:1m`, version `alpha-hz-1m-vstar@<hash>`. Each row is appended to the
prediction ledger with:
- the raw score;
- the within-date percentile;
- the sector;
- whether it is an original or an expanded stock.

Rows are graded at 21 sessions like every forecast, and are never rewritten.

**Stocks the freeze did not see** (added after 2026-09-28) use their sector's weights, because depth 4 has no stock
nodes.

**Production's rows.** Production's 1M forecast ("shaffer") on the same date comes from the weekly live panel and the
daily loop. The shadow job also adds production rows for any stock whose production sweep is already cached. It
never runs new production sweeps: one uncached sweep takes about two minutes per stock.

## The statistic

For each graded week:
- the challenger's cross-sectional rank IC over every stock it scored;
- on the stocks where production also has a 1M forecast (at least 60), both rank ICs and
  Δ = IC_challenger − IC_production;
- the same Δ on the original stocks (at least 20 common).

Weekly 1M outcomes overlap: 21 sessions, sampled every 5. Every t-statistic and interval is therefore widened by
√(21/5) ≈ 2.05.

## The gate ("the effect persists")

All of these must hold:

| | Condition |
|---|---|
| a | At least 52 graded weeks with a production comparison |
| b | The challenger's mean live IC > 0 |
| c | Mean live Δ ≥ 0 (not worse than production) |
| d | Consistent with the backtest: z = (live Δ − 0.0118) / (backtest SD × √(21/5) / √weeks) ≥ −2 |
| e | Mean live Δ on the original stocks ≥ 0 (the backtest's weak spot) |
| f | The decay alarm never fired: a one-sided CUSUM on Δ, reference half the backtest Δ, threshold 5 × backtest SD × √(21/5) |

**Labels:**
- **ACCUMULATING** until (a) is met.
- **EFFECT PERSISTS LIVE (research only)** when all six hold.
- **EFFECT NOT PERSISTING** when (a) is met and any other check fails, or as soon as the CUSUM alarm fires.

**"Better than production" is a separate question.** It is reported every week as the accumulated live Δ with an
overlap-adjusted 95% interval. It reads DEMONSTRABLY SUPERIOR LIVE only when the whole interval is above zero.

At the backtest's effect size (+0.012 with a per-date SD near 0.15), a t ≥ 2 answer needs roughly
4 × SD² × 4.2 / Δ² ≈ 2,800 weekly observations, which is decades. The report prints this number (`weeks_needed`).
The honest reading is that the live shadow can reject the effect, or show it is still consistent. It cannot prove
superiority within any useful time.
