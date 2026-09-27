# How big will the move be? Shaffer Directional 1D – 1W move size — pre-registered protocol (stage 3)

Fixed 2026-09-27, **before any result was computed**. Research only; the Directional model (prior-only P(up)) and every
production system are unchanged. Code: `engine/movesize.py`. Run: `python -m finsim2 lab --move-size`. Report:
`SHAFFER_MOVE_SIZE.md`.

## Why this question

The owner asked for "the best indicator of a stock's movement, by how much". Stage 1 (NEW_DATA_SIGNALS.md) found no new
information that beats the point-in-time prior for the **direction** of 1D – 1W moves, so P(up) stays the prior-only
model. It did find that the event calendar and 8-K event types improve **volatility** forecasts at 1W and 1M — the
"by how much". This study builds the move-size forecast for 1D and 1W and tests whether the event information makes it
better than a strong standard baseline.

## Records

Every asset with ≥ 6 years of prices in the research store (equities, ETFs, indices, Treasuries, corporate bonds,
commodities, FX, crypto), from 2001: 1D every other session, 1W every 5 sessions (non-overlapping outcomes). Features
known at the close of t:

- **base (M0)**: log RMS of daily log returns over 5, 21 and 63 sessions, log VIX, log |today's return|, asset-class
  dummies
- **calendar**: scheduled macro release (CPI, jobs, GDP, PPI, retail, FOMC) on the next session and FOMC on the next
  session — only when announced by t —, scheduled releases / FOMC in the next five sessions, and for stocks: earnings
  expected within 5 / 21 sessions and sessions since the last release (estimated from past 8-K 2.02 filings only)
- **8-K**: the sec_events family (stocks)

Target: log realised volatility over (t, t + h] (RMS of the daily log returns; floored at 1e-4).

## Models and evaluation

Pooled OLS with a small ridge, trained only on outcomes that matured before each test era; the level is calibrated on
the training records (mean forecast variance = mean realised variance). M0 base · M1 + calendar · M2 + 8-K ·
M3 + calendar + 8-K. 90% range = forecast σ × the 5% / 95% quantiles of the training outcomes standardised by their own
forecast (calibrated, not assumed normal). Walk-forward eras 2009–12, 2013–16, 2017–20, 2021–24, 2025– and the 2018
split. Loss: QLIKE of the variance forecast against the realised mean squared return; gain = QLIKE(M0) − QLIKE(model),
averaged per date, t over dates (records do not overlap).

## Gates (per horizon × model M1–M3)

- **G1** walk-forward QLIKE gain > 0 with t ≥ 2
- **G2** gain > 0 in all but one of the complete eras (≥ 3 complete eras, 2025– excluded), split t ≥ 1
- **G3** out-of-sample 90% range coverage between 87% and 93%
- **FDR** Benjamini-Hochberg q = 0.10 across the 6 tests

A passing model is the move-size forecast in the research display (the best passing model; otherwise M0, labelled as
the baseline). It reaches the app's live display only with the owner's approval.
