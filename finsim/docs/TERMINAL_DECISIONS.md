# FinSim Live Terminal — fixed decisions for the build (Claude, 2026-10-20 16:10 ET)

These decisions are settled. Design proposals and implementation work must respect them. Where the owner's
documents conflict, precedence is: `HANDOFF_2026-10-20.md` (newest) > `DIRECTIVE_2026-10-20.md` amendments
(newest first) > the directive's verbatim text. The old ChatGPT build is unavailable (see the recovery finding
at the end of HANDOFF_2026-10-20.md); this terminal is built here, from the specification.

## 1. Where the code lives

- New Python package **`finsim/finsim/terminal/`** (importable as `finsim.terminal`). Standard library only
  (`sqlite3`, `http.server`, `threading`, `urllib`, `decimal`, `zoneinfo` with `finsim.tzfallback` fallback).
  Must run on **Python 3.12 AND 3.14** (the launcher uses the 3.12 `pythonw.exe` found by `py -3.12`; tests are
  run with `py -3.12 -m unittest` and `py -3.14 -m pytest`).
- Its own UI under **`finsim/finsim/terminal/static/`**: vanilla JS and CSS, several files (one per page or
  concern), no build step, no CDN, no frameworks. Served at `/`. Professional dark trading-terminal look.
- Its own tests **`finsim/tests/test_terminal_*.py`** (unittest style, no network, temp SQLite files, hand-worked
  expected numbers with the arithmetic in comments).
- Its own database **`%USERPROFILE%\.finsim\terminal.db`** (env `FINSIM_TERMINAL_DB` overrides; `FINSIM_HOME`
  still names the home folder). **`finsim.db` is never opened for writing by the terminal.**
