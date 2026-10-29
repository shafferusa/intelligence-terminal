# FinSim - FinSim1, the paper-trading terminal, and FinSim2

This folder holds two separate applications:

- **FinSim1, the FinSim Terminal** (`finsim/terminal`, this page): a paper-trading terminal for an institutional fund.
  It simulates trading, execution, settlement, custody, prime brokerage, collateral, margin and double-entry accounting
  on real delayed market data, with fictional capital. Every execution is a simulated fill: nothing is ever sent to a
  broker or an exchange, and no fill is presented as a real exchange execution. FinSim1 has no news, research,
  recommendations or market commentary (those pages and their background jobs were removed in 1.0.5 and 1.0.6).
- **FinSim2** (`finsim2/`): a separate quantitative research application, described in its own section below. FinSim1
  never changes it.

## Start FinSim1

From PowerShell or a command prompt; both folders work and do the same thing:

```powershell
cd C:\Users\logan\intelligence-terminal\finsim
python -m finsim serve
```

```powershell
cd C:\Users\logan\intelligence-terminal
python -m finsim serve
```

The server answers on http://127.0.0.1:8765/ only (this computer). Open that address in a browser, or run
`python -m finsim open` in another window for the app window. `Ctrl+C` stops a server started with `serve`. When FinSim
already runs (the installed app starts it at login), `serve` refuses with exit code 3 and says which version and folder
run and how to open it; `python -m finsim status` shows the same.

- **Python.** On this computer `python` is 3.12.10 and `py -3.12` is the same interpreter; `py` and `python3` (3.14.5)
  also work. The installed app always uses Python 3.12 (`pythonw.exe`, no window).
- **FinSim.cmd.** The same small file sits in `C:\Users\logan\intelligence-terminal` and in its `finsim` folder and starts
  FinSim1 from any folder. Without a command it runs `open` (starts the server when needed and opens the app window);
  otherwise it passes the command on: `FinSim.cmd serve` in a command prompt, `.\FinSim.cmd serve` in PowerShell. It uses
  `py -3.12` when the py launcher has 3.12, else `python`, and returns the command's own exit code.
- **No command.** `python -m finsim` alone prints a three-line hint (`serve`, `open`, `status`) and exits 0.
- **Every day** you use the installed app (see "The installed app" below): the FinSim icon or Windows login starts the
  server, so nothing has to be typed.

## The other application in this folder: FinSim2

FinSim2's own section follows as its authors wrote it. It is a separate application with its own package, data and
launcher (`python -m finsim2`); FinSim1's sections continue after it.

### FinSim2 — portfolio & quant analytics

`finsim2/` is a separate app: a quantitative evidence engine for managing a real portfolio. It is not a trading
simulator. It keeps real daily market history and runs the equation library on it. It tests which signals have
predicted which assets at which horizons, trains simple models walk-forward, and shows that evidence next to your
holdings. You decide what to buy or sell. Design notes: `finsim2/ARCHITECTURE.md`.

```
python3 -m finsim2 install            # once: FinSim2 as a local app — server starts at login, "FinSim2" shortcut on the
                                      # desktop / Start Menu that opens it in its own window (no address to type)
python3 -m finsim2 open               # starts its server (port 8865) if needed and opens the window
python3 -m finsim2 refresh [--full]   # download / update the market history in this terminal (2–4 min the first time)
python3 -m finsim2 research NVDA SPY --ml   # compute the evidence (and train the ML models) from the terminal
python3 -m finsim2 audit [--workers 3]      # replay the Shaffer Score and ML over the universe -> finsim2/SHAFFER_AUDIT.md
python3 -m finsim2 hedge-audit              # walk-forward Shaffer Hedge evaluation + ML-adjustment training -> finsim2/HEDGE_AUDIT.md
python3 -m finsim2 serve | status | stop | phone on | uninstall
python3 -m finsim2 data [sec calendar cftc eia crypto news] [--force] [--status]   # new data sources (finsim2/NEW_DATA_SOURCES.md)
python3 -m finsim2 news [--status]          # research-only news: fetch the public WSJ / MarketWatch RSS now, aggregate, feed status
python3 -m finsim2 news add --url URL --file article.txt | show NVDA | feeds [--add URL | --remove URL]
python3 -m finsim2 universe --expand 500    # add the most liquid US common stocks (then: refresh, data sec --force)
python3 -m finsim2 import-estimates FILE | import-options FILE   # licensed analyst estimates / option history (CSV)
```

