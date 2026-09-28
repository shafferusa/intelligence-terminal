# Analyst expectations from Eulerpool — pre-registered protocol

Fixed 2026-09-29, **before any result was computed and before the data were seen** (they exist only on the owner's
machine). Research only: the Shaffer System, the Hedge and every production output are unchanged.
Code: `engine/newinfo.py` (batch `ep`). Run: `python -m finsim2 lab --eulerpool`. Report: `SHAFFER_EP_RESEARCH.md`.
Data: Eulerpool free tier (non-commercial; "Data by Eulerpool" on anything public), `data/eulerpool.py`.

## Why

Analyst expectations are the input the data list ranks first for Shaffer Alpha, and FinSim2 has never had a history
of them. The first collection (2026-09-28) found two Eulerpool datasets with real depth:

| Dataset | Depth (first collection) | Used here |
|---|---|---|
| `ep_surprises` — EPS actual vs the analyst consensus per fiscal quarter | 500 stocks, 1994 → | yes |
| `ep_grades` — dated rating actions (upgrade / downgrade / initiation) | 485 stocks, 2012 → | yes |
| `ep_estimates` — point-in-time consensus snapshots | 373 stocks, 2026-05 → | no: too short. A revisions family is pre-registered once 12 months are collected (from 2027-05) |
| `ep_targets` — price-target snapshots | 484 stocks, 2026-03 → | no: too short (same rule) |

Earlier results this is judged against, stated so the reader can weigh it: the SEC-based earnings surprise (SUE against
the company's own history) showed NO INCREMENTAL VALUE; the price-reaction earnings-event family (post-earnings drift)
is SHADOW. A consensus surprise measures something different — the gap to what analysts expected — and the
literature finds a post-announcement drift in it; rating changes have a documented short-lived drift.

## Point in time

- **Surprises**: a quarter becomes visible on the day after the company's earnings release (the first SEC 8-K item
  2.02 filing within 120 days of the period end); without one (before August 2004 or missing), 45 days after the
  quarter end, 90 after fiscal Q4. Quarters not yet reported are excluded (the 2026-09-29 fix).
- **Rating actions**: visible the day after the action date.
- Known limits, reported and not corrected: the consensus is Eulerpool's figure for that quarter (not verifiable
  as the last pre-release consensus), and the universe is today's listed stocks (survivorship bias, as in every
  FinSim2 study).

## Families (equities; newinfo batch `ep`, own FDR)

| Family | Features (all from values visible at t) | Track |
|---|---|---|
| `consensus_surprise` | `sur_pct` = (actual − consensus) / max(\|consensus\|, 0.05), clipped to ±2, for the latest released quarter; `beat_rate_4q` = share of the last four released quarters with actual > consensus; `sur_pct_chg` = `sur_pct` minus the previous quarter's. Each value is held for 63 sessions after its release and is missing afterwards (the drift window) | historical |
| `analyst_grades` | `net_grades_21`, `net_grades_63` = upgrades − downgrades over the last 21 / 63 sessions; `grade_activity_63` = ln(1 + upgrades + downgrades + initiations over 63 sessions). 0 once the stock's coverage has begun (its first recorded action), missing before | historical if it covers ≥ 3 complete eras, else INSUFFICIENT DATA |

Scale-free by design: the surprise is a ratio of two figures on the same share basis, so stock splits cannot distort it.

## Tests and gates

Exactly those of `engine/newinfo.py` (module docstring, fixed 2026-09-25), against the frozen benchmark: Alpha
(Δ rank IC on top of the production score; G1 t ≥ 2 with Δ IC ≥ 0; G2 ≥ 3 eras won and 2018 split t ≥ 1), Directional
gate v2 (vs the prior-only model and the current formulation), Hedge (log realised volatility vs σ63 / σ21 / VIX);
enough data = ≥ 3 complete eras (≥ 200 records each) and ≥ 2,000 walk-forward records. Eras 2009–12, 2013–16,
2017–20, 2021–24 (2025– reported, never counted); horizons 1D, 1W, 1M, 3M, 6M, 12M. **Benjamini-Hochberg q = 0.10
over this batch's own family × horizon × target tests.** SHADOW if any test passes G1, G2 and FDR; else NO
INCREMENTAL VALUE.

## What a pass means

A passing family becomes a research display and a live-shadow candidate. It enters no Shaffer model without the
owner's approval and a revision entry in `SHAFFER_SYSTEM.md` §5. Using Eulerpool-derived features in anything shown
publicly requires the attribution; commercial use requires a paid Eulerpool licence.

## Runs

The first run of `lab --eulerpool` on the owner's machine is the confirmatory one; later runs are re-runs.