- The original simulation (`finsim.world`, `finsim.engines.*`, `finsim.api.*`, `finsim.app`, `finsim.static`)
  stays in place and **unchanged** (FinSim2 / Shaffer Desk imports it; the old saves stay readable). Allowed
  edits outside the new package: `finsim/finsim/__main__.py` (re-pointed to the terminal; a `legacy` subcommand
  runs the old simulation server on another port, default 8766, read-only intent documented), and new docs.
  `finsim/finsim/app.py` is NOT edited (it carries another work stream's uncommitted hunks).
- Launcher: **`finsim/finsim/terminal/app.py`** — install / open / status / stop / uninstall / serve for the
  terminal, modelled on the old `finsim/app.py` (Windows: `pythonw` from a Startup-folder VBS named
  `FinSim Server.vbs` so it REPLACES the old entry; Desktop + Start Menu shortcuts; Chrome app-window opener
  falls back to the default browser). It must: refuse to start a second server (exclusive port bind with
  `allow_reuse_address = False` + a lock file with the PID in `FINSIM_HOME`), implement `stop` (loopback
  `/api/shutdown` then PID check), write a rotating log `FINSIM_HOME/terminal.log`, and report the served code
  version (`finsim.terminal.VERSION`) in `/api/health` and in `status`.

## 2. Money, accounts and the ledger

- **One lifetime.** One persistent paper book. **Treasury** holds central cash by currency; it starts with
  **USD 10,000,000 posted ONCE** as a capital contribution (first-run initialisation, idempotent, recorded with
  its source "initial capital"). Further funds are explicit **capital deposits** (never P&L). No other path
  creates money. Account creation never creates money: an account opens empty and is funded by internal
  transfers from Treasury.
- **Accounts** (the left sidebar): Treasury; **US Exchange** (US stocks, ETFs, listed options, futures);
  **Debt Instruments** (bonds); **Credit Instruments** (cash loans, repo, securities lending);
  **Foreign Markets & FX** (non-US securities, FX spot/forwards, foreign bonds); **Prime Broker accounts**
  opened on demand (Goldman Sachs, JPMorgan, Morgan Stanley, Deutsche Bank, Barclays, Citi, UBS, BNP) with a
  disclosed fee/financing schedule. Each account page = its markets + its own accounting (positions, P&L,
  transactions, cash). A ticket trades in the account whose page it is opened from.
- **Sweeps:** sweep-in = when a trade, fee, margin call, collateral call or settlement needs more cash than the
  account holds, exactly the shortfall moves from Treasury as one linked internal transfer, shown in the preview
  and in Transactions (order of funding: the account's own settled cash → Treasury sweep → PB financing if
  switched on → else refuse with the reason). Sweep-out = once per calendar day after the US close, idle settled
  cash above the account's needs (pending settlements, margin, collateral, a configurable buffer) returns to
  Treasury; a per-account switch (default on).
- **One double-entry ledger in SQLite** is the single source of truth: journal entries + lines (account code,
  currency, debit/credit, instrument, lot, memo, reference). Every displayed figure (cash settled/pending by
  currency, positions, cost basis, realised, unrealised, income, financing, fees, collateral, loans, NAV, equity,
  P&L today/MTD/YTD/inception) is computed by ONE module (`accounting.py`) from the ledger + the current marks.
  No parallel cash projection that can diverge. `Decimal` everywhere in accounting; never floats.
- **Transactions:** every command runs through one `CommandBus` with a process-wide writer lock, `BEGIN
  IMMEDIATE`, journal + events + projections in the same transaction, COMMIT or ROLLBACK (no partial state).
  Every command carries a `request_id`; a repeat with the same id returns the stored result (unique index), so
  retries are safe. Errors are raised to the caller (HTTP 400/409/500 with the reason) AND logged; background
  jobs never swallow exceptions (they log + surface them in `/api/health` and a UI "Attention" strip).
- An `integrity()` check (every entry balances; settled+pending cash per account/currency equals the ledger;
  each position's quantity equals its fills minus expiries/exercises; NAV = assets − liabilities; no duplicate
  fill/settlement/accrual per key; Treasury = capital + retained results − advances; every short has a live
  borrow) runs after every command batch and on startup, and is shown in the UI when it fails.

## 3. Market data (free sources only; never a crumb, never an anti-bot bypass)

Verified from this machine on 2026-10-20 16:00 ET (`scratchpad/probe/probe_out.txt`):
- **Yahoo v8 chart** `https://query1|query2.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=1m`
  (browser User-Agent allowed for Yahoo only): equities, ETFs, ADRs, foreign listings, FX (`EURUSD=X`),
  futures continuous (`ES=F`) and per contract (`CLX26.NYM`), yields (`^IRX ^FVX ^TNX ^TYX`), indices, crypto.
  Meta gives `regularMarketPrice`, `regularMarketTime`, `regularMarketVolume`, `previousClose`, `currency`,
  `exchangeName`, `exchangeTimezoneName`, `currentTradingPeriod`; plus 1-minute bars with volume. ~0.1–0.3 s.
  This is the **last trade price** feed (no bid/ask). Yahoo's v7 quote/options endpoints need a crumb: NOT used.
- **Cboe delayed quotes** `https://cdn.cboe.com/api/global/delayed_quotes/options/{SYM}.json` (index symbols
  prefixed `_`, e.g. `_SPX`): underlying `current_price`, `bid`, `ask`, sizes, `last_trade_time`, `iv30`, and
  the FULL option chain (bid/ask/size, iv, greeks, OI, volume, last). Delayed ~15 min. Payloads are LARGE
  (SPY 5.7 MB, SPX 13 MB): fetch one underlying at a time, on demand, cache 60–120 s, never in bulk.
- **Treasury.gov daily par yield curve** (XML, `.gov` UA `LoganTerminal/1.0 (loganshaffer87@gmail.com)`; slow,
  ~16 s): cache for the day. Intraday yields from Yahoo `^IRX/^FVX/^TNX/^TYX`.
- **FRED CSV** (`fredgraph.csv?id=SOFR`, `DGS*`, `BAMLC0A0CM` etc., `.gov` UA): daily rates and OAS.
- **Frankfurter** (ECB reference rates): fallback FX only. **FINRA** daily short volume (`cdn.finra.org`):
  hard-to-borrow heuristic input. **SEC** `company_tickers.json` / companyfacts (`.gov` UA, ≤10 req/s): CIK
  and shares outstanding for compliance notices (optional, cached weekly).
- **Not used:** Nasdaq Trader (Incapsula wall), Stooq, anything needing a key the owner has not set (keys, if
  present in the environment, are optional enhancements and never logged).
- **Every mark is stored with** `source`, `quote_time` (provider timestamp), `fetched_at`, `delay_label`, and a
  `quality` in {`live`, `delayed`, `close`, `computed`, `stale`, `unavailable`}. The UI shows freshness
  (age/delay/source) on every price (handoff §4, §10, §11 override directive amendment -2).
- **Freshness rules:** a quote is `stale` when its provider timestamp is older than its stated delay + 5 min
  during that market's session, or when the session is open and nothing has been fetched for 5 min. Stale or
  unavailable quotes block market/limit executions and are shown explicitly; marks for valuation fall back to
  the last valid mark with its timestamp (displayed as such), never to a simulated or invented number.

## 4. Execution model (explicit, disclosed in every preview and in `docs/TERMINAL_EXECUTION.md`)

- **No simulation, no random prices, no synthetic market events, no day-advance.** Lifecycle (settlement,
  accruals, expiry, variation margin, collateral marks, sweeps) runs from the real clock in a background job,
  idempotent per (job, instrument/account, date).
- **Sessions:** US equities/ETFs 09:30–16:00 ET regular session only (no pre/post executions); listed equity
  options 09:30–16:00, index options 09:30–16:15; CME futures per product-group session table (e.g. equity index
  and treasuries Sun 18:00–Fri 17:00 ET with a 17:00–18:00 daily break; energy/metals 18:00–17:00); FX spot
  Sun 17:00–Fri 17:00 ET; US bonds 08:00–17:00 ET weekdays; foreign listings from Yahoo's `currentTradingPeriod`
  and `exchangeTimezoneName`. Holidays and 13:00 early closes from the repo's `data/nyse-holidays.json`
  (plus `finsim.calendar.us_equity_holidays` as the rule fallback). Orders entered outside a session rest until it
  opens; a DAY order expires at that session's close; GTC rests up to 90 days.
- **Market order:** fills at the latest observed executable price: Cboe NBBO ask (buy) / bid (sell) when
  available and fresh; else Yahoo's last trade price. Requires a fresh quote (section 3) and an open session.
- **Limit:** marketable → fills at the observed price if it is at or better than the limit; resting → fills at the
  limit price when the observed trade price crosses it (buy: last ≤ limit; sell: last ≥ limit). **Stop /
  stop-limit:** trigger when the observed last price reaches the stop, then market / limit.
- **Partial fills:** per matching pass, the fillable quantity is capped at a disclosed participation rate (default
  10 %) of the latest observed 1-minute bar's volume when that volume is known; the remainder keeps working.
  Options and futures: cap by the chain's displayed size / the observed minute volume likewise. Orders with no
  volume information fill in full (disclosed).
- **Fees (disclosed, modelled):** equities USD 0.005/share min 1.00; options USD 0.65/contract; futures USD 2.25
  per contract per side; FX 0.2 pip equivalent spread cost shown; bonds USD 0.02 per 100 face min 5.00; PB
  schedules per broker. Interest: cash interest on idle Treasury cash = SOFR − 0.50 % (disclosed); margin
  financing SOFR + 1.50 %; stock borrow GC 0.30 % with hard-to-borrow tiers; repo SOFR ± spread table;
  currency loans policy rate + 0.75 %.
- **Shorts:** locate → borrow (fee, rebate, 102 % cash collateral marked daily both ways) → short sale; short
  proceeds restricted until covered; no borrow → rejected with the reason. Recalls are NOT invented: a recall
  only arises from observable data (e.g. the name on a hard-to-borrow list) and is shown as a notice, never
  randomly generated.
- **Options:** buy/sell to open/close; exercise/assignment at expiry from the observed underlying close
  (ITM by ≥ USD 0.01 exercises/assigns automatically — the OCC threshold; early exercise by user action);
  Reg T style margin for short options (disclosed formula). **Futures:** contract spec table (multiplier, tick,
  currency, session group, expiry rule, initial/maintenance margin) for a core set (ES NQ YM RTY CL BZ NG GC SI
  HG ZB ZN ZF ZT 6E 6J 6B ZC ZW ZS); variation margin posted daily at the observed settlement/close; expiry →
  final cash settlement. **Bonds:** Treasuries priced from the par curve (labelled `computed`), accrued interest
  ACT/ACT, coupons on schedule; corporates limited to a curated set priced curve + FRED OAS (`computed`).
  **FX:** spot T+2 at Yahoo mid ± disclosed spread; forwards via covered interest parity from FRED rates
  (`computed`). **Credit:** currency loans (daily accrual, floating off the policy rate), repo/reverse repo
  (SOFR-based, haircut table), securities lending. **OTC via PB:** IRS and TRS (`computed`, SOFR/par curve).
- **Risk only warns**; funding, borrow and recall mechanics still apply. **Compliance notices inform only**
  (Reg SHO, Reg T/FINRA 4210, PDT, 13D/G 5 % and Section 16 10 %, 13h-1, Form SHO/13f-2, 13F, option and
  futures position limits, self-trading between own accounts, wash sales, EU/UK short disclosure), each with the
  rule name, the threshold used and "verify the current rule text" wording; not a compliance system.
- **Hedge candidates** pop up on the ticket as soon as an instrument is entered (protective put ~7 % OTM nearest
  expiry ≥ 3 months, collar, put spread, index-futures overlay sized to notional; calls for shorts; Treasury
  futures for bonds; forwards/options for FX), each priced from the live chain with cost and protection level;
  a chosen hedge previews and executes as its own paper trades (sequential, hedge dependent on the primary fill,
  resized on partial fills).

## Owner amendment (2026-10-20 17:03 ET): variation margin on swaps too
The owner: "variation margin is on swaps too btw". Every swap booked through a prime broker (IRS, TRS, cross-currency; and
any other OTC derivative under the PB's CSA) carries DAILY variation margin exactly like futures: each calendar day on which a
mark exists, the change in the swap's mark (computed from the live curve / underlying, labelled `computed`) is exchanged in cash
with the PB — paid when the mark falls, received when it rises — as one idempotent movement per swap per day (key: swap id +
date), funded by the Treasury sweep when the account lacks settled cash. VM paid/received is collateral (an asset/liability
against the PB), not P&L; the P&L is the mark change itself. Initial margin for swaps follows the PB schedule. The Cash &
financing tab, the ledger, Risk (margin headroom per PB) and the lifecycle job all include swap VM. Cross-currency swaps also
exchange notional at start and maturity per the confirmation.

## Owner amendment (2026-10-20 17:07 ET): swap variation margin AND resets
The owner: "vm and resets". Swaps carry both daily variation margin (above) and their scheduled RESETS and payments, per the
confirmation, run by the lifecycle job on the real calendar, idempotent (key: swap id + reset date), cash-settled in the account
(Treasury sweep if short):
- IRS: the floating leg fixes at each reset from the live reference rate (SOFR from FRED, compounded in arrears or term as
  booked; EURIBOR/SONIA equivalents for other currencies from FRED/ECB series where free); the fixed and floating coupons accrue
  daily (stated day count, ACT/360 for SOFR, 30/360 for the fixed leg unless booked otherwise) and the NET coupon is paid on each
  payment date (quarterly/semi-annual as booked).
- TRS: at each reset date the total-return leg (price change of the underlying since the last reset plus dividends paid in the
  period) and the financing leg (reference rate + spread on the notional for the period) are settled in cash and the notional is
  reset to the underlying's current value; dividends accrue as receivable/payable between resets.
- Cross-currency: periodic coupon exchanges in each currency, notional exchange at start and at maturity, and (if booked as
  mark-to-market XCCY) an FX notional reset on the USD leg at each reset with the difference settled in cash.
- Interaction with VM: VM collected/paid between resets is collateral; at a reset/payment the realised cash flow is exchanged and
  the collateral balance is re-measured against the post-reset mark so nothing is paid twice (settled-to-market where the PB
  schedule says so, otherwise collateralised-to-market with the collateral balance carried). Accrued coupons appear on the
  balance sheet as accrued income/expense until paid. Risk shows DV01/exposure from the current (post-reset) terms.

## Owner amendment (2026-10-20 17:09 ET): a Reset in Settings that keeps history
The owner: "a reset button in the settings to remove all positions like if i went bankrupt and wanted to restart, but there
should be history that stores this data". Design: the terminal has LIFETIMES. Settings gets a "Reset lifetime" button (typed
confirmation "RESET", one transaction, idempotent request_id). Reset CLOSES the current lifetime — nothing is deleted: every
account, position, order, fill, ledger entry, mark, collateral/loan/borrow record stays in terminal.db tagged with its
lifetime_id and becomes read-only — then opens a new lifetime: fresh Treasury with the starting capital the owner types
(default USD 10,000,000, posted once as a capital deposit), the four standard accounts empty, no prime-broker accounts, sweeps on.
Open orders of the closed lifetime are marked CANCELLED (reason "lifetime reset") in its history; its final NAV, realised P&L,
dates and the reason are recorded on the lifetime row. Settings shows the list of past lifetimes (name, start, end, final NAV,
P&L) and a "History" view lets the owner browse an archived lifetime's Portfolio, Accounting (P&L, balance sheet,
transactions with CSV export) and Activity read-only. The accounting module takes the lifetime as a parameter (default: the
current one), so every figure is per lifetime and integrity() runs per lifetime. Performance statistics are per lifetime.