**Data.**
- Research store: `~/.finsim2/research.db` (SQLite; `FINSIM2_HOME`, `FINSIM2_DB`, `FINSIM2_PORT` override).
- Asset list: about 130 assets across equities, ETFs, indices, Treasuries, corporate credit, commodities, futures,
  FX and crypto. Any Yahoo symbol can be added.
- Daily history comes from Yahoo Finance and covers each asset's full history (SPY from 1993).
- 19 macro series come from FRED. With `FRED_API_KEY` set, the revised monthly/weekly series (CPI, unemployment,
  industrial production, M2, NFCI, the Fed balance sheet) are first-release values used from their actual
  publication dates, so revisions never leak into history. Without a key they are today's revised values used
  after a fixed lag, and NFCI (re-estimated weekly) is left out; each research bundle's `data_notes` says which.
- Point-in-time fundamentals come from SEC companyfacts, dated by filing date. The .gov user agent is the one in
  `CLAUDE.md`.
- After the US close the server refreshes whatever is stale, then runs the daily learning loop: grade matured
  forecasts, rescore the tracked assets (holdings, watchlist, core ETFs), forecast from the saved ML models and
  retrain them monthly.

**Method.** Every step is point in time; nothing uses data that was not known on the date it describes.
- *Features.* About 60 per asset, grouped as returns, momentum, statistics, risk, volatility (incl. GARCH refitted
  yearly), technical, time series (ADF, half-life, Hurst), valuation, fundamentals, rates, credit and macro.
  Signals are expanding z-scores capped at ±3.
- *Horizon engine.* Tests every signal at 1D, 1W, 1M, 3M, 6M, 12M, 3Y, 5Y and 10Y, reporting:
  - rank IC and hit rate;
  - a Student-t p-value on the *effective* sample (overlapping windows and slow signals are not counted as
    independent), plus a Benjamini–Hochberg q-value across all signals;
  - stability across thirds of the history, and results by regime.
  When a horizon lacks the data (10Y has about 2 independent windows), it says so rather than showing a number.
- *Regimes.* Bull or bear, volatility, rates, inflation, recession, dollar, liquidity.
- *Quant score.* Runs from −100 to +100 at each horizon. Signals are weighted by their evidence, blended toward
  the current regime, and the score is damped when the evidence is weak. The score comes with:
  - an expected return and its error;
  - a confidence built from significance, out-of-sample accuracy, stability, regime similarity, data quality and
    agreement with the ML score;
  - a point-in-time history (weights refitted quarterly from outcomes known at the time) whose out-of-sample IC is
    reported and can be backtested.
- *ML Lab.* The laboratory that tests and refines the Shaffer Score and the Shaffer Hedge (see
  `finsim2/ML_LAB.md`). It never trades and never changes production by itself.
  - Weight research: family weights by class → sector → industry → asset, each shrunk toward its parent, fitted on
    pre-2018 outcomes, confirmed on untouched post-2018 data, and walk-forward over 25 years of point-in-time records.
  - Every formula is a registered version. Challengers run in live shadow and are promoted only after three gates
    (discovery, confirmation, live) and an explicit user action.
  - Hedge research: whether the static hedge was historically too big or too small, per group and objective.
  - Live learning: the forecasts waiting to mature and those already graded.
  - `python3 -m finsim2 lab --build` rebuilds the research records and runs the research.
