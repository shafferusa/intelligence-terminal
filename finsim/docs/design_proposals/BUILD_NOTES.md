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
