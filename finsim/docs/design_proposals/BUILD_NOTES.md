# Cross-package build notes (first-deploy integration, 2026-10-21 ~01:40-02:00 ET)

One entry per edit made outside the package that owns the file, with the reason. Packages still being built by other agents
(WP-10 bonds, WP-17 PB OTC swaps, WP-18 dividends, WP-21 private credit, WP-23 lifetimes/reset, WP-26 TRS/XCCY) were not
touched; their absence is tolerated (health `modules` says `missing`, the API answers 501 NOT_BUILT with the package id).

## Integration edits (first-deploy integrator)

1. `tests/test_terminal_app.py` — `test_main_serve_refusals_exit_5_before_the_server_is_imported` popped
   `finsim.terminal.server` from `sys.modules` for the duration of the test and restores it afterwards. Reason: the test
   asserts the refusals never import the server, but `test_terminal_server` imports it at discovery time in the same
   process; the whole-suite run at 01:34 failed on that alone (passes alone). The restore keeps the module object that
   `test_terminal_server` holds.
2. `finsim/terminal/server.py` `_bind_optional` — (a) every optional module that exposes `bind(services)` is bound after it is
   set on the runtime (lending exposes itself; **prime installs its `Prime` service and the accounting `pb_headroom_fn`
   hook**). Before, only lending was bound, so `services.prime` was the bare module: `execution._schedule_for` /
   `credit.pb_schedule` found no `.schedule` (PB accounts fell back to default fees) and the headroom hook was never set.
   A failed bind is recorded as `error: bind: ...` in health, never hidden. (b) `lifecycle.register(bus, rt)` is now called
   when the lifecycle module imports (after the product modules, as `tests/test_terminal_lifecycle.py` wires it): the
   `lifecycle.*` command kinds were never on the bus in a served process, so every scheduler job would have failed with an
   unknown command.