## Owner amendment (2026-10-20 17:13 ET): two books; prime-broker / OTC eligibility
The owner: "you need the proper certification for prime broker relationships ... i think its the isda but correct me. I want two
books: one that starts at 10m (wont be using prime brokers / cant deal in OTC, right?), and one that starts at an isda-minimum
that can do the prime broker. whenever the 10m one reaches the isda minimum or whatever, it can start relationships".
Real-world facts to encode (shown in plain English in the app):
- OTC derivatives (swaps, forwards, OTC options) are traded under an ISDA Master Agreement plus a Credit Support Annex (CSA,
  the collateral/VM terms) with each dealer; repo under a GMRA; securities lending under an MSLA/GMSLA; a prime-brokerage
  relationship under a Prime Brokerage Agreement (PBA). ISDA itself sets no minimum.
- The legal floor for trading OTC swaps off-exchange is Eligible Contract Participant (ECP) status under CEA section 1a(18): an
  individual with more than USD 10M invested on a discretionary basis (USD 5M if hedging), or an entity with total assets over
  USD 10M. Dealers' own onboarding minimums for an ISDA/CSA + PBA are higher and vary: typically USD 25M–50M+ of net assets for
  a bulge-bracket prime broker (mini-primes accept less). The app uses an editable "PB/OTC eligibility threshold", default USD
  25,000,000 of book NAV, and states the convention on screen.
