# FinSim Live Terminal — changelog

One plain-English line per change, compiled by the WP-15 integration from `docs/BUILD_LOG.md` (every package's line) and
the resolutions in `docs/design_proposals/BUILD_NOTES.md`.  Newest first.  Versions are `finsim.terminal.VERSION`.

## Unreleased (WP-15 integration, 2026-10-21)

- Private Markets: a company that listed or was acquired (SpaceX as SPCX, Cerebras, xAI, Anysphere) is no longer a private name; the API's Private account markets view now lists only private names (plus listed shares still held) and names the listed ones apart.
- Swaps are wired end to end: IRS, total return and cross-currency swaps book through an eligible prime-broker account's OTC tab, with daily variation margin, fixings, payments, TRS resets and cross-currency exchanges run by the lifecycle job.
- The swap ticket now previews total return swaps (observed reset price, notional, financing rate and direction, reset dates, financing per period) and cross-currency swaps (deal FX rate, each leg's quote and model par, notional exchanges, reset dates, schedule per currency).
- Cross-currency rates may be left blank to deal at the prime broker's quote; either notional may be given; both legs' day counts can be chosen.
- An interest-rate swap at a fixed rate better than the dealer's quote is refused (OFF_MARKET) instead of showing a day-one gain no dealer would give.
- Variation margin on a TRS or a cross-currency swap is valued at the close of its own date (the underlying's close, the pair's 17:00 close); when only a later price exists it catches up instead of using another day's price.
- The variation-margin reference after a TRS reset is ordered by command, so a reset that posted no cash still re-bases it (one shared definition).
- The contracts table shows a TRS's side, underlying and reset price, and a cross-currency swap's legs, accruals and notionals in their own currencies.
- A TRS underlying and a cross-currency swap's FX pair are quoted, recorded at the close and (for a TRS) checked for dividends even when nothing else holds them, so resets and margin never wait on an unwatched name.
- Strategy templates and the hedge panel offer a TRS only when the TRS module is present (no more "available" templates that then answer NOT_BUILT).
- The swap position row shows the value NAV carries (the mark at the last variation margin) as its mark, with the live model mark in the position drawer.
- The position drawer lists the name's dividends (ex-date, pay date, received / accrued / upcoming) with the dividend convention.
- Bonds (Build 2) are wired: nightly accrual, coupons, redemption and the monthly model benchmarks run in the lifecycle; bond trades go through POST /api/bonds/trade behind the build2_bonds setting (off by default); the order ticket refuses a bond with "model price, not executable in this build".
- The housekeeping job now runs the private-company master sync (listings and status moves reach an existing database every night, not only at start).
- "Run job" in the API runs only the job named (it ran every job) and refuses an unknown name.
- The Risk page's prime-broker row shows the full financing headroom its formula describes, with what is left after commitments beside it.
- The swap reset sentence mentions SOFR compounded in arrears; the prime-broker fields note names the wire fields (equity_min, monthly_minimum).
- New tests: the D1-D12 acceptance map and a one-database reconciliation through the real runtime and the API (test_terminal_reconcile), and the integration requests (test_terminal_integration).

## 1.0.3 (fix release, built and gated 2026-10-21 15:03 ET)

- Option limit and stop orders are judged on the contract's chain bid and ask, not on a last trade that may be hours old.
- A failed quote fetch never makes an old price look fresh: during a provider outage prices keep their true age, and stale prices never execute.
- Currency and credit loans accrue interest for every night owed, weekends and holidays included (the maturity payoff skipped nights).
- US Exchange Markets lists every active US stock, ETF, ADR, REIT and preferred in the master (about 760, was 60) and every options underlying (was 30); names not on screen refresh in the background at up to 50 a minute, each about every 20 minutes.
- Quotes are saved only when they change and unwatched ones are pruned after an hour, so the database no longer grows with the number of names listed.
- Order ticket: Close on a long option sells to close (it bought another contract), closing a future previews the margin it releases, and quantity, type, prices and hedges no longer carry over to the next instrument.
- Private Markets and Private Credit pages no longer answer busy (503) with several pages open: valuing marks reuses the open database reader instead of opening a second one.
- Swaps (IRS, TRS, cross-currency) stay out of the deployed release until the overnight swap-termination defect is fixed.

## 1.0.2 (fix release, deployed 2026-10-21 13:15 ET)

- Market orders fill on real Yahoo data: Yahoo's zero-volume placeholder minute and the newest minute that is not final yet no longer read as no trading; the volume cap uses the latest completed minute with volume.
- An option picked on the chain is listed from the cached Cboe chain with its chain quote (it was unknown, or registered as a stock); Yahoo option symbols are refused with the reason.

## 1.0.1 (fix release, deployed 2026-10-21 10:39 ET)

- Foreign listings (London, Hong Kong, Tokyo …) were seeded as US dollar US Exchange stocks with the wrong Yahoo symbols; they are now foreign listings in their own currency (pence handled), shown with live quotes under Foreign Markets & FX and repaired in place at every start.
- Treasury showed cents as dollars (USD 100,000,000.00 for 1,000,000.00); every cents figure the API serves is now a decimal string.
- SOFR showed as 387.00 % in places; rate units are now defined once (published percents vs fractions) and every display follows it.
- Available cash never shows below zero: what working orders need beyond the account's own cash is shown as "reserved (Treasury sweep covers N)".
- A second order can no longer be accepted on cash an earlier order will sweep (it used to be suspended at the open); buying power counts earlier commitments.
- The header clock no longer shows --:--:-- on the first paint.
- Markets refreshes update in place: the filter, sort, selection and scroll stay, and a click is never swallowed by a refresh.
- "status" shows providers that have not been called yet as idle (not DOWN), integrity not yet checked as such, and times in ET.
- The Startup / Desktop / Start Menu folders are found through the Windows known-folder API; non-ASCII folders are handled safely.
- Portfolio -> Performance answered 400 whenever SOFR was loaded; it now reads SOFR as a fraction.
- The prime-broker schedule editor no longer zeroes the minimum commission when another field is saved; bad money values are refused with the field named.
- The currency-loan toast and grid show SOFR 3.87 % (not 387.00 %).
- SpaceX (listed as SPCX on 2026-06-12) and the other listed or acquired companies left the private list; the master was re-verified with sources.

## 1.0.0 (first deployment, 2026-10-21 02:07 ET)

- A new live paper-trading terminal replaces the day-advance simulation: real delayed prices, pretend money, one ledger, no simulated market (finsim/terminal; the old simulation stays as `python -m finsim legacy`).
- Three books from the first run — Personal (1,000,000), Fund (25,000,000), Institutional (1,000,000,000) — each its own lifetime with its own Treasury, accounts and ledger.
- One double-entry ledger in SQLite is the single source of truth; every figure is computed by one accounting module; integrity checks run after every command and on start.
- Every command is one transaction with a request id (retries are safe, nothing is half-written); errors surface with the reason.
- Exactly one server per home: a second one refuses to start; stop / status / install / open / uninstall manage it; a rotating log is written.
- Treasury holds the capital; an account that needs cash gets exactly its shortfall swept from Treasury, linked to the trade; idle cash returns after the close.
- Quotes come from Yahoo (delayed), option chains from Cboe, the Treasury curve from Treasury.gov, rates from FRED; every price shows its freshness; stale prices never execute.
- Orders: market, limit, stop, stop-limit and trailing stops; sessions and holidays from the real calendar; partial fills capped by observed volume; all-or-none option packages.
- Listed options with Reg T-style margin, exercise, assignment and expiry on the real date; futures with initial margin and daily variation margin.
- Short selling borrows first (102 % collateral marked daily both ways, fees and rebates accrued nightly); no borrow means no short.
- FX spot (T+2) and currency loans; repaying a foreign loan needs that currency.
- Prime-broker accounts with editable fee schedules, financing and credit interest, opened through onboarding that records the ISDA Master, CSA and PBA.
- Interest-rate swaps through a prime broker: dealt at the model par rate plus the disclosed half-width, daily variation margin, fixings (term or compounded SOFR), net payments.
- Private Markets for eligible books: researched company master with sources, negotiated secondaries with fees, settlement lag and lock-ups, last-round marks.
- Private credit loans with coupons, OID accretion, fees and quarterly payments; dividends and payments in lieu from Yahoo's events.
- Risk (exposures, greeks, DV01, scenarios, liquidity, per-PB margin) and compliance notices inform and never block.
- Daily News with real headlines and a "Requires your attention" strip; Portfolio performance statistics against SOFR and SPY.
- Reset lifetime closes a book read-only and starts a new one; History browses past lifetimes.
- 60 strategy templates and hedge candidates on the ticket (protective put, collar, put spread, index overlay, TRS overlay).
- No glossary (owner decision); conventions are one sentence on the page where they matter.
