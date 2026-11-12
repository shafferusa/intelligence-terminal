# News signals (WSJ · Barron's · MarketWatch) — pre-registered protocol

Fixed **2026-09-28, before any news data was ingested or any result computed**. Research only. Code:
`data/news.py` (ingestion, point in time, ticker linking, features `lex1`, daily aggregation) and `engine/newslive.py`
(this evaluation). The daily learning loop runs the evaluation after each close; `python -m finsim2 news --status`
prints it. The result lives in the store key `news:eval`.

## Research only

- News features never enter the Shaffer Score, the Shaffer Hedge, the Directional or move-size models, or any other
  production output. They are stored (`news_articles`, alt_data `news_dj`) and evaluated **forward only**, on data that
  FinSim2 collects from 2026-09-28 on. There is no historical backfill (the public feeds carry only the latest items,
  and archive crawling is not allowed).
- A passing result here does **not** promote anything. Promotion would need a separate live-shadow gate, written and
  fixed before it runs, and then the owner's explicit approval.

## Data and point in time

- Sources: Dow Jones' official public RSS feeds on `feeds.content.dowjones.io` (WSJ and MarketWatch, one GET per feed
  per run, no pagination, no log-in), user-added feed URLs on that host only, and articles the user imports by hand
  (`wsj.com`, `barrons.com`, `marketwatch.com`). The old host `feeds.a.dj.com` is frozen and never used.
- `known_at` = the time FinSim2 first saw the article (the feed fetch, or the manual import). The publisher's
  `published_at` is stored but **never** used for timing: it can be earlier than when FinSim2 could have acted.
- An article belongs to the **session** whose close is the first close at or after `known_at`, in New York time:
  known before 16:00 on a trading day → that day's session; at or after 16:00, or on a weekend or holiday → the next
  trading session.
- The daily loop fetches the feeds after the close (the scheduler refreshes from 17:30 New York), so a feed item is
  normally first seen after 16:00 and belongs to the **next** session: headlines fetched on the evening of day d are
  features of session d + 1 and are tested on returns from the close of d + 1. The reaction during session d + 1 itself
  is outside the outcome window by construction. Running `python -m finsim2 news` during the trading day assigns what
  it finds to that day's session.
- Feed items published more than 3 days before the fetch are not ingested (they are old news, not news for the
  session they would land in). A manual import of an article already ingested from a feed is kept for display but is
  not counted again (`dup_of`).
- Features per asset-session (`news_dj`, published = the session date): `n_articles`, `n_sources`, `sent_mean`
  (link-confidence-weighted mean of the article sentiment `sent`), `sent_title_mean`, `abs_sent`, event-tag counts
  `ev_*`, `novelty` = n_articles / (1 + mean n_articles over the previous 20 sessions). The move-size forecast of the
  same session (`ms_sigma_1d`, `ms_sigma_1w`, and `p_up_1d` when stored) is snapshotted for every asset that has one,
  with or without news, only when the move-size snapshot's date equals the session.
- Outcomes: log returns from the **close of the feature's session** t to the close of t + 1 (1D) and t + 5 (1W) on
  the SPY trading calendar, adjusted closes. Excess return = asset log return − SPY log return over the same window.
  Only matured outcomes are used.

## Minimum data before any test

All three must hold, otherwise the status is **ACCUMULATING** (with the progress counts) and no test is run or shown:

1. ≥ 126 sessions of ingestion (sessions in which at least one article was ingested, linked or not);
2. ≥ 2,000 asset-sessions with news (n_articles ≥ 1);
3. ≥ 60 sessions with news on ≥ 20 assets.

## Tests

Benjamini–Hochberg at **q = 0.10** across the five tests below (T1-1D, T1-1W, T2-1D, T2-1W, T3). p-values are
two-sided unless stated. A test **passes** when it is BH-significant and meets its own condition.

- **T1. Sentiment rank IC.** For each session with news on ≥ 20 assets whose outcome has matured, the Spearman rank
  correlation across those assets between `sent_mean` and the next 1D (T1-1D) and 1W (T1-1W) log excess return vs
  SPY. Statistic: the mean of the daily ICs; t = mean / (sd / √n_sessions), divided by √5 at 1W for the overlapping
  windows. Normal p-value. Condition: BH-significant (the sign is reported; a negative IC is a finding, not a pass
  for a long signal).
- **T2. Move size.** Does news intensity predict the size of the move beyond the stored move-size forecast?
  Asset-sessions with a move-size snapshot and a matured outcome. News day = n_articles ≥ 1 and (novelty ≥ 1 or any
  event tag). Loss: QLIKE of r² (the asset's own squared log return over the horizon; |r| floored at 1e-4, as in
  `engine/movesize.py`) against ms_sigma² × scale. Sessions are split in time into a first and a second half. On the
  first half, fit a common scale (mean r²/σ² over all rows) and a separate scale for news and for no-news rows (the
  QLIKE-optimal constant is the mean of r²/σ² within the group). On the second half, the gain per row is
  QLIKE(common) − QLIKE(news / no-news); rows are averaged within each session and t = mean / (sd / √n_sessions),
  divided by √5 at 1W. T2-1D and T2-1W. Condition: gain > 0 and t ≥ 2 (and BH-significant, one-sided p from t).
- **T3. Sign hit rate.** Over asset-sessions with news, `sent_mean` ≠ 0 and a matured, non-zero next 1D excess
  return: the share where the signs agree, against 50%, exact two-sided binomial test (normal approximation above
  20,000 rows). Condition: BH-significant and hit rate > 50%. Rows on the same session are correlated, so this is the
  least robust of the tests; it is reported, and it cannot pass on its own without T1-1D pointing the same way.
- **Reported, not tested:** for each event tag, the mean next 1D and 1W excess return over asset-sessions carrying
  the tag, with the count.

## Status labels

- `ACCUMULATING` — below the minimum data; progress counts only.
- `NO EVIDENCE` — tested; nothing passes.
- `EVIDENCE — RESEARCH ONLY` — at least one test passes. Still research only (see above).

## What would change this protocol

Nothing that uses the results. A new feature version (`lex2`, a different linker) or a different test needs a new,
separately dated protocol; this one and its results stay as they are.