Design:
- TWO BOOKS, each a full lifetime of its own (own Treasury, own accounts, own ledger, own reset/history):
  1. "Exchange Book" — starts at USD 10,000,000. Exchange-traded only: US stocks/ETFs/ADRs, listed options, futures, Treasuries
     and corporate bonds, FX spot, cash loans from a bank, margin-style stock borrow through its (non-prime) broker. No prime
     broker accounts and no OTC derivatives UNTIL its NAV reaches the eligibility threshold; then "Open prime broker account"
     unlocks (onboarding records ISDA Master + CSA + PBA with the dealer and date, one click). If NAV later falls below the
     threshold, existing relationships stay (real dealers manage this by margin, not by closing), shown as a notice.
  2. "Prime Book" — starts at the eligibility threshold (USD 25,000,000 default, typed at creation). May open prime-broker
     accounts and trade OTC from day one (onboarding dialog still records the agreements).
- The sidebar has a compact book switcher at the top (two entries); all pages follow the selected book; Portfolio and Accounting
  offer "both books" consolidated views with internal transfers eliminated (there are none between books: books never transfer
  to each other). The "Add funds" and "Reset lifetime" actions are per book.
- The ticket refuses an OTC/PB trade in an ineligible book with the plain-English reason (this is an eligibility mechanic, not a
  risk warning).