- *The Shaffer System* (`finsim2/SHAFFER_SYSTEM.md`). **The Shaffer Score is the expected percentage total
  return over the selected horizon.**
  - It comes in two parts: **Shaffer Alpha** (1M–5Y) and **Shaffer Directional** (1D, 3D, 1W).
  - Each forecast carries its median, P(>0), P(beating its benchmark), 50% and 90% ranges, reliability, the
    equation and hierarchy node used, and the main contributors.
  - The equation is learned per horizon and asset type (Global → class → product type → sector → industry →
    asset) and is used only where it beat the calibrated prior out of sample.
  - The former −100…+100 score is now the "evidence index", one of its inputs.
  - Derivatives are forecast through their underlying: the trade layer converts the forecast into the instrument's
    expected P&L.
  - The trade ticket's **Shaffer Hedge** report shows the forecast, its uncertainty, the principal unwanted risk,
    the recommended hedge (hedge-2's sizing), its cost, the expected downside reduction and the expected profit
    sacrificed.
  - Commands: `python -m finsim2 system NVDA SPY` prints the forecasts, and `system --all` does the monthly refit.
    What more data would help is listed in `finsim2/DATA_NEEDS.md`.
- *Short-term outlook* (asset page). For the next day and the next week it shows three things:
  - **P(up)** from the prior-only Directional model. No signal beats this model at 1D–1W.
  - **The expected move** (±1σ).
  - **A calibrated 90% range.**

  The move size comes from the validated calendar + 8-K volatility model (`finsim2/SHAFFER_MOVE_SIZE.md`). It is refitted
  weekly by the daily learning job and otherwise frozen. It is informational, not a trade signal.
  `python -m finsim2 movesize [ASSET ...] [--fit]` prints it from the terminal.
- *News* (the News page, and a News card on the asset page). It is **research only, not part of the Shaffer Score**.
  - Headlines come from Dow Jones' public RSS feeds for WSJ and MarketWatch: one request per feed a day, never a
    log-in or an article page.
  - You can add articles you are reading, including Barron's. The page's bookmarklet copies the article in your own
    browser, and you paste it into the form. The text is used once for features and never stored.
  - Each article is dated from when FinSim2 first knew it. Its assets are linked by ticker and company name.
  - The features are evaluated forward only under `finsim2/NEWS_SIGNALS_PROTOCOL.md`. The status shows ACCUMULATING
    until there is enough data. Details: `finsim2/NEW_DATA_SOURCES.md`.
- *Independent ML forecasts (benchmark).* Linear, ridge, LASSO, elastic net, logistic, random forest and gradient
  boosting, all in pure Python. Kept as a benchmark for the Shaffer Score, not as a second recommendation.
  - Training is walk-forward with purged folds, and the last 15% of rows are held out untouched.
  - The return ensemble must beat every baseline on the same rows: zero, historical mean, previous return,
    momentum, mean reversion and the point-in-time Shaffer Score. It must also be significant, hold up on the
    holdout and beat block-shuffled copies of itself.
  - Otherwise the ML score is 0 and the lab says "NO VERIFIED ML EDGE" with the reasons.
  - Direction (probability of a rise), future volatility and drawdown probability are separate problems, each
    tested against its own baseline.
  - Models are saved, forecast daily and retrained monthly. Every forecast is logged and graded when it matures.
- *Pages.*
  - Dashboard, Markets and Watchlist.
  - Asset Research: the one-page answer for an asset.
  - Analytics: the equation library evaluated on the asset, by family, each with value, percentile, direction and
    evidence.
  - Quant Lab: equation leaderboard, a clickable signal-horizon matrix, and score history.
  - ML Lab: production models, historical performance, signal research, weight research, challengers and promotion,
    hedge research, live learning, version comparison, and the independent ML forecasts.
  - Model health: every engine's status from its walk-forward and live record.
  - Risk: VaR and ES, factor drivers, scenarios in which unset factors move with the ones you set, and Monte Carlo
    with a percentile cone.
  - Backtests: any signal, the quant score or the ML ensemble, with costs (net per trade), a one-session execution
    lag by default, and each signal pointed the way its history said at each date, not over the whole sample.
  - Portfolio: a transaction ledger, positions with every score, NAV history, optimiser (frontier, minimum
    variance, maximum Sharpe, risk parity) and drivers.
  - Settings.

It never presents a simulated or fitted result as a forecast. Numbers come with their sample size, and weak
evidence is labelled weak.

**Shaffer Score (v2).** The central, transparent score per asset and horizon, from −100 to +100:

    SS(a,h,t) = 100 · tanh( Σ_f W_f,a,h,t · A_f,a · H_f,h · [ Σ_{i∈f} ω_i · s_i · c_i · r_i · d_i ] / K_a,h )

- Families: 15 of them, from Momentum to Relative Value, including composites such as risk-adjusted momentum,
  trend quality, mean-reversion opportunity, rate duration and the credit signal. Each signal has an economic prior
  direction (+, − or "learn from evidence").
- s: the signal's reading, clip(z ÷ 2, −1, 1), turned by its direction.
- w: predictive strength before t, the median of the IC, the IC implied by the hit rate and the IC implied by the
  conditional-return spread, × stability. Small samples are shrunk toward the asset-class and global evidence.
  That evidence comes from other assets' January checkpoints, known at the time.
- c: confidence, min(1, √(n_eff/100)) × CI strength × (1 − ½q) × data quality.
- r: the regime adjustment, shrunk for small samples.
- d: decay (HEALTHY / WEAKENING / DECAYING / INSUFFICIENT DATA). It reduces a signal's weight gradually and never
  flips its sign.
- ω: a within-family correlation penalty, so duplicate signals count once.
- W: family evidence × out-of-sample validation V.
- A, H: asset-class and horizon applicability.
- K: 0.10 × Σ A·H.

A signal is reversed only on strong, stable evidence (|t| ≥ 3, n_eff ≥ 60), never on a tiny sample.

**What the Shaffer Score is.** A structured quantitative evidence score: what the evidence currently says. Out of
sample it has not shown statistically reliable predictive power at any horizon (the best, 1W, has IC +0.012, date-
clustered t 1.2), so it is not presented as a forecast; an expected
return appears only where the calibration supports one. The ML Score says whether machine learning has found a
*verified* edge (0 when it has not). The Shaffer Hedge says what risks a book has and how to hedge them.

**Candidate families (shadow).** Seven families of economically different information — Carry, Yield Curve, Term
Structure, Inflation, FX, Commodity, Optionality — are computed point in time and evaluated in shadow, never in the
score, until an out-of-sample admission test passes: a date-clustered incremental IC over the production score that
is significant before 2018 and confirmed after it, with calibration no worse (`SHAFFER_AUDIT.md` §27). None has
passed yet; Carry is the closest (strong since 2018, not significant before).

There is one scoring routine, `engine/shaffer.compute_shaffer_score(asset, horizon, as_of)`. The 25-year history
and today's score both come from it. Evidence is refitted monthly from outcomes known at the time, and nothing is
retrofitted. The tests check that:
- a past date scored directly equals its record;
- later prices do not change it.

A calibrated score maps the raw score to realised forward returns with an isotonic curve, learned only from earlier
out-of-sample scores. An expected return is shown only when that calibration is significant. Every live score is
written to the append-only prediction ledger and graded once when it matures.

Where it appears:
- Every asset's Analytics page has a **Shaffer Score** tab. It shows the horizon table, family points, the
  strongest and the contradicting signals, the calibration table, the historical record, results by regime,
  family validation and definitions.
- It is also on Asset Research, in the evidence strip (with the Shaffer vs ML agreement), and in the Markets,
  Watchlist and Positions tables.

`python -m finsim2 audit` replays it over the whole universe and writes `finsim2/SHAFFER_AUDIT.md`. That report
covers IC and hit rate by horizon, returns by score band, results by asset class and regime, family value, decay,
and ML against baselines.

Its constants and tables are at the top of `finsim2/shaffer_score.py`. A file at `~/.finsim2/shaffer_score.py`
(or `FINSIM2_SHAFFER`) can add a custom live-only score. It is shown beside the canonical one, never instead of it
and never in the history.

**Shaffer Hedge.** Risk first, product second (design: `finsim2/SHAFFER_HEDGE.md`, products: `finsim2/PRODUCT_REGISTRY.md`, status of every requirement: `finsim2/SYSTEM_REPORT.md`).
- The portfolio (or a position, or a proposed trade) becomes a risk vector with each risk in its own unit:
  - beta-dollars for the market, sector, industry and style spreads;
  - DV01 by key rate (2/5/10/30Y) and real DV01;
  - CS01 (IG/HY);
  - currency, commodity and crypto dollars;
  - vega, and the Greeks of any options;
  - single-name residual.
- Hedge products are sized in their own unit:
  - shares by price × β;
  - futures by F × multiplier × β, never by margin;
  - options by delta-adjusted notional, never by premium;
  - Treasury futures by DV01;
  - credit funds by CS01;
  - forwards by currency notional.
- An optimiser meets the target on each chosen risk while minimising cost, basis risk (the hedges' own residuals and side
  exposures) and market impact. It picks up to 2–3 legs in whole contracts/shares.
- Each candidate gets a Shaffer Hedge Score, 100·tanh(E·Q·L·R·B·T). Its terms are effectiveness from a walk-forward record,
  cost efficiency, liquidity, regime suitability, basis-match quality and tail convexity.
- Products FinSim2 cannot price faithfully are shown as PRODUCT NOT ELIGIBLE FOR SHAFFER HEDGE, with the reason.
- The ML adjustment is separate. It is at most ±15% of the raw hedge and applies only where it beat the static rule and the
  minimum-variance hedge out of sample; otherwise it is 0 and the page says why.
- Scenarios (±5/10/20%, a VIX spike, rates, curve, credit, the dollar, oil, crypto) re-price options fully.

Where it appears:
- The **Shaffer Hedge** page shows the whole book.
- **Analyze hedge** is on every position.
- The **trade ticket** (from Markets, Asset Research or Portfolio → Trade…) shows:
  - the Shaffer Score and the long/short scores net of costs;
  - the risk the trade adds and the hedge (25/50/75/100% or custom, or an alternate product);
  - **Trade only** or **Trade + Shaffer Hedge**. The trade and its hedge are validated together and recorded in one
    transaction: all legs or none.

Every proposal is kept in an append-only hedge ledger and graded when its horizon has passed.

**Ledger.** It supports:
- long and short positions: SHORT/COVER with 150% collateral, a daily borrow fee and dividends owed;
- equity-index, Treasury and currency futures, and FX forwards, marked daily to model, margined and settled at expiry;
- bought index/ETF/stock options, marked to model and settled at intrinsic value.

Buying power is re-checked on every date of the replay.

The standard-library equation library it evaluates lives in `finsim/quant/`:
- returns and statistics (1–20), regression (21–33), time series (34–43), volatility incl. GARCH (44–50);
- stochastic processes (51–62), machine learning (63–83), neural networks (84–100);
- portfolio theory, VaR and ES (101–110), fixed income (111–118);
- derivatives, swaps and credit.

`finsim/quant/catalog.py` holds each equation's LaTeX, a note and its implementation.

## FinSim1 commands and exit codes

`python -m finsim <command>` from either folder, or `FinSim.cmd <command>`:

| Command | What it does | Exit code |
|---|---|---|
| (none) | prints the three-line hint | 0 |
| `serve` | runs the server in this window. It first prints one line naming the code tree, the VERSION and the database; on your real home it adds which tree the FinSim icon and Windows login start when that is another one. Options: `--port 8765`, `--home DIR`, `--db FILE` (`finsim.db` is refused), `--host 127.0.0.1` (this computer only), `--offline` (fake prices for tests; refused on your real home), `--log-level INFO` | 0 stopped; 3 FinSim already runs on this home (the message names its version, PID, start time in ET, address and code tree) or another program holds the port; 4 the database was written by a newer release; 5 startup error or a refused option (the traceback is in `terminal.log`) |
| `stop` | asks the server to shut down and waits for the port and the lock to free; `--force` ends the lock holder only when it is a live Python process; prints the sha256 of `finsim.db` | 0 stopped; 1 nothing was running; 2 still running |
| `status` | what runs and from which code tree (version, PID, uptime, integrity, workers, market-data providers, lifecycle jobs, lock); the Startup entry and the tree it starts, as `Startup entry -> <tree> (finsim-terminal <VERSION>, release <tag>, commit <SHA>)`, and whether its `pythonw.exe` and code exist; the FinSim icon; old Chrome app shortcuts; backups; the end of `terminal.log` when something is wrong | 0 the terminal answers; 1 otherwise |
| `install` | sets up the installed app for this Windows user from the folder it runs in: the Startup entry (starts the server at login), the FinSim icon on the Desktop and in the Start Menu; then starts the server and prints `status`. Refused from a git working tree such as this repository unless the owner passes `--from-working-tree`: the installed app comes from a release folder. `--no-open` does not open the window | 0 the terminal is up at the end; 1 otherwise, or refused (then nothing was changed) |
| `open` | opens the app window; when the server does not answer it starts it first through the installed launcher | 0 opened; 1 the server did not come up (the end of the log is printed) |
| `uninstall` | removes the Startup entry, the FinSim icon and the launcher files and stops the terminal; `terminal.db` stays (`--purge` deletes it only after you type its full path) | 0 |
| `import ...` | the historical import tool: report, dry run, apply or restore of an import batch (`python -m finsim import --help` lists its options; every run names `--home`) | 0 done; 1 failed; 2 refused or a usage error |

The `legacy` command (the retired simulation game's archive view) was removed in 1.0.6. Nothing was deleted: the old
game's code and `finsim.db` are kept as they were.

## The installed app

- **Release folder.** The app you use every day runs from a release folder,
  `%USERPROFILE%\.finsim\releases\<tag>\intelligence-terminal\finsim`, made from a commit; `RELEASE.txt` two folders up
  names the tag, the commit SHA, the branch and the VERSION. Edits in this repository do not reach the running app until a
  new release is installed. When the running server is another version or tree than the folder you type in, `status`
  names both and gives the two real choices: open the installed app, or `stop` and then `serve` here.
- **Startup entry.** `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\FinSim Server.vbs` starts the server at
  Windows login with `pythonw.exe` (no window). When `pythonw.exe` or the code it names is missing it starts nothing and
  writes why to `%USERPROFILE%\.finsim\launcher.log`; `status` checks both.
- **The FinSim icon.** Use the single shortcut `FinSim.lnk` (Desktop and Start Menu, described as "FinSim Paper
  Terminal"). It runs `FinSim Terminal.vbs`, which starts the server when needed and opens the app window. Two older
  Chrome app shortcuts named FinSim (Desktop `FinSim (1).lnk`, Start Menu `Chrome Apps\FinSim.lnk`) only open a browser
  window on 127.0.0.1:8765 and cannot start the server; `status` and `install` report them and leave them in place
  (delete them yourself if you no longer want them).
- `install` writes `FinSim Server.vbs`, `FinSim Terminal.vbs` and `finsim.ico` into `%USERPROFILE%\.finsim` and copies
  the server VBS into the Startup folder; the files are plain ASCII and name the code tree, `pythonw.exe` and the home.

## Data and backups

The database is `%USERPROFILE%\.finsim\terminal.db`, and everything else lives beside it in `%USERPROFILE%\.finsim`
(`FINSIM_HOME` moves the folder, `FINSIM_TERMINAL_DB` names another database, `FINSIM_PORT` another port):

| Path | What |
|---|---|
| `terminal.db` (with `-wal` and `-shm`) | the database: every command, journal entry, order, fill, settlement and NAV snapshot |
| `backups\terminal-YYYYmmdd-HHMMSS.db` | a copy taken automatically each time the server opens the database, before anything is migrated (the newest ten are kept) |
| `backups\terminal-pre-<version>-<stamp>.db` | the copy taken before a release is installed |
| `backups\import\terminal-pre-<batch>-<stamp>.db` and `.sha256` | the copy taken before an import is applied; the import tool's restore puts it back |
| `terminal.log` | the server log (5 MB, three older files kept); `status` prints its end when something is wrong |
| `launcher.log` | what the Startup entry, the FinSim icon or a windowless `open` could not start, and why |
| `terminal.lock`, `terminal.db.lock` | held by the running server: one server per home and per database |
| `releases\<tag>\` | the installed release folders |
| `finsim.db` | the retired simulation game's saves; FinSim1 never opens it for writing (`stop` and `status` print its sha256) |

## The pages

The sidebar follows the owner's order; the keys are the defaults (change them in Settings > Keyboard):

1. **Dashboard** (F12): the landing page, with the "Requires your attention" tiles, fund NAV, P&L, exposures, cash,
   margin, collateral and an account summary.
2. **Portfolio** (F7): every open position with its mark, market value, P&L, margin and collateral requirements.
3. **Trading**: order entry at the venues US Exchange (F1), Debt Instruments (F2), Credit Instruments (F3), Foreign Markets
   & FX (F4), Private Markets (F5) and each prime-broker account (F6), with Watchlists (Shift+F9).
4. **Pending Trades** (F8): working, queued, held and partially filled orders, with modify, cancel and replace, and the
   order history.
5. **Completed Trades** (Shift+F8): every execution, historical as-of executions included, never merged, with its
   settlement status and accounting.
6. **Accounts** (Shift+F4): Treasury (F10), the trading accounts and the prime-broker accounts.
7. **Cash & Collateral** (Shift+F5): cash movements, restricted cash, collateral balances, margin and funding.
8. **Derivatives** (Shift+F6): OTC contracts (total return swaps, CDS index, interest-rate swaps), listed options and
   futures, with their servicing.
9. **History** (Shift+F10): historical NAV (restated values with their split), transactions and account activity.
10. **Settings** (Shift+F11): simulation settings, market data, keys and account configuration.

Book views: **Risk** (F11) and **Accounting** (Shift+F7). Activity, the book's event log, is a tab of History and has no
default key of its own (give it one in Settings > Keyboard). Shift+F1 / Shift+F2 / Shift+F3 select the Personal, Fund and Institutional book; on an
account page F8 and Shift+F8 open that account's pending and completed trades and Shift+F5 its cash; F9 shows or hides
the order ticket; Shift+F12 or `?` lists every key.

## Market data

Read only and delayed; nothing is ever traded against these sources, and every number on screen carries its source,
time and quality:

- Yahoo Finance: stock, ETF, futures and FX quotes (up to 15 minutes delayed) and daily closes;
- Cboe: delayed listed-option chains;
- U.S. Treasury: the treasury.gov par yield curve, TreasuryDirect FedInvest prices and fiscaldata securities;
- FRED (Federal Reserve Bank of St. Louis): SOFR and the ICE BofA option-adjusted spreads used as CDX index proxies;
- New York Fed: SOFR;
- FINRA: daily short-sale volume;
- Frankfurter and the ECB: FX reference rates; Japan's Ministry of Finance: JGB yields.

OTC values (total return swaps, CDS index, interest-rate swaps) are model values computed from these inputs, labelled
MODEL, and never shown as dealer-confirmed prices. With `--offline` (tests only) a fake price source replaces all of them
and the screen says FAKE DATA.

## Limits

Cross-currency swaps are fixed / fixed in 1.0.6: no free non-USD reference fixing is sourced, so there is no floating leg (SOFR, ESTR, SONIA ...) and no cross-currency basis. Forwards, FX options and cross-currency legs need a current rate for each currency: CHF (its free series stopped in March 2024), NZD, SEK and NOK are spot only.

## Tests

From `C:\Users\logan\intelligence-terminal\finsim`:

```powershell
python tests\run_terminal_suite.py
py -3.14 tests\run_terminal_suite.py
```

The runner takes every `tests\test_terminal_*.py` file and ends with `RAN n FAIL n ERR n SKIP n FILES n` and
`ResourceWarnings n`; it exits 0 when everything passed. Name files to run only those, for example
`python tests\run_terminal_suite.py test_terminal_app`. The same suite with unittest alone:

```powershell
python -m unittest discover -s tests -t . -p "test_terminal_*.py"
```

The tests use temporary folders, port 0 and fake prices; they never touch `%USERPROFILE%\.finsim` or port 8765.

## Why `python -m finsim` failed from the repository root

`C:\Users\logan\intelligence-terminal\finsim` holds the `finsim` package (`finsim\finsim`) and FinSim2
(`finsim\finsim2`), but it is not a package itself. From the repository root Python therefore found that folder as an
empty namespace package named `finsim` and stopped with "No module named finsim.__main__; 'finsim' is a package and
cannot be directly executed". Since 1.0.6 the folder carries a small `__main__.py` that puts its own folder first on the
module path and runs the real package, so the command works from both folders. Nothing is pip-installed (installing
`finsim` would also capture FinSim2), and FinSim2's own start is unchanged.

## Troubleshooting

- `status` says the server runs another version or tree: either open the installed app, or `stop` and then `serve` in
  the folder you want to run (see "The installed app").
- `serve` or `open` exits 3: FinSim already runs on this home (use `python -m finsim open`), or another program holds the
  port (`python -m finsim status`, or `--port`).
- The FinSim icon or Windows login starts nothing: read `%USERPROFILE%\.finsim\launcher.log`, then run
  `python -m finsim status`, which checks the Startup entry's `pythonw.exe` and code.
- `stop` exits 2: the server did not shut down; `stop --force` ends it only when the lock holder is a live Python process.
- `install` is refused: run it from the release folder (the release tooling does), not from this repository.