3. `finsim/terminal/matcher.py`, `finsim/terminal/quotes/worker.py`, `finsim/terminal/lifecycle.py` — the `threading.Event`
   stored as `self._stop` is renamed `self._stop_event` (WP-11's core interface request). On 3.12 the attribute shadowed
   `threading.Thread._stop`, so `is_alive()` / `join()` raised `TypeError` once the thread had finished and
   `QuoteWorker.stop()` could not reach its own persist. `api.thread_alive` / `server._join` keep their tolerance (harmless).
4. `finsim/terminal/orders_preview.py` `_validate` — the OVERSELL / CLOSE_ONLY_MISMATCH position check is skipped for
   `inst.kind == "FUTURE"` (WP-04's interface request): futures have no borrow, a SELL from flat opens a short and a SELL
   beyond the long flips; `futures.apply_fill` books the signed quantity (tested in `test_terminal_futures`
   `test_short_and_flip_fill_path`). Equities / ETFs / options keep the check.
5. `finsim/terminal/orders_preview.py` — new `_reserved_shares()` helper; a plain `SELL` now subtracts the shares reserved
   by covered calls (`options.reserved_shares`, WP-03's SHARES_RESERVED request) from the sellable long; the rejection
   carries `reserved_shares` in its details and names the convention in the hint. Absent-safe (0 when the options module is
   missing, exposes no such function, or the lookup fails).
6. `finsim/terminal/api_products.py` `private_companies` — passes `account_id` (query `account_id`) to
   `private.companies_view` (WP-19's interface ask) so the page's eligibility / holdings columns are per account.
7. `finsim/terminal/quotes/worker.py` — `_id_safe()` helper; the resolver's `instruments.upsert` request id is built from
   the sanitised symbol (`inst:upsert:ES_F:<epoch>`), and `_rid()` sanitises its prefix. Found by the smoke test: resolving
   `ES=F` (and any `=X` FX symbol) failed with `request_id must match ^[A-Za-z0-9:_.-]{8,80}$` because the symbol's `=`
   is not allowed in a request id. The payload still carries the real symbol.
8. `finsim/terminal/instruments.py` `upsert_from_yahoo_meta` — Yahoo meta with `instrumentType == "FUTURE"` (the continuous
   `<root>=F` symbol) is recorded as a `kind="INDEX"` reference row, `support="LIMITED"`, no account kinds, session group of
   the root's spec when known, with a note that the listed contract (`ESZ26`) is the tradeable instrument (decisions s7.1).
   Before, it was classified UNSUPPORTED "foreign listings and crypto" (kind EQUITY, group FOREIGN), so `/api/instruments/
   resolve?symbol=ES=F` answered 400. Never an order: the preview rejects it NOT_TRADEABLE_HERE.
9. `finsim/terminal/static/index.html` — the header tag now reads `PAPER TRADING` (was `PAPER` with the sentence only in the
   title); the title sentence is unchanged. `test_terminal_static` still 8/8.

## Interface requests from BUILD_LOG.md and their status

- WP-04 "services.futures must be bound" — bound by `_bind_optional` (module attribute); "validate() OVERSELLs a flat futures
  SELL" — fixed (edit 4).
- WP-03 "validate should subtract options.reserved_shares" — done (edit 5); "_attach_hedge forward hedge.edits" — no change
  needed: `hedges.package` reads `hedge.edits` from the command payload itself; "options.register(bus), bind services.options /
  services.hedges, routes /api/hedges, /api/instruments/{id}/chain, /api/options/exercise" — all present (WP-11 routes,
  `_bind_optional`).
- WP-06 "lending.register(bus), lending.bind(services), repo.register(bus)" — done by `_bind_optional`.
- WP-07 "prime.register/bind at startup" — register was done, bind was NOT (fixed, edit 2a); routes present.
- WP-08 "bind services.lifecycle + product modules, call lifecycle.register(bus, services), start the thread" — register was
  missing (fixed, edit 2b); the scheduler is built in `build_runtime` and started by `start_workers`; smoke: `lifecycle.alive`
  true, no job errors in `terminal.log`.
- WP-09 "services.risk / services.compliance must be bound" — bound (module attributes); routes present.
- WP-11 "rename _stop to _stop_event" — done (edit 3).
- WP-19 "api_products.private_companies should pass account_id" — done (edit 6); "WP-08 HOUSEKEEPING should call
  private.reseed(tx, services)" — NOT wired (WP-08's housekeeping job does not know the private module); open.
- WP-20 "route /api/portfolio/performance" — present; "RateStore.sofr() returning a decimal fraction" — `RateStore.sofr()`
  exists and `performance.sofr_input` consumes `(date, Decimal)`; not re-verified against a live FRED print here.
- WP-22 "runtime.news bound before the QuoteWorker" — done in `build_runtime`.
- WP-24 "services.strategies binding + routes" — present; "WP-02 ensure_instruments_tx + TRS legs to the OTC desk" — the
  first exists in `strategies.py`; the TRS leg hand-off waits for WP-17/26.
- WP-13 "ticket CSS injected from ticket.js (move to terminal.css)" — cosmetic, not done.
- Core observations left open (no behaviour change made): `accounting.reserved_c` double-counts a resting SELL_SHORT whose
  collateral already left 1010 (WP-06); `accounting.pb_headroom_c` counts a positive swap PV as long market value (WP-09);
  `accounting.py` is 1,344 lines (over the ~900 guideline).


# WP-15 integration (2026-10-21 ~11:05-13:00 ET) — resolutions

The integrator owns this log entry (TERMINAL_WORKPLAN.md WP-15: "resolve every BUILD_NOTES item").  Every item below is
either resolved (with the file, the change and the test that pins it) or left open with the reason.  Tests named here are
`tests/test_terminal_integration.py` (the interface requests) and `tests/test_terminal_reconcile.py` (D1-D12 and the §19.12
reconciliation through the real runtime and the HTTP API).

## Interface requests relayed with this run

1. **Private Markets lists SpaceX** (the owner: "spacex is in private markets for some reason"; WP-17's request to WP-19).
   Verified the 09:46 fix: `private.companies()` leaves every company that LISTED (status PUBLIC + ticker, from its listing
   date) or was ACQUIRED out; the page shows them only in a small "no longer private" panel whose line ("Listed 2026-06-12:
   SPCX on Nasdaq - trade it in US Exchange") opens the listed shares in US Exchange; pre-listing lots stay sellable at the
   listed mark.  Added: the API markets view of a PRIVATE account (`GET /api/accounts/{lid}:PRIVATE/markets`) now lists
   only private names (plus listed names the account still holds) with a `listed` list beside them, and no longer lists
   private-credit loan instruments as companies (`api_products.markets_view`).  Pinned: `test_terminal_reconcile.
   test_private_markets_lists_no_listed_company_and_the_api_wiring` (21 private names, SpaceX / Cerebras / xAI /
   Anysphere only under `listed`, SpaceX's sentence exact).
2. **strategies TRS availability** — `strategies.availability` also requires `services.swaps_trs` (a TRS template was shown
   available with WP-17 alone, then `package_legs` hit 501 NOT_BUILT).  The same rule now gates the hedge panel's
   `TRS_OVERLAY` (`hedges._trs_overlay`).  Tests: `test_terminal_strategies` (new reason text), `test_terminal_hedges`.
3. **SWAP_RESET sentence** — `tables.CONVENTIONS["SWAP_RESET"]` now says "or compound SOFR daily in arrears when booked so"
   (COMPOUND_ARREARS is live).  The UI fixture `tests/fixtures/terminal/api/state.json` keeps the old copy (fixtures are
   not the integrator's files; no test compares them).
4. **WP-26 term names and hooks** — verified, no change needed: `swaps_trs` accepts the ticket's TRS terms
   `{underlying_id, units, receiver, reset_months, tenor_months}` and XCCY terms `{pay_ccy, recv_ccy, pay_notional_c,
   recv_notional_c|null, pay_rate|null, recv_rate|null, pay_basis, recv_basis, months, mtm_reset}`; exposes `preview`,
   `book`, `terminate`, `mark_trs`, `mark_xccy`, `dv01_c`, `accrue_night` (coupon legs through `swaps.accrue_leg`), `pay`,
   `fix`, `next_events`, `contract_detail`; a TRS reset re-bases `mark_c` / `vm_balance_c` and writes the RESET row with
   `entry_id` and `detail.post_mark_c = 0`.  Added `detail.command_id` to the RESET row (item 12).
5. **accounting SWAP rows** — the row's mark is now the overlay NAV carries (the mark at the last VM; 0 after an STM
   settle), so mark and market value agree; the live model mark is in `extra.model_mark` and in the position drawer
   ("swap marks": overlay, live model mark, VM balance, IM).  Pinned: `test_terminal_reconcile.test_d10_...` (row 40,316.09
   = market value while the live mark is -4,491.29).
6. **server / api_products** — none needed (confirmed: `_bind_optional` binds `swaps` and `swaps_trs`, the `/api/otc/*`
   routes, and SWAP_FIX / SWAP_PAY / SWAP_VM / TRS_RESET / XCCY_EVENTS / ACCRUE_DAILY swaprcv / swappay / trsfin work).
7. **MARKS_RECORD records TRS underlyings and XCCY FX pairs** — new `instruments.otc_reference_ids(conn)` (the underlyings
   of OPEN TRS contracts, the USD pairs of OPEN cross-currency swaps, with the observing accounts); `lifecycle_eod.
   recorded_set` adds them, so TRS_RESET's CLOSE and the XCCY FX_RESET's 17:00 close are recorded (and back-filled from
   daily bars after downtime) for names nobody holds; HOUSEKEEPING keeps their cached quotes.
8. **Quote pins and dividends for them** — `QuoteWorker._scan_pins` pins `otc_reference_ids` at HELD priority (their marks
   drive SWAP_VM, the NAV overlay and the resets); `RatesRefresher.held_names` adds the TRS underlyings (with the PB
   account) so the dividends slot fetches the events a TRS receiver is owed.  Pinned: `test_terminal_integration.
   test_trs_underlying_and_xccy_pair_are_recorded_and_pinned`, `test_terminal_reconcile.test_d10_...`.
9. **SWAP_VM valued as of its date** — `swaps.h_vm` calls `mark_trs` / `mark_xccy` with `valuation_date = D` (signature
   checked, never a TypeError fallback); `mark_xccy` now prices FX at the pair's recorded / observed 17:00 close of D
   (`fx_close_on`), as `mark_trs` already did with the underlying's close.  When an observed input (the underlying's or the
   FX quote, the USD curve) is dated after D the key ends CAUGHT_UP (quiet, terminal), as the IRS curve rule does — never a
   VM at another day's price.  Pinned: `test_terminal_integration.test_trs_vm_values_at_the_close_of_its_own_date_or_catches_up`
   (15,000.00 at the recorded 341.00 close, not 35,000.00 at the next day's 343.00) and `test_xccy_vm_uses_the_recorded_fx_
   close_of_its_date` (-4,330.67 at 1.1300, delta -12.02).
10. **contract_view for TRS / XCCY** — merges `swaps_trs.contract_detail` as `detail`, takes `side` from it
    (RECEIVE_TOTAL_RETURN / PAY_TOTAL_RETURN; RECEIVE_USD_PAY_EUR …), clears the IRS-only `pay_fixed` / `fixed_rate`, and
    adds `balances_by_ccy` (accrued legs, notional lent / borrowed, VM, IM per currency: an XCCY's EUR accruals and
    notional are visible).  Pinned: `test_contract_view_shows_the_trs_specifics`, `..._xccy_legs_per_currency`.
11. **swapticket.js** — TRS preview (observed reset price with its freshness, notional, financing rate and direction, SOFR,
    per night, reset dates, indicative financing per period, IM rule); XCCY preview (deal FX with freshness, both notionals,
    the start / maturity exchanges, MTM reset dates, per-leg table: rate dealt vs PB quote vs model par, basis, first
    coupon, value, DV01, role; schedule per currency; funding per currency); blank XCCY rates = the PB quote, pay / receive
    bases, either notional; kind-aware confirmation; the contracts table shows the TRS reset price / XCCY leg rates and the
    accrued legs per currency; the history shows TRS resets, XCCY exchanges / coupons / FX resets.  Checked by executing
    every path on real encoded payloads under a JScript harness (scratchpad `wp15/js`), no exception.
12. **VM reference order** — `swaps._reference_mark_c` orders the latest RESET against the latest VM by COMMAND (the
    RESET's journal entry's command, else `detail.command_id`), so a reset that posted no entry still re-bases the
    reference; `swaps_trs._vm_reference` now calls it (one definition).  Pinned: `test_vm_reference_rebased_by_a_reset_
    that_posted_no_entry`.
13. **IRS OFF_MARKET** — `swaps._deal` refuses a client fixed rate better than the PB quote (pay below par + hw, receive
    above par - hw) with 400 OFF_MARKET, as the XCCY quotes do; a worse rate deals as given.  Pinned: `test_terminal_
    integration.test_a_rate_better_than_the_quote_is_refused_and_a_worse_one_deals`.
14. **performance SOFR fraction** — already fixed at 10:14 (`performance.sofr_input` reads `RateStore.sofr_fraction()`);
    verified, no change.

## Items from BUILD_LOG.md resolved here

- **WP-10 bonds (integrator items, 02:08)**: the four ProductJobs BOND_ACCRUE / BOND_COUPON / BOND_MATURITY / BOND_LISTING
  are in `lifecycle_eod.REGISTRY` (accrual after ACCRUE_DAILY; coupon, maturity and the monthly benchmark listing after
  PC_PAY); `build2_bonds` is a setting (`api.SETTING_KEYS`, boolean, default off); `POST /api/bonds/trade` runs
  `bonds.trade` (400 UNSUPPORTED "model price, not executable in this build" while the flag is off); the order ticket
  refuses a BOND with that sentence (`orders_preview`, 400 UNSUPPORTED naming the route and the setting).
  `test_terminal_lifecycle` (registry order, 28 jobs) and `test_terminal_bonds` (the scheduler test now uses the REGISTRY's
  jobs) updated; `test_terminal_reconcile` pins the route, the flag and the ticket refusal.
- **WP-19 / WP-08 "HOUSEKEEPING should call private.reseed"**: done (`lifecycle_eod.Housekeeping`: `services.private.reseed`
  when bound — the master sync with forward status moves and the held-listed pins; `_seed_private` otherwise).  Pinned:
  `test_terminal_integration.HousekeepingPrivateSyncTests`.
- **`POST /api/jobs/run {job}`** ran the whole tick (the scheduler has no `run_job`): it now runs that one job
  (`tick(only=job)`) and answers 400 for an unknown name — needed for "run the SWAP_VM job through the jobs API".
- **risk.py PB headroom row** (10:16 OPEN item): the PB row and the margin row show the §12.4 gross headroom their formula
  describes; `headroom_after_committed_c` carries what is left for a new order.
- **tables._PB_FIELDS_NOTE** (10:14 OPEN item): names `equity_min` / `monthly_minimum` (USD), the wire names.
- **WP-18 "positions view attaches dividends.position_notes"**: the position detail (`GET /api/accounts/{id}/positions/{iid}`)
  carries `dividend_notes`, shown in the drawer with the DIVIDEND sentence.

## Left open (reason)

- `accounting.reserved_c` reserves 1.02 x remaining x last for a resting SELL_SHORT whose collateral already left 1010
  (WP-06): conservative (it can only refuse an order for cash the account would have had); changing the reservation rule
  moves every short's fundability and needs its own tests — not in an integration pass.
- `accounting.pb_headroom_c` fallback counts a positive swap PV as long value (WP-09): never runs in a served process —
  `prime.bind` installs `pb_headroom_fn` (loan value only on equities / ETFs; swaps, options, futures excluded).
- `accounting.py` length (> ~900 lines): a split needs a new module; behaviour first.
- 409 PREVIEW_STALE carries an unstored preview (the ticket re-previews once; works).
- Ticket CSS injected from `ticket.js` (cosmetic).
- CME / SIFMA calendars approximated by the NYSE list (decisions §7.2, stated on screen).
- The codec keeps `*_c` beside a stripped name that already exists (by design: never overwrite a field).
- `TradingCalendar.holiday_name` (WP-18 nicety: the holiday's name in the pay-date note) — the note gives the dates.
- `orders_preview` / `preview.js` funding block could show `committed` / `treasury_gross` (UI nicety; the 409 text is right).
- `api_products.schedule_get` serves no seed and the PATCH answers without before / after (the editor works; SETTINGS_CHANGED
  events carry before / after).
- Risk's rates scenario stays DV01-linear for swaps (`swaps.mark(shift_bp=)` exists; a full re-mark is a risk-package change).
- XCCY floating legs are NOT_IN_THIS_BUILD (WP-26 deviation: no free non-USD fixing source).
- TRS / XCCY catch-up order: in a catch-up tick SWAP_VM runs before MARKS_RECORD (the design's tick order), so the VM of a
  missed trading day whose close is only back-filled later in that tick ends CAUGHT_UP (the request's rule) and the change is
  measured on the next VM date; collateral is right either way, but under STM the P&L lands on the later business date.  A
  retryable CLOSE_PENDING would stall the contract's later VM keys whenever a close can never be recorded, so it was not done.