## Owner amendment (2026-10-20 17:41 ET): THREE books (1M, 25M, 1B institutional) — supersedes the two-book layout above
The owner: "i think there should be 3 books: one at 1m, one at 25m, and one at 1b (institutional)". Same mechanics as the
two-book amendment (each book = its own lifetime: own Treasury, accounts, ledger, Add funds, Reset + history; a compact book
switcher at the top of the sidebar; consolidated views in Portfolio/Accounting; books never transfer to each other), with these
three books created on first run:
1. "Personal Book" — USD 1,000,000. Exchange-traded only (stocks/ETFs/ADRs, listed options, futures, Treasuries/corporates, FX
   spot, bank cash loans, margin-style stock borrow through its broker). Below the ECP floor (USD 10M) it cannot trade OTC at
   all; prime brokers/OTC unlock when its NAV reaches the eligibility threshold (default USD 25M, editable), with the one-click
   onboarding (ISDA Master + CSA + PBA recorded). Compliance notices relevant at this size (Reg T margin, pattern-day-trader
   rule with its USD 25,000 equity floor, cash-account good-faith/free-riding) are shown pre-trade when they apply.
2. "Fund Book" — USD 25,000,000. Eligible for prime brokers and OTC from day one (onboarding dialog records the agreements).
   13F notice when 13F securities exceed USD 100M; 13h-1 large-trader notice at the daily/monthly thresholds.
3. "Institutional Book" — USD 1,000,000,000. Prime brokers and OTC from day one; the compliance layer treats it as an
   institution (13F applies from the start, 13h-1 large trader, Section 13D/G 5% and Section 16 10% on single names, options
   and futures position limits/accountability levels, Form SHO/13f-2 short reporting), all inform-only.
The eligibility threshold, the three starting amounts and the book names are editable in Settings (changes apply to new
lifetimes; the live book's history is never rewritten).

## Owner amendment (2026-10-20 17:48 ET): PRIVATE MARKETS for the institutional (hedge-fund type) book — real companies
The owner: "institutional (hedge fund type) can also be in PRIVATE markets, make this real like anthropic, open ai / other
companies etc". Design (no fabricated prices or events; everything sourced and labelled):
- A new account "Private Markets" (sidebar, like the other accounts). Open by default in the Institutional Book; unlocks in the
  other books when their NAV reaches the Qualified Purchaser threshold (editable; default USD 25M for the Fund Book as an entity,
  USD 5M for the Personal Book as an individual), with the plain-English rule shown (accredited investor / qualified purchaser /
  QIB; Rule 506 and Rule 144 transfer restrictions).
- Instrument master `private_companies`: name, sector, country, status (private / filed for IPO / public → ticker), LAST
  REPORTED ROUND: date, round type (Series X, tender offer, secondary, SAFE), post-money valuation, implied price per share where
  disclosed, lead investors, and a SOURCE URL + as-of date for every number. Ship a starter table of ~25 real names researched
  at build time with web search and cited (Anthropic, OpenAI, SpaceX, xAI, Stripe, Databricks, Anduril, Scale AI, Perplexity,
  Mistral, Safe Superintelligence, Anysphere (Cursor), Groq, Revolut, ByteDance, Canva, Epic Games, Discord, Fanatics, Kraken,
  Ramp, Rippling, Deel, Vercel, Cerebras …); a figure that cannot be sourced is left blank, never guessed. The owner can "Record a
  reported round" (dialog: date, valuation, price per share, source URL) and "Add a private company" the same way; a company that
  lists switches its mark to the live ticker from the listing date.
- Trading: "Buy secondary shares" / "Participate in round" / "Sell secondary": the owner enters the quantity and the NEGOTIATED
  price per share (default = the last round's implied price; a discount/premium field), the placement/broker fee (default 3 %,
  editable), the settlement lag (default T+30 calendar days, pending until then) and the lock-up (default 6 months; the ticket
  shows it and the Sell ticket warns while it runs). There are no invented counterparties or fills: the trade executes at the
  owner's negotiated price because private secondaries are negotiated, and the preview says so in one sentence.
- Marks: `private-last-round` quality = the last reported round's implied price per share (or valuation × owned fraction when
  only the valuation is known); it changes ONLY when a new reported round is recorded or the owner records a secondary indication
  with a source. Shown with its as-of date; never dimmed as stale (private marks are expected to be old) but labelled "last round
  <date>". P&L = mark − cost; realised on sale.
- Risk: a separate "Private / illiquid" bucket (% of NAV, concentration, lock-up calendar); liquidity shows these as illiquid;
  no VaR shocks applied to private marks (disclosed). Compliance notices: accredited/QP status, Rule 144 holding periods,
  Form D is the issuer's filing (informational), 13F does not cover private shares (informational).
- Private credit (direct loans to such companies, floating coupon = policy rate + spread, OID accretion, amendment fees,
  quarterly payments) lives in Credit Instruments as already specified and may reference the same company master.

## Owner amendment (2026-10-20 18:28 ET): NO Glossary
The owner: "glossary can just be removed". Drop the Glossary page, its sidebar entry, the glossary data module and any gl()
term links. WP-25 (Glossary data) is CANCELLED; WP-12 (UI) builds no Glossary page and does not depend on WP-25; WP-15
(integration) deletes any glossary file that was already written and removes every reference. Conventions that B16 wanted
"stated on screen in one sentence" stay as inline one-sentence notes on the pages where they matter (not in a glossary).

## 5. Unsupported in this build (shown as such in the UI, never fabricated)

Private equity DEAL FLOW (buyouts), investment banking, clients/institutions/investors, careers/missions,
MBS/structured, exotic OTC (variance/vol/dividend/inflation swaps, barriers), commodity physicals, crypto.

## 6. Verification bar before deploy

Focused acceptance tests (hand-worked numbers), restart persistence, rollback on failure, duplicate-server
prevention, stale-quote and session blocking, ledger/NAV reconciliation; compatibility runs of the original
FinSim subset and the FinSim2 suites (baseline captured before changes, in the scratchpad `baseline/` folder);
browser check of the real UI with console/API error review; the old `finsim.db` byte-identical afterwards.

## 7. Answers to the design's open questions (Claude, 2026-10-20 17:58 ET — binding for the build)

1. **Futures per-contract symbols on Yahoo:** verified 17:55 ET — `ESZ26.CME`, `NQZ26.CME`, `6EZ26.CME`, `ZNZ26.CBT`, `GCZ26.CMX`
   all answer (v8 chart). Use `<root><month><yy>.<CME|CBT|NYM|CMX>`; fall back to the continuous `=F` symbol marked LIMITED only
   when a contract symbol fails.
2. **CME/SIFMA holidays:** approximate with the NYSE list from `data/nyse-holidays.json` (futures early close 13:00, bonds 14:00
   on NYSE early-close days); Settings may add overrides. Stated on screen as a convention.
3. **Dividends:** accrue a receivable on the ex-date from Yahoo's dividend events; cash on the pay date when the feed carries it,
   else ex-date + 15 business days (convention shown on screen). Short positions pay the same amount in lieu.
4. **Static margin and position-limit tables:** acceptable, labelled "assumed — verify current exchange values" in the UI and
   editable in Settings. Maintenance margin = 90 % of initial unless the table says otherwise.
5. **FX result:** one P&L bucket ("FX") so the P&L explain ties exactly. Acceptable.
6. **Short-option margin:** a RESERVED requirement that reduces available cash and buying power (no cash movement), like a real
   broker; futures initial margin IS moved to the clearing account (1300) and variation margin settles in cash daily.
7. **Not simulated (disclosed limitations):** early assignment before expiry, recalls beyond observable hard-to-borrow data,
   forced liquidation. Recalls appear only as notices from observable data.
8. **Treasury idle cash:** interest accrues daily and pays monthly at SOFR − 0.50 % on USD balances only (0 % on other
   currencies, disclosed).
9. **Cboe User-Agent:** verified 17:55 ET — cdn.cboe.com answers an honest UA. Use `FinSimTerminal/1.0 (paper trading;
   python-urllib)` for Cboe (and every non-Yahoo, non-.gov host); the browser UA stays Yahoo-only; `.gov` keeps
   `LoganTerminal/1.0 (loganshaffer87@gmail.com)`.
10. **Market orders outside the session:** accepted and queued; they fill at the first fresh quote after the open (build prompt
    B7). The preview says "works at the open" with the session time; a DAY market order entered after the close works at the next
    session's open and expires at that session's close.
11. **Sweep-ins:** fully automatic at any size, shown in the preview and linked in Transactions (directive amendment -7).
12. **Short-sale collateral convention:** the short's sale proceeds are RESTRICTED (not spendable) and, together with a 2 % top-up
    from the account (swept from Treasury if needed), form the 102 % cash collateral posted to the lender at borrow time; the
    collateral is marked daily both ways (calls paid by sweep, excess returned); the proceeds unlock when the short is covered and
    the borrow returned.
13. **Index options (SPX/SPXW, 13 MB chain):** fetched on demand only, reduced at parse to strikes within ±20 % of spot and
    expiries ≤ 1 year, cached 120 s; never fetched in bulk.
14. **Currency loans:** USD, EUR, GBP, JPY, CHF, CAD, AUD at policy rate + 0.75 % (SOFR, ECB deposit rate, SONIA, BoJ, SNB, BoC,
    RBA from FRED/OECD proxies — monthly proxies are labelled as such); other currencies unsupported.
15. **Legacy mode:** `python -m finsim legacy --port 8766` runs the OLD simulation server unchanged (it backs up finsim.db at
    start as it always did) and prints a warning that it is an archive; no further read-only enforcement.
16. **Foreign bonds and FX options:** unsupported in this build (shown as such); foreign listings, FX spot and FX forwards are
    supported.

17. **Process shape (shared-machine guard, confirmed 19:00 ET):** the served process is exactly
    `pythonw.exe -m finsim serve --db <terminal.db> --host 127.0.0.1 --port 8765` (the capture guard exempts command lines
    matching `(^|\s)-m\s+finsim\s+serve\s.*--port\s+8765(\s|$)`). The server must NEVER spawn child Python processes or
    shells (no per-request helpers, no subprocess for quotes or jobs): quote workers, matcher and lifecycle are THREADS in the
    one process. Keep resident memory small (target < 300 MB; bounded caches). Only the launcher's `open` may spawn the browser.
