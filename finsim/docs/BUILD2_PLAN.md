# FinSim Live Terminal - Build 2 plan (planner, 2026-10-21 15:56 EDT)

Inputs read: the UI sweep (155 findings, C:\Users\logan\AppData\Local\Temp\claude\C--Users-logan-intelligence-terminal\5e0edd51-72c4-438a-89eb-77c644650257\scratchpad\sweep1_findings_full.json), the 56 code-review findings (review_findings_1021.json), the orchestrator follow-ups ORC-1..10, the owner's asks of 2026-10-21, BUILD_LOG.md (every package, the 1.0.3 fast lane and hotfixes 13:20-15:03), TERMINAL_DECISIONS.md, TERMINAL_STATUS.md, the BUILD_PROMPT owner amendments + B1-B17 and the HANDOFF. The current repo was diffed against the sweep snapshot (scratchpad\staging-sweep): only accounting, api_products, api_views (one reader fix), config, credit, db, marks, orders_preview, orders_rules, private, private_credit, swaps, quotes cache / worker, core.js, markets.js, positions.js and ticket.js changed since the sweep. 1.0.3 went live at 15:42 (BUILD_LOG); per the orchestrator's 15:32 note 1.0.4 is gated as soon as Build + integrate finish.

## Summary

Build-2 plan: 12 packages over disjoint files cover 211 open ids; 10 ids are already fixed by the 1.0.3 fast lane and hotfixes (USX-01/02/03/04/07/15, SHELL-01, OA-01, MONEY-1, TX-6); nothing is deferred and nothing is dismissed - every remaining finding is honestly buildable with free sources verified today (fiscaldata MSPD + auctions, TreasuryDirect FedInvest prices, NY Fed SOFR, ECB data API, MOF JGB yields and the published newspaper index answer; FRED is in maintenance, so P07 adds labelled fallbacks; the Bank of England blocks scripts, so gilts are LIMITED monthly). EXEC-1 and EXEC-2 stay open in P05 only for the remainders the 14:05 gate-check named (orders_preview STOP_SIDE / trailing reference, worker chain errors); PO-02 stays in P09 to be proven with the busy probe. The four owner asks map to P07 (Debt: real Treasuries with CUSIPs, generic corporates, foreign government benchmarks, bond ticket), P06 (FX desk with spot, forwards and crosses, tradable foreign listings with explicit FX conversion), P08 (counterparty directory, loans by lender, repo / reverse repo, securities lending, stock borrows) and P09 + P12 (MONEY-2 fixed so swaps can ship, then OTC equity / FX options and forwards with the PB). Parallelism rests on 27 written contracts: P01 (route, static-stub, schema and instrument hooks), P02 (UI plug-in registry, ticket dispatch, account tab bar), P04 (funding cascade / FX conversion / add_product_job) and P05 (worker refreshers, preview hooks, add_conventions) build their hooks as their first step; product packages register plug-ins instead of editing shared files. Suggested start order: P01, P02, P05, P07, then P04, P06, P09, P08, then P12 (after P09), P10 (after P02), P03, P11 (after P07 and P08). SR-13 (option margin as a reservation, decision s7.6) needs a one-time migration of posted 1310 balances on the live database; the orchestrator should know before deploying it.

## Sources verified by the planner (2026-10-21 15:36-15:42 EDT)

- api.fiscaldata.treasury.gov v1/accounting/od/auctions_query - 200 (CUSIP, type, term, dates, rate, TIPS flag; upcoming auctions included)
- api.fiscaldata.treasury.gov v1/debt/mspd/mspd_table_3_market - 200 (every outstanding marketable security; record 2026-09-30)
- www.treasurydirect.gov/GA-FI/FedInvest/todaySecurityPriceDetail - 200 (prices for 2026-10-21, 462 CUSIPs, BUY / SELL / END OF DAY)
- fred.stlouisfed.org fredgraph.csv and api.stlouisfed.org (FRED_API_KEY route) - both answer the HTML 'Site Maintenance' page
- markets.newyorkfed.org/api/rates/secured/sofr/last/2.json - 200 (SOFR 3.88 % for 2026-10-20)
- data-api.ecb.europa.eu FM deposit facility rate and YC euro-area AAA 10Y spot - 200
- www.mof.go.jp jgbcme.csv - 200 (JGB yields 1Y-40Y to 2026-10-20)
- www.bankofengland.co.uk IADB CSV - 403 Access Denied (anti-bot: never circumvent; gilts stay LIMITED monthly from FRED)
- query1.finance.yahoo.com v8 chart EURJPY=X and ^TNX - 200
- shafferusa.github.io/intelligence-terminal/reports/index.json - 200 (201 entries, newest 2026-10-21 am)

## Rules for every package

1. Ownership: edit only the files in your owns list (new files included). One owner per file across Build 2. Requests for other files
   go to requests_for_others with the exact edit. The hooks in 'Contracts' are their owner's FIRST step.
2. Tests: the existing suite stays green (py -3.12 C:\Users\logan\AppData\Local\Temp\claude\C--Users-logan-intelligence-terminal\5e0edd51-72c4-438a-89eb-77c644650257\scratchpad\run_suite.py). Update tests you own when behaviour changes on purpose
   (money values hand-worked, arithmetic in comments). The owner does not want time spent on new unit tests except money math.
3. Honesty: no simulation, no invented prices or fills, no fabricated data. Every price / mark shows source, as-of and quality; a
   missing free source is shown as LIMITED / unavailable with the reason and its inputs; real institution names only with ASSUMED,
   editable terms labelled as such. "Not in this build" only where a product cannot be built honestly (say why).
4. UI: vanilla JS, ASCII, no CDN, no frameworks; trader wording (no endpoints, package ids, internal codes or doc references on
   screen); new tables fit 1440 px with the dock open (actions visible) and do not clip at 1100 px; 0 console errors.
5. Verify as in each task (fresh temp homes, FakeProvider for fills, real data read-only and gently, Playwright py-3.14, one server
   and one browser per agent, POST /api/shutdown + tasklist).
6. Market hours: after 16:00 ET US equity / option orders rest or are refused with the reason (never filled); FX on weekdays; CME
   evening session from 18:00 ET; US bonds 08:00-17:00 ET.
7. Never deploy, never touch %USERPROFILE%\.finsim or ports 8765 / 8865, no git, never change finsim.terminal.VERSION, never stop a
   process you did not start.
8. End with ONE BUILD_LOG line (clock time) and the structured result.

## Contracts (hooks and cross-package interfaces)

Hooks are built FIRST by their owner (within its first ~30 minutes) so the packages running in parallel can use them. A consumer
whose hook has not landed yet codes against the contract below and verifies once it is in the tree (it never edits the owner's file).

C1 (P01, api.py Router) product routes: Router.__init__ first calls `register_routes(router)` on every bound optional product
   module that defines it (server.OPTIONAL_MODULES order; rt.<attr>), THEN api_views / api_products. First match wins, so a product
   route can take over a path. Handlers take `req` (req.rt, req.q(), req.body, req.params, req.now, req.lifetime()); product modules
   import api helpers (ApiError, NotFound, run_command, ok_or_raise, quote_dict, mark_dict) lazily inside register_routes.
   Route prefixes: P06 /api/fx/*, P07 /api/debt/* + /api/bonds/*, P08 /api/credit/*, P11 /api/private/*, P12 /api/otc/options*,
   /api/accounts/{id}/close.
C2 (P01, server.OPTIONAL_MODULES) add "otc_options": "products.otc_options" (P12), "counterparties": "counterparties" (P08),
   "treasuries": "products.treasuries" (P07, optional). A missing module stays "missing" (tolerated).
C3 (P01, api.py static route) planned optional UI files, served as 200 `/* <path>: not built yet */` (application/javascript)
   while missing, so no 404 reaches the console: pages/markets_debt.js, components/bondticket.js (P07); pages/markets_fx.js,
   components/fxdesk.js (P06); pages/markets_credit.js, components/creditdesk.js (P08); pages/markets_otc.js, components/otcticket.js
   (P12). P02 puts the same eight in index.html (data-optional) and in tests/terminal_fixture_server.py OPTIONAL_STATIC.
C4 (P01, db.py) `Database.ensure_schema_ext(owner, statements)`: idempotent additive DDL on the writer (CREATE TABLE/INDEX IF NOT
   EXISTS; `ALTER TABLE t ADD COLUMN c ...` applied only when PRAGMA table_info lacks c), logged once, callable from a product's
   bind(rt). At open, otc_contracts.kind CHECK is widened (idempotent rebuild only when the stored DDL lacks them) to
   IRS, TRS, XCCY, EQ_OPTION, FX_OPTION, EQ_FORWARD, FX_FORWARD. Database.reader() re-entrant per thread (ORC-7). No SCHEMA_VERSION
   change (the file stays readable by 1.0.3).
C5 (P01, api_products instrument route) if the kind's module defines `instrument_extra(rt, inst, now) -> dict` (BOND -> rt.bonds,
   PRIVATE -> rt.private, FX -> rt.fx, SWAP and OTC kinds -> rt.swaps / rt.otc_options) merge it as `extra` (headline {value,
   quality, as_of, source, note}, spec_view [{label, value}], actions); BOND / PRIVATE / SWAP / OTC rows are never subscribed to
   Yahoo (quote null, no 'up to 15 min delayed' record).
C6 (P01, GET /api/instruments) for a PRIVATE account drop rows `rt.private.tradeable_private(rt, iid)` refuses (C23).
C7 (P01, order preview / submit) 409 BOOK_MISMATCH when ?book= names a book that does not own account_id.
C8 (P01, bars route) full-session 1-minute bars through the P05 worker request (C14): 202 + retry_ms while fetching, then every
   bar of the session.
C9 (P01, schedule GET/PATCH) serve `seed` (prime.seed_schedule) and `fields` (prime.FIELD_SPECS); PATCH answers before/after.
C10 (P01, /api/state) `prime_brokers` = prime.brokers() with each dealer's seed schedule; `conventions` BOOKS / QP sentences built
   from book_defaults, qp_threshold and pb_otc_threshold.
C11 (P02, UI shell) index.html optional tags (C3 list); core.js `T.marketPlugins = {}` and `T.ticketKinds = {}`; T.ticket.open
   dispatches on fields.kind (else the cached instrument kind; GET /api/instruments/{id} once when unknown) to
   T.ticketKinds[kind](dockBody, fields) when registered, else components/ticket.js; markets.js render() delegates to
   T.marketPlugins[acct.kind].render(el, ctx {account_id, acct, kind, role, tab, book, setTab}) (for PB only the tabs the plugin
   lists, e.g. ['otc']); an account tab bar (Markets / Positions / Orders / Cash & financing / Transactions) on every account page
   incl. plugin pages; helpers T.providerLabel(name), T.help.renderKeys(el), T.attention.expand(); the router passes an optional
   ?query on #/activity and #/risk to the page; T.api errors expose incident_id from error.details.
   Plugins register: DEBT (P07), FOREIGN_FX (P06), CREDIT (P08), PB 'otc' (P12); ticket kinds BOND (P07), FX (P06),
   PRIVATE (P11), SWAP + EQ_OPTION/FX_OPTION/EQ_FORWARD/FX_FORWARD (P09/P12).
C12 (P04, execution.fund) (a) cascade: own available -> ONE linked SWEEP_IN of min(shortfall, Treasury available) -> the rest from PB
   headroom -> else refuse / overdraft rule (MONEY-4); (b) `fx_convert=True`: for a non-USD need the account and Treasury cannot
   cover in that currency, call services.fx.convert_for(ctx, account_id, ccy, need_c, link_type, link_id, settle_date) (C17) for
   the remainder, then fund the USD side as usual; (c) `commitments=True` (OTC IM, PO-07): the need is measured against available
   (settled + receivables due by the date - payables - reserved), not settled cash, and the shortfall is swept at booking.
   fee_for adds services.fx.venue_fee_c(inst, side, qty, gross_c) for foreign listings as a separate disclosed fee line (C25).
C13 (P04, lifecycle_eod) `add_product_job(job, before="SNAPSHOT")`, idempotent by job name, callable from a product's bind(rt)
   (product modules are bound before the scheduler is built). Users: P08 (repo / securities-lending jobs), P12 (OTC expiry), P07
   if it needs a ledger job. P04 itself adds LOAN_MATURE (credit.loan_mature for loans with a maturity date).
C14 (P05, quotes/worker.py) module-level `register_refresher(name, interval_s, fn, run_after_et=None)`: the QuoteWorker runs fn(services,
   now) on its fetch threads (never a request thread, never inside the writer), records ok / error in provider health under `name`
   (shown in Data Status), respects offline mode (fn gets services.providers; the FakeProvider in offline) and backs off on errors.
   Plus `QuoteWorker.request_bars(iid)` (like request_chain) keeping the full-session 1-minute bars of the last fetch for C8.
C15 (P05, orders_preview + matcher) FOREIGN session groups are judged on the quote record's provider_session
   (cache.session_for(rec, now)) in preview and matcher; a non-USD order the account + Treasury cannot fund in its currency gets
   `funding.fx_conversion` = services.fx.conversion_plan(...) (C17) and, when the client sends fx_convert: true, the stored preview
   carries it and the fill calls execution.fund(..., fx_convert=True); kinds FX / BOND / PRIVATE / SWAP / OTC are refused at PREVIEW
   (not at submit) with the pointer to their own ticket; hedge legs' cash is in the funding block (USX-17); a TRS_OVERLAY hedge
   candidate is refused at preview as an order leg (PO-10).
C16 (P05, tables.py) `add_conventions(dict)` (product sentences register at import; setdefault semantics) and the option sentence in
   STOP_RULE / LIMIT_RULE / TRAILING. Other packages never edit tables.py; constants of their own live in their own module.
C17 (P06, fx.py) `conversion_plan(services, snapshot, account_id, ccy, need_c, settle_date, conn=None) -> dict | None` (pure: pair,
   deal rate, USD amount, value date, spread cost, executable + reason); `convert_for(ctx, account_id, ccy, need_c, link_type, link_id,
   settle_date, services=None) -> dict` (books a linked USD->ccy spot deal in that account; value date <= settle_date else 409
   FX_VALUE_DATE); `forward_rate(services, pair, value_date, snapshot=None) -> (Decimal | None, inputs)` (the canonical CIP forward;
   P12 uses it); `venue_fee_c(inst, side, qty, gross_c) -> (cents, note)`.
C18 (P07, bonds.py) `dv01_c(services, inst, face, snapshot=None) -> (cents per bp, inputs)` and `reprice_c(services, inst, face, bp,
   snapshot=None) -> cents` for Risk (P11) and hedges (P10); `T.components.bondlist.render(el, {account_id, scope: 'FOREIGN'})`
   (in bondticket.js) for the Foreign bonds tab of P06's plugin.
C19 (P06/P07) Foreign government bonds trade in the Foreign Markets & FX account (account kind FOREIGN_FX), priced and booked by
   P07's bond engine, shown in P06's plugin tab through C18's bondlist; settlement in the bond's currency funded through C17.
C20 (P08, counterparties.py) `exposure(services, lifetime_id, snapshot=None) -> [{counterparty_id, name, type, products, exposure_c,
   collateral_c, net_c}]` and GET /api/credit/exposure?book= for Risk (P11).
C21 (P09, products/swaps.py + swapticket.js) `_delegate` routes EQ_OPTION / FX_OPTION / EQ_FORWARD / FX_FORWARD to
   services.otc_options with the same hook names as swaps_trs (preview, book, terminate, mark, dv01_c, next_events, event keys);
   SWAP_VM values them through the delegate; the contracts table renders any kind generically (kind label + side sentence from the
   row); T.components.swapticket exposes mount(el, {account_id, notes}), prefill(kind, terms) and terminate(contract_id).
C22 (P12, prime.py) `im_params(services, account_id) -> {irs_im_bp, trs_im_pct, xccy_im_pct, option_im_pct, forward_im_pct}`
   (defaults = today's constants 50 bp of DV01 / 10 % / 2 %), FIELD_SPECS (label, unit, min, max per field), seed_schedule(broker),
   require_otc_kind accepting the new kinds.
C23 (P11, products/private.py) `tradeable_private(rt, instrument_id) -> (bool, reason, trade_in)`; `instrument_extra` for PRIVATE;
   listed rows carry spec.aliases (SpaceX, xAI, Cursor ...) for search; privateticket.js registers T.ticketKinds.PRIVATE (docked).
C24 (P03, api_views.py) account rows carry available_by_ccy (major units) and available_base; position rows carry notional
   (futures: contracts x multiplier x mark), account_name and, in ALL_BOOKS, the book name.
C25 see C12 (fees of foreign venues, provided by P06, applied by P04, shown by P05's preview).
C26 (P08, GET /api/credit/loans?account_id=|book=) rows {loan_id, kind, currency, principal, lender, counterparty_id, index_name,
   index_value_pct, index_as_of, spread_pct, all_in_pct, accrued, payoff, opened_date, maturity_date, status} for the Cash page (P11).
C27 (P08, repo.py / lending.py) `repo.pledged_face(conn, account_id, instrument_id) -> int` (face pledged in open repos) and
   `lending.lent_qty(conn, account_id, instrument_id) -> int` (shares out on loan); P07's bond SELL refuses pledged face ('release the
   repo first') and P05's preview / validate refuse a SELL beyond position - lent ('recall N lent shares first').

## Order of work

Four packages run at a time. Suggested order: P01, P02, P05, P07 first (P01 / P02 / P05 land their hooks as step 1; P07 is the owner's loudest ask); then P04 (its hooks first), P06, P09, P08; then P12 (needs P09's delegate), P10 (needs P02's ticket dispatch), P03 and P11 (Risk needs P07's DV01 and P08's exposure). Hard dependencies are in depends_on; the hook dependencies are soft (consumers code against the contract and verify once the hook is in the tree).

## Packages

### P01 - Platform: API and schema hooks, settings, books and eligibility, news, server operations

- depends_on: none
- findings (27): TX-1 ORC-7 ORC-9 SHELL-03 SHELL-07 APIUI-15 SHELL-08 SHELL-09 SHELL-10 SHELL-11 SHELL-14 SHELL-22 SHELL-24 USX-37 SHELL-27 SR-07 SR-09 SR-11 BM-03 SR-04 PO-12 SR-01 BM-01 APIUI-04 BM-02 USX-26 USX-27
- owns:
  - `finsim/finsim/terminal/server.py`
  - `finsim/finsim/terminal/db.py`
  - `finsim/finsim/terminal/api.py`
  - `finsim/finsim/terminal/api_products.py`
  - `finsim/finsim/terminal/app.py`
  - `finsim/finsim/terminal/books.py`
  - `finsim/finsim/terminal/lifetimes.py`
  - `finsim/finsim/terminal/news.py`
  - `finsim/finsim/terminal/config.py`
  - `finsim/finsim/terminal/static/pages/settings.js`
  - `finsim/finsim/terminal/static/pages/news.js`
  - `finsim/finsim/terminal/static/components/lifetimes.js`
  - `finsim/tests/test_terminal_server.py`
  - `finsim/tests/test_terminal_app.py`
  - `finsim/tests/test_terminal_books.py`
  - `finsim/tests/test_terminal_lifetimes.py`
  - `finsim/tests/test_terminal_news.py`
  - `finsim/tests/test_terminal_api.py`
  - `finsim/tests/test_terminal_integration.py`

STEP 1 - HOOKS FIRST (within ~20 minutes; P06, P07, P08, P11 and P12 build on them in parallel): C1 product route registration in the
Router (product register_routes before api_views/api_products, first match wins); C2 OPTIONAL_MODULES entries otc_options,
counterparties, treasuries; C3 the eight planned optional UI files served as a JS stub while missing; C4 Database.ensure_schema_ext,
the idempotent otc_contracts kind widening at open, and Database.reader re-entrant per thread (= ORC-7); C5 instrument_extra merge and
no Yahoo subscription for BOND / PRIVATE / SWAP / OTC instruments in the instrument route. Then the findings.

FINDINGS
- TX-1: take the exclusive lock next to the database (<db_path>.lock, plus the home lock) BEFORE Database.open / backup / migration and
  before the port bind; a --db or FINSIM_TERMINAL_DB whose lock is held is refused (exit 3) naming the holder pid / port, writing nothing
  (re-run checks\tx-review\exp4_servers.py and exp4b_port_refusal_writes.py: no backups\ file, meta.integrity_ts unchanged). Same rule
  in app.py serve.
- ORC-7: Database.reader() re-entrant per thread (a nested reader() on a thread that holds one returns that connection; depth counted;
  released at the outermost exit). Prove: hold 3 of 4 pooled readers, a nested call on the 4th thread never blocks.
- ORC-9: app.py POWERSHELL_SEQUENCE built from finsim.terminal.VERSION (no '1.0.0' literal). The core.js T.VERSION half is P02's.
- SHELL-24 / USX-37: the HTTP server's handle_error drops ConnectionAbortedError / ConnectionResetError / BrokenPipeError to one DEBUG
  line; every other exception still logs its traceback.
- SHELL-03: GET /api/attention filtered to the selected book's lifetime (account_id prefix '<lifetime>:' or details.lifetime_id) plus
  global rows (providers, integrity, curve); ?all=1 = consolidated; resolving another book's item is refused 409 BOOK_MISMATCH.
  (attention.js sending ?book is P02's.)
- SHELL-07 / APIUI-15: /api/unsupported derived from the modules: each product module may expose unsupported() rows; the static list
  keeps only what was decided unsupported (private-equity deal flow, investment banking, MBS / structured, exotic OTC, commodity
  physicals, crypto, early assignment and forced liquidation not simulated); trader wording, no endpoints / WP ids / doc or glossary
  references; settings.js shows all four columns wrapped.
- SHELL-08: settings.js fetches /api/accounts and /api/books before painting Accounts / Prime brokers / Books and repaints on change
  (T.state.on, unsubscribed on unmount): correct on a direct load and after F5.
- SHELL-10: Settings > Calendar: holidays and early closes for this year and next (/api/calendar), session hours per group (/api/sessions
  rules), the CME_CAL sentence; the session_overrides JSON textarea becomes a small form (group, date, close time or CLOSED).
- SHELL-14: Data Status: 'not used yet' instead of n/a; labels for yahoo_news and fed; health.lifecycle.next_due from the scheduler
  (shown instead of 'due ?'); news per source (ok / down / never); empty fallback years hidden; db size = file + WAL; pollers JSON
  behind a debug toggle; everything dimmed with 'as of hh:mm' while the server is unreachable; the new refreshers of C14 listed.
- SHELL-09: news.py reads the published index https://shafferusa.github.io/intelligence-terminal/reports/index.json (verified 15:40 ET:
  200, 201 entries, newest 2026-10-21 am) on its hourly slot, the local file as fallback; when the newest edition is older than
  yesterday the column lists the latest editions with dates and says 'newest edition YYYY-MM-DD'; links open the published page.
- SHELL-11: the 'Compliance notices' tile counts only info / warn / breach (never ok or n/a) and links to Risk's compliance panel.
- SHELL-27: news tiles link to what they count: working orders and pending settlements -> Activity filtered by type for the book
  (#/activity?type=orders|settlements, P03 reads it), collateral calls and recalls -> Risk financing, 'open attention items' expands
  the strip (T.attention.expand, P02).
- SHELL-22: /api/state builds the BOOKS / QP / LIFETIME sentences from book_defaults, qp_threshold and pb_otc_threshold (C10); Settings >
  Lifetimes prints the LIFETIME sentence once.
- SR-07: Settings 'Save books' renames through lifetimes.h_update_settings (book.update_settings) and new lifetime names default to
  book_defaults[code].name (re-run checks\specrev\p2_settings_names.py).
- SR-09: INFO attention ELIGIBILITY_BELOW_THRESHOLD when a lifetime has relationships and NAV is below the threshold (a light command
  books.eligibility_notices(services) run by the Supervisor's 30 s loop; auto-resolved when NAV recovers).
- BM-03 / SR-04 / PO-12: books.eligibility: profile in (FUND, INSTITUTION) is eligible by profile (design 13.10); a FUND lifetime is
  judged by NAV only when its starting capital is below the threshold; a lifetime whose PRIVATE account exists stays unlocked (QP gate
  'the account then stays'); the FUND profile is qualified from day one. Re-run checks\specrev\p1_fund_eligibility.py: after the 1.00 fee
  F onboards JPM.
- SR-01: books.require_private honours 'stays unlocked'; the QP gate applies to BUY / ROUND only (P11 changes private.validate to call
  it only for buys - cross-item in P11).
- BM-01 / APIUI-04 / BM-02: Treasury reserve per book: setting treasury_reserve_by_book {book_code: cents} (the old global
  treasury_reserve_c migrated into every book once, SETTINGS_CHANGED recorded); PATCH validates 0 <= reserve with the plain message
  'reserve must be 0 or more' and the field label (never treasury_reserve_c); P04 reads it per lifetime in accounting (cross-item there),
  P03's treasury.js sends {treasury_reserve, book}.
- SR-11: Settings > 'Margins & limits (assumed - verify current exchange values)': editable futures_margin_overrides {root: {im, mm}}
  and position_limit_overrides {key: limit} (SETTING_KEYS, validation, SETTINGS_CHANGED before/after); P04's futures.py applies the
  IM / MM override to new requirements and P05's compliance.py the limits (cross-items there). Re-run checks\specrev\p11_tables_editable.py.
- USX-26: futures tab rows carry group_label (CME equity index, CME rates, CME FX, NYMEX energy, COMEX metals, CBOT grains) and per root
  the most active listed month (highest observed volume / open interest, else the front month outside its expiry week; GCZ26 not GCV26)
  with the other listed months in `months` for a picker (P02 renders both).
- USX-27: list rows carry last_print_age_d; a FULL row without a print for more than 10 sessions gets stale_note 'no print since <date>
  - possibly delisted' and is not offered b / s (P02 hides them); it stays searchable.

CROSS-ITEMS P01 ALSO DOES (requested by other packages; no finding id): C6 private search filter (P11's tradeable_private); C7 409
BOOK_MISMATCH on order preview / submit (SHELL-02, P02); C8 bars route via the P05 worker (USX-06, P10); C9 schedule seed + field specs
(PO-19, P12); C10 /api/state prime_brokers + conventions (SHELL-16 / PO-15, P02); lifetimes.js renders the agreements of the lifetime
view ('ISDA Master / CSA / PBA with <dealer>, signed <date>', PO-16, P12); settings.js: the PB section shows the eligibility sentence
(PO-14, P12) and labels the PB limit 'USD, 0 = no cap' (PO-17, P11), General calls T.help.renderKeys(el) (SHELL-21, P02), the 'legacy
FinSim simulation (8766)' link only when a probe of 127.0.0.1:8766 answers, else the command that starts it (SHELL-20, P02), headings use
profile_label and book names, 'LIFETIMES OF <book name>' (SHELL-15, P02); news.js footer prints 'never fetched' or the last error per
source instead of '--:--' and maps provider names through T.providerLabel (SHELL-18 / SHELL-15, P02).

UI acceptance:
- STEP 1 hooks in place: a stub product module with register_routes answers its route; a planned optional file missing on disk is served as a JS stub (no 404 in the console); ensure_schema_ext adds a table twice without error.
- Settings: General, Accounts, Prime brokers, Books, Lifetimes, Calendar, Margins & limits, Data and Unsupported each render on a direct load and after F5 without switching tabs, with no raw keys and nothing Build 2 shipped listed as not built.
- Fund Book: BUY 10 SPY (fee 1.00) then '+ Open prime broker account' -> JPM opens; Private Markets stays open; no 'unlock' hint.
- Treasury reserve 2,000,000 saved in the Institutional Book changes only that book's Treasury available; '-5' is refused with 'reserve must be 0 or more'.
- Personal Book attention strip and News tiles show only Personal items plus global provider rows; tile links land on the filtered Activity / Risk views.
- Daily News newspaper column lists the published editions with dates (or 'newest edition <date>').
- Settings > Calendar shows 2026-11-26 closed and 11-27 / 12-24 early closes; Data Status shows next lifecycle job and db+wal size.
- US Exchange Futures tab shows readable group headers, one active month per root with a month picker; a 67-day-stale name has no b / s and says why.
- No ConnectionAbortedError traceback on stderr during a 10-minute walk; serve --home H2 --db H1\terminal.db exits 3 before writing anything.

### P02 - UI shell: plug-in hooks, navigation, header, sidebar, freshness chips, Markets page

- depends_on: none
- findings (25): SHELL-02 SHELL-12 SHELL-13 SHELL-15 SHELL-16 PO-15 SHELL-17 PO-22 BM-32 SHELL-18 SHELL-19 SHELL-20 SHELL-21 SHELL-26 PO-13 BM-15 USX-11 BM-16 BM-30 OA-04 OA-10 APIUI-10 USX-05 USX-16 USX-28
- owns:
  - `finsim/finsim/terminal/static/core.js`
  - `finsim/finsim/terminal/static/index.html`
  - `finsim/finsim/terminal/static/terminal.css`
  - `finsim/finsim/terminal/static/components/header.js`
  - `finsim/finsim/terminal/static/components/sidebar.js`
  - `finsim/finsim/terminal/static/components/bookswitcher.js`
  - `finsim/finsim/terminal/static/components/attention.js`
  - `finsim/finsim/terminal/static/components/freshness.js`
  - `finsim/finsim/terminal/static/pages/markets.js`
  - `finsim/tests/terminal_fixture_server.py`
  - `finsim/tests/fixtures/terminal/api/`
  - `finsim/tests/test_terminal_static.py`
  - `finsim/tests/test_terminal_ui_ops.py`

STEP 1 - UI HOOKS FIRST (within ~20 minutes; P06, P07, P08, P09, P11 and P12 register into them): C11 - index.html data-optional tags
for the eight planned files of C3 plus the same entries in tests/terminal_fixture_server.py OPTIONAL_STATIC (test_terminal_static
checks both); core.js T.marketPlugins / T.ticketKinds registries and the T.ticket.open dispatch by kind; markets.js delegation to the
account kind's plugin (PB: only the tabs the plugin lists); the account tab bar on every account page (OA-10) so plugin pages get it too;
T.providerLabel, T.help.renderKeys, T.attention.expand; T.api errors expose incident_id from error.details. Then the findings.

FINDINGS
- SHELL-02: T.book.set and the lifetime-reset success clear localStorage finsim.active_account; g-keys only use an account of the
  current book; the router switches the book (or asks) for an #/a/<id> of another book and never mounts the ticket on a CLOSED account
  (the server's 409 BOOK_MISMATCH is P01's C7).
- SHELL-12: '/' targets only a visible search box (offsetParent !== null), else the toast.
- SHELL-13 + USX-28 (dock part): 't' outside account pages toasts 'open an account page first'; when the dock opens on an account page
  with no instrument, mount an empty ticket (T.ticket.open({account_id})) with the hint 'type a symbol or press b / s on a row'.
- SHELL-15: provider labels incl. yahoo_news and fed; header card with no account reads 'pick an account (Alt+1..9)'; version / pid move
  to the server chip tooltip (the settings / news / lifetimes / watchlists label parts are cross-items in P01 / P10).
- SHELL-16 / PO-15: the onboarding modal lists dealers from /api/state.prime_brokers (C10) with each seed schedule (commission,
  financing spread, GC / HTB, OTC half-width, monthly minimum; 'modelled assumptions'), points to Settings > Prime brokers, marks dealers
  already open, toasts 'already open' when created is false, no 'Build 2' text.
- SHELL-17 / PO-22 / BM-32: .nav-why display:block with the .nav-sub indent; one short reason per dimmed row, the full sentence on
  hover; no duplicate eligibility text under the switcher; short book labels or compact NAV (1.00bn); toasts capped; the Treasury row
  shows NAV / available; the 'BOOK' heading renamed.
- SHELL-18: .grid3 = repeat(3, minmax(0, 1fr)), one column below 1100 px (the news.js footer is P01's cross-item).
- SHELL-19: an unknown route toasts 'no page <hash>' and replaces the hash with #/news; an unknown tab falls back to the first tab and
  corrects the hash.
- SHELL-20: remove the sidebar 'legacy FinSim (8766)' link (the Settings part is P01's).
- SHELL-21: T.help.renderKeys renders the full key map in a compact two-column table (Alt+1..9, Alt+B, t, grid keys, Ctrl+Enter); the
  ? overlay lists the ticket keys in words; 'b / s' is one label.
- SHELL-26: renderBanner tests server_ok === false before offline (both messages when both apply); the integrity dot greys to
  'unknown - server unreachable'.
- PO-13: '+ Open prime broker account' is driven by e.can_onboard only; with relationships but can_onboard false the row is dimmed with
  'new relationships need NAV >= threshold'.
- BM-15 / USX-11: the header P&L TODAY cell shows the total P&L today (fees included) with realised / unrealised / fees on hover.
- BM-16: the active account is cleared when it is CLOSED or not in the current lifetime; the book card shows instead.
- BM-30: the sidebar 'avail' shows the base-currency total or 'USD x . EUR y' from available_by_ccy (C24, P03).
- OA-04: freshness.js formats an as_of only when numeric (ISO strings printed as they are); T.fmt.date / dt return '' for non-finite
  input, so a bond page head paints.
- APIUI-10: page pollers show HTTP errors on the page (error line / stale banner with retry) instead of 'loading ...' forever; the
  incident id is shown (the ticket typeahead status is P10's).
- USX-05 (the 60 / 30-row cap is gone since 1.0.3): Options underlyings rows open the instrument page with the chain on a single click /
  Enter; b / s replaced by a 'chain' action on that tab.
- USX-16: 'add symbol' resolves and adds the symbol to the account's default watchlist (POST /api/watchlists) so it appears at the top of
  the tab; the box is labelled accordingly.
- USX-28: Markets grids hide the bid / ask columns when no row has them.

CROSS-ITEMS P02 ALSO DOES (no finding id): core.js T.VERSION taken from /api/health (ORC-9, P01); T.pxHtml(rec, {unit}) shows 'GBp
2,700.00' with the GBP value on hover when quote_unit / price_scale != 1 (OA-15, P06); a short archived chip 'FROZEN hh:mm' (BM-31, P03);
Settings highlighted in the sidebar on #/h routes and T.book.set from a History route lands on #/settings/lifetimes (SHELL-23, P03);
markets.js renders group_label and the month picker (USX-26, P01), shows stale_note rows without b / s (USX-27, P01) and gives option rows
BTO / STO and other kinds their own sides on b / s (ORC-2, P10); attention.js sends ?book and the strip can be expanded (SHELL-03 /
SHELL-27, P01); the router accepts an optional ?query on #/activity and #/risk and hands it to the page (#/activity?type=orders,
#/risk?section=financing; SHELL-27, P01 / P03 / P11).

UI acceptance:
- 0 console errors on every page; no 404 for the eight planned optional files (served as stubs until built); header session chips stay complete on every page.
- Every account page has the tab bar Markets / Positions / Orders / Cash & financing / Transactions; Cash & financing is one click from the FX and Credit pages.
- A plugin registered from the console (T.marketPlugins.DEBT = {render: ...}) renders the Debt Markets page; T.ticketKinds.BOND gets T.ticket.open({kind: 'BOND', ...}).
- 'g m' after switching to the Fund Book opens F1's US Exchange; after a lifetime reset nothing opens a CLOSED account.
- Options underlyings: one click on SPY opens the chain; 'add symbol KO' puts KO on the Equities tab.
- Onboarding modal shows every dealer's schedule; re-opening GS toasts 'already open'.
- With the server stopped: red server chip, grey integrity dot, 'Server unreachable since ...' banner.
- #/nope toasts and shows Daily News; a bond page head paints (no RangeError) with a curve loaded.
- Header P&L TODAY equals Portfolio TODAY (fees included); sidebar avail shows EUR cash of the FX account.

### P03 - Portfolio, Accounting, Activity, History, Transactions and Treasury pages

- depends_on: none
- findings (27): BM-04 SHELL-05 APIUI-02 BM-05 BM-06 BM-07 SHELL-04 APIUI-03 BM-08 SHELL-06 SHELL-23 BM-13 BM-14 APIUI-14 BM-17 USX-24 OA-32 BM-18 BM-21 BM-23 BM-24 BM-25 BM-26 BM-29 BM-31 BM-33 USX-20
- owns:
  - `finsim/finsim/terminal/api_views.py`
  - `finsim/finsim/terminal/performance.py`
  - `finsim/finsim/terminal/static/pages/portfolio.js`
  - `finsim/finsim/terminal/static/pages/activity.js`
  - `finsim/finsim/terminal/static/pages/history.js`
  - `finsim/finsim/terminal/static/pages/accounting.js`
  - `finsim/finsim/terminal/static/pages/transactions.js`
  - `finsim/finsim/terminal/static/pages/treasury.js`
  - `finsim/finsim/terminal/static/components/grid.js`
  - `finsim/finsim/terminal/static/components/performance.js`
  - `finsim/tests/test_terminal_display_units.py`
  - `finsim/tests/test_terminal_performance.py`

FINDINGS (several ids are the same defect seen by different sweepers - fix once, list all)
- BM-04 / SHELL-05 / APIUI-02: api_views.portfolio serves by_class as a list [{klass, label, count, mv, unrealised, day_pnl, weight}];
  portfolio.js accepts the list (and the old dict); the positions grid and the by-account panel render in every book and in All books.
  Check against a real build_runtime answer, not only the fixture.
- BM-05: footer {gross_long, gross_short}; by_account rows with pnl_today, weight (share of the scope's NAV), account_name and, in
  ALL_BOOKS, the book name; the account filter shows names.
- BM-06: Portfolio '% NAV' = share of the requested scope's NAV (book or ALL_BOOKS). The account Positions column is labelled
  '% acct NAV' (P10 cross-item) and Risk's concentration warning is judged at book level (P11 cross-item).
- BM-07 / SHELL-04 / APIUI-03: activity() resolves the lifetime from book= unless an account is given; ALL_BOOKS supported; system job
  rows labelled and hidden behind a 'system' filter; the page reads #/activity?type=orders|settlements|transfers (SHELL-27 links).
- BM-08 / SHELL-06: history_view returns the pnl and balance_sheet payloads flat (dict(meta, **view)) like trial balance; History P&L
  defaults to ITD.
- SHELL-23: History pages are read-only (accounting.render(readOnly) hides Run now); a 'back to lifetimes' link (the sidebar highlight
  and book-switch routing are P02's).
- BM-13: Accounting gets Transactions (scope ALL / ALL_BOOKS / account, the grid plus a CSV link to /api/export/transactions.csv) and
  Cash & financing (cash by account and currency, settlement ladder, loans, margin, sweeps) tabs; a positions CSV link on Portfolio.
- BM-14 / APIUI-14: the P&L explain shows fees and financing with their P&L sign so the rows add up to 'explained' (colour by effect).
- BM-17 / USX-24 / OA-32: grid.update(rows, {empty}) replaces 'loading ...' with the real empty text once an answer arrives; your pages
  pass 'no transactions' / 'no activity' / ...; orders.js and positions.js (P10) pass theirs (cross-item there).
- BM-18: Transfer preselects To = the first trading account (or the active one), leaves From out of To, error 'pick two different
  accounts'.
- BM-21: Add funds / Withdraw: plain decimals with at most 2 dp (exponent and sub-cent input refused with a clear message; the server's
  strict parse is P04's cross-item), confirm 'Add USD 1,000,000.00 to Personal Book?', Enter submits, focus on the amount.
- BM-23: the sweep log REASON reads 'for fill F-000003 (BUY 20 MSFT)' from linked_type / linked_id, linked to the account's orders.
- BM-24: transaction rows carry the other side (counterparty account or lender); the page filters itself out by the route's account; no
  doubled memo suffix; the capital history shows the memo, not the request uuid.
- BM-25: Activity merges the event and transfer rows of one ref; system rows behind the filter; detail modals are key / value tables
  (journal lines with Dr / Cr) instead of raw JSON (Ledger too).
- BM-26: the Integrity tab lists one row per check run (code, name, ok, count, message) for the selected book's lifetime unless
  ALL_BOOKS (P04 adds the per-check rows to IntegrityReport - cross-item there).
- BM-29: one advances convention on the Treasury page and the balance sheet ('advanced to accounts' on the Treasury scope, 'funded by
  Treasury' on account scopes) with the same formula text.
- BM-31: history by_account rows carry account names (the short chip is P02's).
- BM-33: the ITD baseline tile reads 'inception (capital flows)' from the API's baseline object.
- USX-20: on Transactions a futures fill shows qty / price / gross only on the FILL row; the FUT_MARGIN row has its own memo
  ('initial margin +17,500.00').

CROSS-ITEMS P03 ALSO DOES (no finding id): C24 - account rows available_by_ccy / available_base (BM-30, P02); position rows notional,
account_name and book name (USX-30, P10); treasury.js PATCHes {treasury_reserve, book} for the selected book and floors the shown
available at 0, coloured by sign (BM-01, P01).

UI acceptance:
- Portfolio in Personal, Fund, Institutional and All books: grouped positions with subtotals, footer gross long / short, by-account table with P&L today and % of total; no console error.
- Activity in each book shows only that book (after a reset only the new lifetime); ?type=orders filters.
- History of a reset lifetime: P&L and balance sheet show figures; no Run-now buttons.
- Accounting has Transactions (with CSV) and Cash & financing tabs; the P&L explain rows add up to 'explained'; Integrity lists the checks it ran.
- Empty grids say 'no ...' instead of 'loading ...'.
- Treasury: Transfer defaults to Treasury -> US Exchange; Add funds asks for confirmation; sweep reasons link to their fills.

### P04 - Money and lifecycle: accounting, funding, Treasury, lifecycle jobs, options and futures money paths

- depends_on: none
- findings (18): MONEY-3 MONEY-4 MONEY-5 MONEY-6 LC-1 LC-2 LC-4 LC-5 SR-02 SR-13 TX-4 TX-5 ORC-3 USX-10 USX-17 BM-20 APIUI-07 USX-09
- owns:
  - `finsim/finsim/terminal/accounting.py`
  - `finsim/finsim/terminal/accounting_checks.py`
  - `finsim/finsim/terminal/treasury.py`
  - `finsim/finsim/terminal/units.py`
  - `finsim/finsim/terminal/ledger.py`
  - `finsim/finsim/terminal/execution.py`
  - `finsim/finsim/terminal/lifecycle.py`
  - `finsim/finsim/terminal/lifecycle_jobs.py`
  - `finsim/finsim/terminal/lifecycle_eod.py`
  - `finsim/finsim/terminal/products/options.py`
  - `finsim/finsim/terminal/products/futures.py`
  - `finsim/finsim/terminal/products/dividends.py`
  - `finsim/tests/test_terminal_accounting.py`
  - `finsim/tests/test_terminal_treasury.py`
  - `finsim/tests/test_terminal_ledger.py`
  - `finsim/tests/test_terminal_lifecycle.py`
  - `finsim/tests/test_terminal_persistence.py`
  - `finsim/tests/test_terminal_reconcile.py`
  - `finsim/tests/test_terminal_options.py`
  - `finsim/tests/test_terminal_futures.py`
  - `finsim/tests/test_terminal_sweeps.py`
  - `finsim/tests/test_terminal_equities.py`
  - `finsim/tests/test_terminal_dividends.py`

STEP 1 - HOOKS FIRST (within ~30 minutes; P06, P08, P09 and P12 build on them): C12 execution.fund (cascade, fx_convert via
services.fx.convert_for, commitments mode, venue fee line in fee_for) and C13 lifecycle_eod.add_product_job. Then the findings.
Every money change is hand-worked; re-run the reviewers' repros in checks\money (s1-s14) and checks\lc and compare with the expected
values written in review_findings_1021.json.

FINDINGS
- MONEY-3: while the day's VM is pending, a flat (account, future) carries the latest futures_settle unposted_c as unrealised (a
  zero-quantity row or the account's futures OTE) until FUTURES_VM posts it; checks\money\s6_futures.py: NAV 99,066.00 right after the
  closing SELL.
- MONEY-4: execution.fund cascades exactly like the preview (C12a); buying_power_split agrees; s9_pb_funding.py: the submit fills with
  one SWEEP_IN 100,000.00 + PB 150,001.50; a PB-only shortfall uses the Treasury first as disclosed.
- MONEY-5: one NAV definition everywhere: unpriced positions at cost (assets carry the cost row, short proceeds the liability) so
  nav_c == assets - liabilities, note 'N position(s) at cost: no price'; s12_unpriced.py: 999,997,000.00 = 1,000,100,000.00 -
  103,000.00.
- MONEY-6: treasury.transfer checks settled cash in that currency net of payables and working-order reservations (an immediate
  movement) and refuses 409 INSUFFICIENT_FUNDS naming the settled amount; the L18 integrity check is never the refusal path
  (s7_transfer_receivable.py at 18:00).
- LC-1: OPTION_EXPIRY exercise / assignment / lapse fills use the expiry date as trade date and business date, settlement =
  cal.settlement_date(expiry, settle_days) (as FUTURES_FINAL does); checks\lc\opt_late_expiry_sched.py run on Monday books trade
  11-20, payable due 11-23, and the Monday ex-dividend is kept.
- LC-2: SWEEP_OUT keys exist only for dates on which the account held that currency at 17:30 (or genuine scheduler downtime); a
  mid-session transfer is never swept back (checks\lc\sweep_backdated.py).
- LC-4: PB_MONTHLY_MIN's core path excludes pay:pbmin:% entries (or calls prime.fees_charged_c): 380.00 then 500.00
  (checks\lc\pbmin.py).
- LC-5: in a catch-up, today's COLLATERAL_MARK waits until the earlier nights accrued (or each night reads the collateral of its night):
  4.77 / 4.78 / 4.77 (checks\lc\borrow_order.py).
- SR-02: L21 ignores CLOSED lifetimes (or the reset freezes their pending settlements as FROZEN); checks\specrev\p6_reset_pending_settlement.py
  raises no INTEGRITY_HOLD.
- SR-13 (decision s7.6): short-option margin is a RESERVED requirement, no cash movement: recompute_option_margin upserts margin_state
  only; reserved / available / buying power subtract options_required; the cash view shows required and reserved; the balance sheet
  loses 'Option margin deposit'; L16 compares margin_state with the recomputed requirement; a requirement beyond own available +
  Treasury raises FUNDING_SHORTFALL (no forced liquidation). One-time idempotent migration at start: every 1310 balance released to 1010
  ('migrate:omargin:{account}', memo 'decision 6: option margin is a reservation'), integrity ok; s19.7 numbers in test_terminal_options
  re-worked by hand. Do this last among your findings; prove it on a scratch DB holding a written put.
- TX-4: job_failures rows for (job, 'due'), (job, 'before') and batch keys are cleared on the next success; parked_until honoured for
  the pseudo-keys (checks\tx-review\exp2_jobfail.py).
- TX-5: RETRY / FAILED outcomes counted on the scheduler (errors, last_error; health errors_1h real), each 503 logged, one WARN
  LIFECYCLE_BLOCKED after N consecutive RETRYs naming the busy cause, resolved on success (exp6_retry_invisible.py).
- ORC-3: reserved_c reserves for a working futures order only the initial margin of its OPENING contracts (net of the position it
  closes), never the notional.
- USX-10: the account view (cash(), available_view, header / sidebar) counts receivables and payables due by the same settlement date,
  so same-day round trips no longer show AVAILABLE 0.00 with a phantom sweep; preview and execution use that one rule.
- USX-17: execution.fund and the preview use one availability rule (settled + receivables due by the order's settle date - payables
  - reserved); the preview's hedge-leg cash is P05's cross-item.
- BM-20: a foreign-currency deposit / withdrawal checks the FX rate first: 409 FX_RATE_UNAVAILABLE 'no EUR/USD rate yet; try again
  when FX rates load', no ERROR attention, the message not prefixed with its own code.
- APIUI-07: a SWAP position's qty is the signed notional in whole currency units (notional_c // 100) with its currency; CSV and
  Portfolio the same.
- USX-09: options._chain_quality grades chain and legs by the provider quote_time with the quotes.freshness delay + grace rule (STALE
  with its age), so the chain chip matches what the ticket enforces.

CROSS-ITEMS P04 ALSO DOES (no finding id): LOAN_MATURE job calling credit.loan_mature for loans with a maturity date (MONEY-1
follow-up: no job matures a term loan today); options._physical (exercise / assignment) cancels or reduces working SELL / STC orders on
the shares it delivers with an order event 'shares delivered by exercise' (TX-3, P05); accounting.treasury_available_c / treasury() read
the per-book reserve treasury_reserve_by_book (BM-01, P01); IntegrityReport carries one row per check run (BM-26, P03);
units.parse_money strict (<= 2 dp, no exponent, no separators) for deposits / withdrawals (BM-21, P03); futures.py applies
futures_margin_overrides to new requirements (SR-11, P01); optionally MARKS_RECORD before SWAP_VM in catch-up passes (LC-3, P09).

UI acceptance:
- Same-day round trip in US Exchange: Cash & financing, header and sidebar show the real available (no phantom sweep).
- A futures position closed flat shows NAV including the closing P&L before the evening VM.
- Writing a put shows its margin as reserved on Cash & financing (no Treasury sweep, no 'Option margin deposit' line); integrity ok.
- After 17:00 a transfer of unsettled sale proceeds is refused 409 with a plain message; an EUR deposit with no rate is refused 409 plainly.
- Swap positions show their notional in currency units; PB order beyond Treasury alone fills with Treasury sweep + PB financing.

### P05 - Order engine and quotes: preview, rules, matcher, quote cache / worker, compliance notices

- depends_on: none
- findings (19): EXEC-1 EXEC-2 EXEC-3 EXEC-4 EXEC-5 EXEC-6 EXEC-7 EXEC-8 EXEC-9 TX-2 TX-3 TX-7 ORC-5 SR-12 USX-18 USX-31 USX-08 USX-23 OA-26
- owns:
  - `finsim/finsim/terminal/orders_rules.py`
  - `finsim/finsim/terminal/orders.py`
  - `finsim/finsim/terminal/orders_preview.py`
  - `finsim/finsim/terminal/matcher.py`
  - `finsim/finsim/terminal/commands.py`
  - `finsim/finsim/terminal/compliance.py`
  - `finsim/finsim/terminal/tables.py`
  - `finsim/finsim/terminal/marks.py`
  - `finsim/finsim/terminal/quotes/cache.py`
  - `finsim/finsim/terminal/quotes/worker.py`
  - `finsim/finsim/terminal/quotes/__init__.py`
  - `finsim/tests/test_terminal_orders.py`
  - `finsim/tests/test_terminal_quotes.py`
  - `finsim/tests/test_terminal_marks.py`
  - `finsim/tests/test_terminal_core.py`
  - `finsim/tests/test_terminal_bus.py`
  - `finsim/tests/test_terminal_fill_live_bars.py`

STEP 1 - HOOKS FIRST (within ~30 minutes; P01, P06, P07 and P10 build on them): C14 worker register_refresher + request_bars; C15
preview / matcher hooks (FOREIGN sessions from the quote record, fx_conversion funding block and fx_convert pass-through to the fill,
refusals at preview for FX / BOND / PRIVATE / SWAP / OTC kinds pointing to their tickets); C16 tables.add_conventions. Then the findings.

FINDINGS (EXEC-1 and EXEC-2 landed in 1.0.3 in orders_rules.py / quotes/cache.py; the 14:05 gate-check (BUILD_LOG) left the
remainders below open)
- EXEC-1 remainder: orders_preview STOP_SIDE and the trailing reference use orders_rules.trigger_price (options: ask for buys, bid for
  sells), orders.py event texts say 'observed bid / ask' for options, tables STOP_RULE / LIMIT_RULE / TRAILING carry the option
  sentence; checks\exec-cache-checker k1 / k5: BTO STOP 21.00 with ask 20.40 accepted, STC STOP 66.00 with bid 65.01 refused STOP_SIDE,
  preview trail ref 65.01 / stop 63.01.
- EXEC-2 remainder: a Cboe chain failure is attached (record_error) to that underlying's option records so ERROR_AGE_S applies (k4:
  BTO MARKET at +210 s of a Cboe outage -> 409 QUOTE_STALE).
- EXEC-3: restored records are not executable until a live put (QuoteRecord.restored -> is_executable STALE); checks\exec_review\
  c3_restored_record.py -> 409 QUOTE_STALE after the restart until the first fresh fetch.
- EXEC-4: a decision with triggered=True is persisted (ORDER_TRIGGERED event) also for NONE / NOT_MARKETABLE (wants_command); c4 fills
  at 759 once the triggered sell limit is crossed.
- EXEC-5: a marketable LIMIT fills at px_exec whenever px_exec is at or better than the limit; the limit price (LIMIT_CROSSED) only for
  an order that was resting (not marketable at its previous executable look); c7: 339.50 / 339.40, never 345.00.
- EXEC-6: the participation cap is per (instrument, bar_ts) across all orders, accounts and books (SUM of fills against that bar);
  cap_note says so; c10: at most 12,000 against a 120,000 bar.
- EXEC-7: ChainCache.underlying_quote(underlying_id) from a fresh cached chain snapshot (method chain_underlying: bid / ask / sizes /
  last_trade_time / fetched_at), so stock fills use the NBBO ask / bid when a fresh chain is cached; the ticket sentence matches; c8.
- EXEC-8: the preview's trade date is that of the first session the order can trade in (session.trade_date when open, else
  next_session(group, now).trade_date); c11: Thanksgiving preview settles 11-30.
- EXEC-9: the 15:30 ET expiry-day cut-off for OPENING option trades is enforced where fills happen (evaluate / h_match return NONE, or
  the order is cancelled at 15:30 with the reason); c12: no fill at 15:45.
- TX-2: the bus evicts the instrument ids a rolled-back command touched (InstrumentMaster.invalidate - P06 adds it and stops caching rows
  read on an open-transaction connection); sqlite3.IntegrityError maps to DUPLICATE_POSTING only for UNIQUE / idem violations, FK /
  CHECK failures become 500 with their own code; checks\tx-review\exp1_phantom.py: the retry of the same request_id succeeds.
- TX-3: a fill refusal other than funding suspends the order (SUSPENDED + reason + one de-duplicated attention per order); unexpected
  exceptions back off per order with one attention; the bus's COMMAND_FAILED attention is de-duplicated by (kind, ref); exp3 / exp6.
  (P04 cancels the delivered SELL orders at exercise - cross-item there.)
- TX-7: the rejection audit id is per attempt (rej:<rid>:<sha8(code + message)>); a failed audit write is logged (exp7).
- ORC-5: orders_preview.validate reads through marks.shared_reader (no raw pooled reader around accounting / compliance / risk / hedges /
  prime); the nesting audit of BUILD_LOG 14:59 shows 0 nested pooled readers in preview.
- SR-12: decision s7.11 - sweep-ins are automatic at any size: sweep_confirm_threshold default 0 (off); the 25 % of NAV large-notional
  confirm stays (opening exposure only).
- USX-18: the CONFIRM_REQUIRED text names the rule that fired with its own threshold ('notional 390,175.00 > 25 % of NAV =
  249,997.48'); the sweep threshold appears only when that rule fired.
- USX-31: GTC orders expire at the session close of the 90th calendar day (DST-safe).
- USX-08: an instrument page / ticket instrument is fetched ahead of list rows (first fetch within one worker tick); NO_QUOTE wording
  'no quote yet - fetching' with code NO_QUOTE (not QUOTE_STALE) in the preview (the instrument page text is P10's).
- USX-23: the wash-sale notice excludes the lots being closed; only replacement purchases count.
- OA-26: FX / foreign previews show only relevant notices (no 13F / 13D / Reg SHO / 201 / wash sale for FX), no participation line for
  FX, NO_QUOTE when no quote ever existed (the qty unit label is P10's).

CROSS-ITEMS P05 ALSO DOES (no finding id): the preview funding block includes the hedge legs' cash (USX-17, P04); TRS_OVERLAY refused at
preview as an order leg, 'booked on the OTC desk' (PO-10, P10); compliance.py applies position_limit_overrides (SR-11, P01); the preview
shows the venue fee line from execution.fee_for (C25, P06 / P04); preview and validate refuse a SELL beyond the position minus the shares
lent out (services.lending.lent_qty, C27, P08) with 'recall N lent shares first'.

UI acceptance:
- Option STC LIMIT above the bid rests (never fills at the stale last); option stops trigger on the bid / ask.
- Yahoo failing (FakeProvider 503): the ticket shows QUOTE_STALE within 75 s; after a restart a MARKET BUY is refused until a fresh print.
- A stop-limit hit on a dip shows 'triggered' on Orders and fills when its limit is crossed later.
- Orders entered after the close preview the true settle date; a Fund / Institutional order above 2,000,000 of sweep no longer asks for confirmation unless its notional is above 25 % of NAV, and the modal names that rule.
- Instrument page quote appears within one worker tick; a never-quoted symbol says 'no quote yet - fetching'.
- Ticket for a bond / FX pair / private name / swap is refused at preview with the pointer to its own ticket (no submit-time surprise).

### P06 - Foreign Markets & FX marketplace: FX desk (spot, forwards, crosses), tradable foreign listings, currency positions

- depends_on: none
- findings (7): OA-08 OA-09 OA-11 OA-12 OA-14 OA-15 OA-24
- owns:
  - `finsim/finsim/terminal/fx.py`
  - `finsim/finsim/terminal/sessions.py`
  - `finsim/finsim/terminal/instruments.py`
  - `finsim/finsim/terminal/static/pages/markets_fx.js`
  - `finsim/finsim/terminal/static/components/fxdesk.js`
  - `finsim/tests/test_terminal_fx.py`
  - `finsim/tests/test_terminal_sessions.py`
  - `finsim/tests/test_terminal_instruments_foreign.py`

OWNER: "FX Forwards needs to be in FX and why is there [a] NOT IN THIS BUILD - foreign listings and foreign bonds ... FX spot deals from
the Cash & financing page ... cmon, get this built". Build the Foreign Markets & FX account into a real marketplace.

BUILD
1. pages/markets_fx.js registers T.marketPlugins.FOREIGN_FX (C11) with tabs: FX spot (the 25 USD pairs plus the major crosses: EURJPY,
   EURGBP, EURCHF, GBPJPY, AUDJPY, EURAUD, EURCAD, CHFJPY ...; Yahoo symbols like EURJPY=X answer, verified 15:41 ET), FX forwards (per
   pair: tenor grid ON / 1W / 1M / 2M / 3M / 6M / 9M / 1Y + broken date: forward points, outright, the rates used with series and as-of),
   Foreign listings (the 38 + resolvable foreign symbols; b / s open the generic order ticket), Foreign bonds (body = P07's
   T.components.bondlist with scope FOREIGN, C18 / C19), Currency positions (net per currency: settled, pending spot, forwards by value
   date, USD equivalent). No 'NOT IN THIS BUILD' box. Grids fit 1100 px (OA-27 part).
2. components/fxdesk.js registers T.ticketKinds.FX: one FX deal ticket (spot or forward; buy and sell currency from every quoted
   currency; amount in either currency with its unit shown; tenor or value date) with a preview (mid, deal rate +/- the disclosed spread,
   forward points and their inputs, value date, both legs' settled before / after, any settlement shortfall and how it is funded) and
   Confirm. The Cash page's FX convert becomes a link to this desk (P11 cross-item).
3. fx.py: real forwards (fx.forward command: outright = CIP from spot and the two currencies' rates for the tenor, +/- disclosed
   forward spread; fx_deals kind FORWARD with its value date; settlement through the existing SETTLE_DUE path; mark-to-market computed
   (CIP) with inputs, realised at value date); crosses booked directly (the 'convert through USD in two deals' refusal goes; each leg's
   ledger rate from its USD rate, 3900 nets to zero exactly); the contracts of C17 (conversion_plan, convert_for, forward_rate,
   venue_fee_c); register_routes (C1): GET /api/fx/pairs, GET /api/fx/forwards?pair=, GET /api/fx/positions?account_id=; POST
   /api/fx/preview takes kind / tenor / value_date. Rates: USD from the Treasury curve (bills) or SOFR, the other currency from its
   tables.POLICY_INDEX series through RateStore (FRED is in maintenance today: P07 adds NY Fed SOFR and ECB rate fallbacks); monthly
   proxies (JPY, CHF, CAD, AUD) labelled LIMITED with their as-of; a missing rate -> no forward price, never a default.
4. Foreign listings tradable (OA-12): instruments.py: support FULL for listings with a Yahoo symbol and a known venue; session from the
   quote's provider_session (Yahoo currentTradingPeriod; P05 judges FOREIGN groups on it in preview and matcher, C15); settle T+2 (T+1
   venues by suffix as today); GBp / ZAc / ILA price_scale kept; per-venue fees venue_fee_c (UK stamp duty 0.50 % on purchases of UK
   shares, Hong Kong stamp duty 0.10 % both sides, France FTT 0.40 % on purchases of large French caps, Italy FTT 0.10 % on purchases;
   others 0; table labelled 'assumed - verify current rates'); local-currency settlement: the preview shows the currency need and the
   explicit USD -> currency conversion (conversion_plan), the fill books it (convert_for through execution.fund(fx_convert=True), C12)
   - never a silent conversion; sale proceeds stay in the currency.
Non-USD equities have never filled in this terminal (foreign rows were UNSUPPORTED): prove a GBP fill end to end (pence marks,
price_scale, GBP payable, conversion, settlement) early; anything execution.py / accounting.py must change beyond C12 goes to P04
as an exact request at once (note it in BUILD_LOG so P04 sees it).
5. instruments.py cross-items (no finding id): TX-2 half - InstrumentMaster.invalidate(ids) and get() never caches a row read on a
   connection inside an open transaction (P05); public upsert(tx, **fields) for P07's Treasury master; search() also matches
   spec.aliases (SR-14, P11).

FINDINGS
- OA-08: FX rows open the FX desk; the generic ticket refuses FX at preview with the pointer (P05) - no OK preview followed by a
  submit refusal.
- OA-09: FX forwards tab + desk + fx.forward; the hedge panel's FX_FORWARD candidate becomes available (P10 cross-item) and PB OTC FX
  forwards price through forward_rate (P12).
- OA-11: a sale of a currency the account (and Treasury) does not hold and cannot fund by the value date is refused 409
  SETTLEMENT_UNFUNDABLE with the shortfall and the fix ('buy EUR first' / 'transfer EUR from Treasury'); the desk preview shows legs and
  funding.
- OA-12: foreign listings tradable as above.
- OA-14: crosses on the FX tab; currency pickers driven by the FX master (every quoted currency: HKD, SGD, MXN, KRW ...).
- OA-15: rows and the desk show 'GBp 2,700.00' with the GBP value; P02 adds the unit to T.pxHtml and P10 to the instrument page
  (cross-items).
- OA-24: Currency positions tab and GET /api/fx/positions; P10's Positions page shows that block for the FX account (cross-item).

Hand-work at least one forward in the result (e.g. EURUSD 3M = S x (1 + r_USD x d/360) / (1 + r_EUR x d/360) with the printed inputs)
and one cross deal's ledger lines.

UI acceptance:
- Foreign Markets & FX shows tabs FX spot / FX forwards / Foreign listings / Foreign bonds / Currency positions and no 'NOT IN THIS BUILD' text.
- b on EURUSD opens the FX desk; a spot deal previews deal rate, spread cost and T+2 and books; Currency positions shows it.
- EURUSD 3M forward previews outright, points and the rates with as-of, books, and appears with its value date.
- EURJPY quotes and deals directly; HKD, SGD and MXN are selectable.
- SHEL.L shows 'GBp 2,700.00 (GBP 27.00)'; BUY 100 SHEL.L in the London session previews the GBP need, the USD->GBP conversion and UK stamp duty and fills (fed FakeProvider); outside the session it rests until the London open.
- Selling EUR the account cannot fund is refused with the amount and the fix.

### P07 - Debt Instruments marketplace: real Treasuries, generic corporates, foreign government benchmarks, bond ticket, FRED outage

- depends_on: none
- findings (12): OA-02 OA-03 OA-05 OA-06 OA-07 OA-13 OA-23 OA-25 OA-33 BM-10 ORC-8 SR-08
- owns:
  - `finsim/finsim/terminal/products/bonds.py`
  - `finsim/finsim/terminal/products/bonds_jobs.py`
  - `finsim/finsim/terminal/products/treasuries.py`
  - `finsim/finsim/terminal/quotes/providers.py`
  - `finsim/finsim/terminal/quotes/parsers.py`
  - `finsim/finsim/terminal/quotes/rates.py`
  - `finsim/finsim/terminal/quotes/http.py`
  - `finsim/finsim/terminal/static/pages/markets_debt.js`
  - `finsim/finsim/terminal/static/components/bondticket.js`
  - `finsim/tests/test_terminal_bonds.py`
  - `finsim/tests/fixtures/terminal/debt/`

OWNER: "no bonds in debt instruments available??? thought this was suppose to be a market place". Build the Debt Instruments account
into a real bond marketplace; the existing bond engine (bonds.trade, ACT/ACT accrual, coupons, maturity jobs) stays and is enabled.

BUILD
1. Treasury master (products/treasuries.py or bonds.py) from api.fiscaldata.treasury.gov (.gov UA; both verified 15:38 ET):
   v1/debt/mspd/mspd_table_3_market (every outstanding marketable security: CUSIP, rate, issue / maturity, outstanding) and
   v1/accounting/od/auctions_query (new issues, TIPS flag, terms): bills, notes, bonds, TIPS, FRNs; instrument id 'UST-<CUSIP>', kind
   BOND, account kinds DEBT + PB, named 'US Treasury 4.125 % 15-Aug-2035 (912828XX0)'; created / updated through InstrumentMaster.upsert
   (P06 adds it; until then your own light command inserting instruments rows per the db.py DDL, then master.refresh(iid) after
   commit); refreshed daily by a C14 worker refresher (never in a request); the old monthly model benchmarks UST-{tenor}-{yyyymm}
   and the universe's curated corporates with invented maturities are DELISTED unless an order / lot references them. Do NOT edit
   finsim/data/universe.json (the old simulation and FinSim2 read it).
2. Prices: TreasuryDirect FedInvest per CUSIP (verified reachable 15:38 ET: todaySecurityPriceDetail, 462 CUSIPs with BUY / SELL / END
   OF DAY; read what the columns mean and label it 'TreasuryDirect FedInvest <date>'); intraday the par-curve model (Treasury.gov daily
   par curve + Yahoo ^IRX / ^FVX / ^TNX / ^TYX shifts) labelled computed with its inputs; TIPS from the Treasury.gov daily real yield
   curve and the TreasuryDirect CPI index ratio where reachable, else LIMITED with the reason; bills by discount / BEY. Register a BOND
   mark through marks.register_kind (no marks.py edit).
3. Generic corporates, honest (OA-07): one generic per rating bucket x tenor (AAA / AA / A / BBB / BB / B / CCC x 2 / 5 / 10 / 30Y),
   coupon = model par yield at listing rounded to 1/8 %, priced par curve + the bucket's ICE BofA OAS from FRED (add BAMLC0A1CAAA ...
   BAMLH0A3HYC to FRED_SERIES, OA-06), named 'Generic A-rated USD corporate 5Y 4.375 % 15-Nov-2031 (generic: curve + ICE BofA A OAS)';
   no bucket print -> unavailable with the reason (never the old static spread labelled close).
4. Foreign government benchmarks (OA-13, C19): Germany from the ECB data API euro-area AAA curve (daily; verified), Japan from MOF
   jgbcme.csv (daily; verified), UK / France / Italy from FRED monthly OECD long-term yields (LIMITED, as-of shown; the Bank of England
   database answers 403 to scripts - never circumvent); generic 2 / 5 / 10 / 30Y benchmarks in the local currency, account kind
   FOREIGN_FX, T+2 in the currency (funding through P06's conversion, C17), labelled 'generic benchmark priced from <series> as of <date>'.
5. Trading: components/bondticket.js registers T.ticketKinds.BOND: face amount (lot 1,000), BUY / SELL, clean price and yield,
   accrued, settle T+1 (foreign T+2), fee USD 0.02 per 100 face min 5.00, executed at the observed / computed price +/- a disclosed
   half-spread (assumed table by type and tenor); POST /api/bonds/preview (C1) + the existing bonds.trade; build2_bonds on by default
   (or the flag removed); US bond session 08:00-17:00 ET, outside it refused with 'bond market closed - reopens 08:00 ET'.
6. pages/markets_debt.js registers T.marketPlugins.DEBT: tabs Treasuries (bills / notes / bonds / TIPS / FRN filters), Corporates
   (generic), Curve (par and real curves charted with the benchmark rows); search by CUSIP / issuer / maturity; columns coupon,
   maturity, price, yield, spread, rating, accrued, DV01, position; b / s on every tradable row; LIMITED rows say why; T.components.
   bondlist for P06's Foreign bonds tab. Fits 1100 px.
7. DV01 / reprice contracts (C18) for Risk (P11) and the DV01_FUTURES hedge candidate (P10 cross-item: ZT / ZF / ZN / ZB sized by
   dv01_c); instrument_extra for BOND (C5): headline price with source / as-of and labelled spec_view.
8. ORC-8 FRED outage: parse_fred_csv detects the HTML maintenance page ('Site Maintenance', not a CSV header) -> 'FRED unavailable
   (maintenance)' in provider health / Data Status (no WARNING spam); RateStore keeps the last good values with their true as-of;
   free fallbacks: SOFR from markets.newyorkfed.org (verified 200: 3.88 % 2026-10-20), ECB deposit rate from data-api.ecb.europa.eu
   (verified 200), labelled with their source; the FRED_API_KEY route (api.stlouisfed.org) is also in maintenance today - a secondary
   route only, the key never logged or echoed.

FINDINGS: OA-02 (bond rows show their price), OA-03 (every bond listed, grouped, no 60-row cap), OA-05 (bond ticket + execution +
DV01 hedge), OA-06 (OAS series fetched; no unsourced static spread), OA-07 (real securities / generic corporates labelled), OA-13
(foreign government benchmarks), OA-23 (bond columns, labelled spec, face ticket; ^TNX in Debt explains it is a yield index), OA-25
(curve chart + benchmarks, bond search instead of Yahoo resolve), OA-33 (b / s on bond rows), BM-10 (bond DV01 via C18), ORC-8,
SR-08 (umbrella: bonds here; FX forwards / foreign listings by P06 and repo / securities lending by P08 - say so in the result).
CROSS-ITEM (no finding id): bonds preview / trade refuse a SELL of face pledged in an open repo (services.repo.pledged_face, C27, P08).
Hand-work one Treasury purchase (clean x face / 100 + accrued + fee) and one night of accrual in the result.

UI acceptance:
- Debt Instruments lists hundreds of real Treasuries with CUSIPs; filters Bills / Notes / Bonds / TIPS; search by CUSIP finds the note.
- A 10Y note row shows coupon, maturity, price, yield and accrued with source and as-of; its page head paints.
- BUY 100,000 face of a note previews clean, accrued, fee, T+1 and the half-spread, books (offline with a fed curve), and the position accrues overnight.
- Corporates tab lists the generic rating buckets labelled generic with their OAS series and date (or 'unavailable' while FRED is down).
- Curve tab charts the par curve with benchmark rows; Foreign bonds (via the FX page) shows Bund / JGB benchmarks with as-of and gilts LIMITED.
- With FRED in maintenance, Data Status says 'FRED unavailable (maintenance)', SOFR comes from the NY Fed with its date; no 'NOT IN THIS BUILD' on the Debt page.

### P08 - Credit Instruments marketplace: counterparties, currency loans, repo and reverse repo, securities lending, stock borrows, private credit

- depends_on: none
- findings (7): OA-16 APIUI-05 OA-17 OA-18 OA-22 OA-29 OA-30
- owns:
  - `finsim/finsim/terminal/credit.py`
  - `finsim/finsim/terminal/lending.py`
  - `finsim/finsim/terminal/lending_accruals.py`
  - `finsim/finsim/terminal/repo.py`
  - `finsim/finsim/terminal/counterparties.py`
  - `finsim/finsim/terminal/data/counterparties.json`
  - `finsim/finsim/terminal/products/private_credit.py`
  - `finsim/finsim/terminal/static/components/privatecredit.js`
  - `finsim/finsim/terminal/static/pages/markets_credit.js`
  - `finsim/finsim/terminal/static/components/creditdesk.js`
  - `finsim/tests/test_terminal_loans.py`
  - `finsim/tests/test_terminal_lending.py`
  - `finsim/tests/test_terminal_shorts.py`
  - `finsim/tests/test_terminal_private_credit.py`

OWNER: "why isnt there counterparties for credit instruments?". Build Credit Instruments into a credit marketplace with real
counterparties (ASSUMED, editable terms, labelled as such - never presented as quotes from those institutions).

BUILD
1. counterparties.py + data/counterparties.json (bound as rt.counterparties, C2): banks for currency loans (e.g. JPMorgan Chase Bank,
   Bank of America, Citibank, Wells Fargo, HSBC, Barclays, BNP Paribas, Deutsche Bank, MUFG, UBS) with assumed spread over the policy
   index per currency, max amount and tenors; repo dealers from the NY Fed primary dealer list (verify the current list when building)
   with assumed repo spread vs SOFR and a haircut table; agent lenders for securities lending (BNY, State Street, JPMorgan, Northern
   Trust, Citi, eSecLending) with assumed fee split / rebate; the book's prime brokers; private-credit borrowers picked from the private
   company master (company_id linked). Terms editable in a Counterparties tab (stored in the settings table through your own light
   command, SETTINGS_CHANGED before / after); C20 exposure(services, lifetime_id) and GET /api/credit/exposure?book= for Risk (P11).
2. Currency loans with a lender choice (credit.loan_draw counterparty_id; the loan's lender = that institution; spread from its terms);
   GET /api/credit/loans (C26: accrued to date, index value + as-of, spread, all-in, payoff incl. the nights catch_up_accruals would
   post) for the Credit page and P11's Cash page; full payoff (full: true) reachable from the UI.
3. Repo / reverse repo (repo.py made real): repo = cash borrowed against Treasuries the book holds in its DEBT account (pledged face is
   encumbered: not sellable while pledged), reverse repo = cash lent against Treasury collateral; rate = SOFR (NY Fed fallback from P07)
   +/- the dealer's spread, ACT/360 daily accrual, overnight / term / open, roll and close; collateral valued from P07's bond marks with
   the assumed haircut (bills 1 %, notes / bonds 2 %, TIPS 3 %), marked daily both ways (margin call or excess return through the
   sweep). Extra columns / tables through Database.ensure_schema_ext (C4: repos.counterparty, terms_json, closed_ts; sec_loans);
   lifecycle jobs through add_product_job (C13: REPO_ACCRUE, REPO_MATURITY, REPO_COLLATERAL_MARK). Double-entry lines with a
   hand-worked example in the result; integrity checks stay green.
4. Securities lending out: lend long US equity positions (US Exchange / PB) to an agent lender: fee by tier (the FINRA heuristic /
   HTB table), 102 % cash collateral received (a liability) with the rebate paid, daily collateral mark, recall by the owner; a sale of
   lent shares asks for the recall first. Jobs SECLEND_ACCRUE / SECLEND_COLLATERAL_MARK via C13.
5. pages/markets_credit.js registers T.marketPlugins.CREDIT; components/creditdesk.js holds the tickets: tabs Currency loans, Repo &
   reverse repo, Securities lending, Stock borrows (every open borrow of the book in any account, with lender, fee, rebate, collateral,
   recall notice and Return), Private credit (privatecredit.js), Rates (SOFR, policy indices, OAS with as-of), Counterparties
   (directory, terms, exposure). No empty instrument grid on Credit.
6. Private credit: OA-22 (commit refused - or explicitly acknowledged - without SOFR / an OAS print, with the reason; oas_issue
   back-filled from the first print on or after the start date), OA-29 (plain titles instead of routes, one maturity convention,
   partial prepayment), OA-30 (the same QP / accredited rule as Private Markets for the Personal Book below QP, or at least a pre-trade
   notice), borrower picked from the company master.

CONTRACTS P08 PROVIDES: C20 exposure, C26 loans route, C27 repo.pledged_face / lending.lent_qty (P07 and P05 refuse sales of pledged
face / lent shares with them).

FINDINGS: OA-16 / APIUI-05 (the three tabs render loans / borrows / rates), OA-17 (counterparties everywhere), OA-18 (repo, reverse
repo, lending out, borrows on Credit), OA-22, OA-29, OA-30.

UI acceptance:
- Credit Instruments in P / F / I: tabs Currency loans, Repo & reverse repo, Securities lending, Stock borrows, Private credit, Rates, Counterparties with real content; no 'NOT IN THIS BUILD'.
- Draw EUR 1,000,000 from 'Deutsche Bank AG (assumed terms)': the loan lists index value + date, spread, all-in rate and accrued; full payoff repays principal + accrued.
- Repo 10,000,000 face of a held 2Y note with a primary dealer for 1 week: cash in, the face shows pledged, one night accrues (hand-worked); reverse repo books too.
- Lend 1,000 AAPL to an agent lender: collateral received, fee accruing, Recall returns the shares.
- Stock borrows of US Exchange shorts appear on Credit with Return.
- Private credit commit while FRED is down is refused with the reason; Counterparties tab shows exposure per counterparty.

### P09 - PB OTC swaps: MONEY-2 fix, shippable swaps, swap ticket and contracts table

- depends_on: none
- findings (12): MONEY-2 ORC-10 ORC-6 PO-02 PO-03 PO-04 PO-07 PO-08 PO-11 PO-21 PO-23 LC-3
- owns:
  - `finsim/finsim/terminal/products/swaps.py`
  - `finsim/finsim/terminal/products/swaps_trs.py`
  - `finsim/finsim/terminal/static/components/swapticket.js`
  - `finsim/tests/test_terminal_swaps.py`
  - `finsim/tests/test_terminal_swaps_trs.py`

FINDINGS
- MONEY-2 (blocks shipping swaps): swaps.h_terminate first posts the swaprcv / swappay nights from accrued_through + 1 to
  min(today, maturity) - 1 through accrue_leg (the swaps_trs._catch_up loop), answering 409 FIX_PENDING / RATE_UNAVAILABLE when a night
  cannot accrue; checks\money\s14_irs_terminate.py: pay 3,341.67 / receive 3,225.00 / net 116.67 settled at termination. Audit the
  other paths that close or pay a contract (maturity close, payment, STM) for the same skipped-night defect and fix them.
- ORC-10: make PB OTC deployable: with MONEY-2 fixed run test_terminal_swaps / swaps_trs on py-3.12 and py-3.14, s10_irs / s11_trs /
  s14 and the lifecycle 45-day TRS run (checks\lc\trs_sim.py); state in the result exactly which files the next release copy must
  include (products/swaps.py, products/swaps_trs.py, static/components/swapticket.js and their tests) or what still blocks them.
- ORC-6 / PO-02: swaps.preview, swaps_trs.preview, contracts_view, next_events, mark_trs close_on and mark_xccy fx_close_on read on the
  held connection or marks.shared_reader - never a nested pooled reader; re-run scratchpad\sweep\prime-otc\busy_probe.py with swaps
  held: 0 x 503 BUSY, page loads < 1 s (the 14:59 fix probably covers the marks; prove it).
- PO-03: a failed contracts load keeps contracts null and shows 'contracts could not be loaded - retrying' with backoff; a 30 s in-place
  refresh of contracts, marks and VM with the mark's as-of.
- PO-04: typed terms kept per kind (state.values[kind]); only the preview and contracts blocks re-render; a rejected input stays for
  correction.
- PO-07: IM is funded against available (settled + receivables due - payables - reserved) through execution.fund(...,
  commitments=True) (C12c) and swept at booking; the preview shows the same funding line.
- PO-08: XCCY preview and booking pin the pair (cache subscribe at ORDER) and refuse 409 FX_STALE when only the prior-day ECB fallback
  exists; the preview and confirm show the deal rate's source and as-of.
- PO-11: the contracts table sits in its own overflow-x wrapper under its header; status and Terminate leftmost; dealt / accrued / next
  in the drawer; fits 1440 px with the dock.
- PO-21: after book / terminate refresh account_detail and books (the header updates with the sidebar).
- PO-23: prices through T.fmt.px, side sentences from sideText(), the VM entry id linked to its journal entry.
- LC-3: SWAP_VM for TRS / XCCY: when D is a trading day and its close is neither recorded nor observed -> SKIPPED CLOSE_PENDING (WARN,
  retried), never a price dated before D or an intraday print of D (checks\lc\trs_vm_stale.py / trs_vm_intraday.py).

CONTRACTS P09 PROVIDES (no finding id): C21 - _delegate routes EQ_OPTION / FX_OPTION / EQ_FORWARD / FX_FORWARD to services.otc_options
(P12) with the swaps_trs hook names; SWAP_VM values them through the delegate; the contracts table renders any kind generically;
T.components.swapticket.mount(el, {account_id, notes}), prefill(kind, terms) (PO-10's TRS overlay, P10) and terminate(contract_id)
(PO-09, P10); swapticket registers T.ticketKinds.SWAP (the terminate dialog). SR-10 consumer: IM from prime.im_params(services,
account_id) (C22, P12; default = today's constants until it lands).

UI acceptance:
- Fund Book GS OTC tab: an IRS 10M 5y preview keeps every typed term; booking works; the contracts table is fully visible at 1440 px with the dock.
- With TRS + XCCY held, PB pages load without BUSY (busy_probe 0 x 503).
- Terminate after a missed night settles the caught-up coupons (pay / receive shown).
- XCCY preview shows the deal rate with its source and time; the header updates right after booking.
- Cash & financing of the PB shows the IM swept against available, not against cash owed to a pending settlement.

### P10 - US Exchange UI and hedges: instrument page, chain, chart, ticket, preview, hedge panel, strategies, positions, orders, watchlists

- depends_on: P02
- findings (26): USX-06 USX-12 ORC-4 USX-13 APIUI-08 APIUI-09 USX-14 APIUI-06 USX-21 APIUI-13 USX-22 USX-25 USX-30 USX-32 USX-33 USX-34 USX-35 USX-36 ORC-2 APIUI-11 PO-09 PO-10 SHELL-25 BM-34 USX-29 OA-21
- owns:
  - `finsim/finsim/terminal/static/pages/instrument.js`
  - `finsim/finsim/terminal/static/pages/positions.js`
  - `finsim/finsim/terminal/static/pages/orders.js`
  - `finsim/finsim/terminal/static/pages/watchlists.js`
  - `finsim/finsim/terminal/static/components/ticket.js`
  - `finsim/finsim/terminal/static/components/preview.js`
  - `finsim/finsim/terminal/static/components/hedge.js`
  - `finsim/finsim/terminal/static/components/chain.js`
  - `finsim/finsim/terminal/static/components/chart.js`
  - `finsim/finsim/terminal/hedges.py`
  - `finsim/finsim/terminal/strategies.py`
  - `finsim/finsim/terminal/strategies_catalogue.py`
  - `finsim/tests/test_terminal_hedges.py`
  - `finsim/tests/test_terminal_strategies.py`

FINDINGS (the 14:56 hotfix already fixed Close sides, the carried-over ticket and the futures-close preview: do not regress them)
- USX-06: the chart shows the session's full 1-minute bars from the bars route (P01 C8 over P05's request_bars), instrument.js retries a
  202 (T.api.getReady), the last-price label is formatted, title and 'no bars' never overlap.
- USX-12 / ORC-4: Hedge on a held position opens the hedge panel in 'hedge this position' mode: candidates sized to the held quantity
  (hedges.py mode=position), submitted as the hedge legs only (one leg = its own order, several = a strategies AON package), never a new
  primary order.
- USX-13 / APIUI-08: strategies materialise the option legs (ensure_instruments with the cached chain rows) BEFORE the incremental
  margin, or margin takes synthetic legs; every option template previews and submits on strikes never traded before
  (checks\apiui strat scripts).
- APIUI-09: hedge.js formats pct_from_spot (already a percent) with T.fmt.ratePct.
- USX-14 / APIUI-06: orders.js formats trail_pct as a percent ('trail 2.00 %').
- USX-21 / APIUI-13: the Result panel shows fill prices as plain executed prices (or with the fill's own quote record); never 'NO
  STATUS' (the private result's sweep line is P11's).
- USX-22: 'quote used' shows the price basis the fill used (bid / ask / last).
- USX-25: the chain opens on the nearest expiry 7-30 days out, scrolls the ATM row into view, one-sided quotes read 'no bid'.
- USX-30: the Positions column reads '% acct NAV'; a notional / exposure column (futures) from the rows' notional (C24, P03).
- USX-32: the exercise dialog speaks per right (a put sells 100 shares at K), shows intrinsic vs the mark and warns when out of the money.
- USX-33: the instrument header shows a curated, labelled spec (spec_view from C5 when present; never 'null' or empty keys).
- USX-34: the preview header echoes the typed limit when the preview carries none.
- USX-35: no GET /api/instruments/<OCC> for a chain leg (its meta comes from the chain).
- USX-36: ratio '1 contract per 100 shares'; zero-size candidates hidden; candidates always load when the panel opens.
- ORC-2: the instrument page opens the ticket without a side (BTO for options) on load; watchlist b / s use the kind's sides (the
  markets.js part is P02's).
- APIUI-11: Positions actions by kind: PRIVATE -> the private ticket SELL (T.ticketKinds.PRIVATE, P11); SWAP -> terminate (C21, P09);
  PRIVATE_LOAN -> the private-credit repay; BOND -> the bond ticket (P07); no Close / Hedge where no order path exists.
- PO-09: swap rows show side + notional in their currency (qty fixed by P04's APIUI-07) and 'Terminate ...' plus a link to the OTC tab
  instead of Close / Hedge.
- PO-10: the hedge panel's TRS overlay 'pick' opens the OTC tab's TRS form prefilled (swapticket.prefill, C21) - never an order leg (P05
  refuses it at preview).
- SHELL-25: a watchlist add checks inst.account_kinds against the chosen account (refuse 'TLT trades in US Exchange' or default to the
  first tradeable account); rows open in a tradeable account; labelled inputs; account names on the panels.
- BM-34 / USX-29: Positions and Orders keep their action columns visible with the dock open at 1440 / 1600 px (actions first or sticky,
  compact columns); the Cash grid header wrap is P11's.
- OA-21: PRIVATE / BOND instrument pages show the mark (C5 headline) with round date / source and no Yahoo source, chart, session chip
  or option-chain box.

CROSS-ITEMS P10 ALSO DOES (no finding id): ticket.js hands an instrument whose kind has a T.ticketKinds handler (BOND, FX, PRIVATE, SWAP,
OTC kinds) to that handler when it is picked in the typeahead; preview.js renders funding.fx_conversion and the ticket offers the
checkbox 'convert USD -> GBP as shown' sending fx_convert: true (OA-12, P06); NO_QUOTE wording and 'fetching quote ...' on the
instrument page (USX-08, P05); the qty unit label for foreign / FX kinds (OA-26, P05); the GBp unit on the instrument page (OA-15, P06);
the FX account's Positions page shows the currency-positions block from GET /api/fx/positions (OA-24, P06); hedges.py: FX_FORWARD
candidate priced by fx.forward_rate opening the FX desk prefilled (OA-09, P06) and DV01_FUTURES for bonds sized by bonds.dv01_c (OA-05,
P07); orders.js / positions.js pass the grid empty texts (BM-17, P03); ticket typeahead failures shown in the ticket status (APIUI-10,
P02); orders.js 'New order' on a PRIVATE account opens the private ticket (OA-20, P11).

UI acceptance:
- SPY page: the session's 1-minute chart, chain on a 7-30 DTE expiry centred on ATM, labelled spec without nulls.
- Hedge on 100 AAPL proposes position hedges and submits only the put.
- Strategies 'Bull call spread' on AAPL previews and submits on fresh strikes; legs show '-7.22 % from spot'.
- Orders shows 'trail 2.00 %'; the Result panel shows plain fill prices; 'quote used' shows bid / ask.
- Exercise on an OTM put explains it sells 100 at K and warns.
- Positions with the dock open at 1440 px show Close / Hedge / Exercise; swap rows show notional and Terminate.
- A watchlist refuses TLT in Debt Instruments; the private instrument page shows the last-round mark.
- Typing a CUSIP / EURUSD / PRV name in the generic ticket hands over to the bond / FX / private ticket.

### P11 - Private Markets fixes, Risk page completeness, Cash & financing page

**P11 ADDENDUM (owner, 2026-10-21 ~18:29, added by the orchestrator before P11 started): "calculate gross notional against nav too if that isnt
already happening, prob going to leverage w TRS".** risk.py already computes gross exposure (TRS at the underlying notional, futures at notional,
listed options at delta-USD, IRS / XCCY excluded) and `leverage` = gross / NAV, but the Risk page never shows the multiple and the 2x warning
limit is fixed. Add, on ONE mark snapshot like every other figure:
- Risk page header tiles: **Gross exposure / NAV** (x, the existing headline gross) and **Gross notional / NAV** (x: every position at notional:
  securities at market value, futures qty x multiplier x price, listed options at FULL underlying notional qty x multiplier x S, TRS at the
  underlying notional, IRS / XCCY / FX forwards at notional), each with its definition on hover; plus Long / NAV, Short / NAV, Net / NAV.
- The same ratios per account and per prime-broker account (the PB rows) and per book in the All-books scope; the TRS bucket's notional / NAV
  shown prominently (the owner plans to lever with TRS).
- JSON fields on the risk view (gross_leverage, gross_notional_leverage, long_pct_nav, short_pct_nav, net_pct_nav, per-account / per-PB
  equivalents) with hand-worked tests in tests/test_terminal_risk.py (money math).
- The ticket preview's 'gross exposure after the trade would be Nx NAV' warning stays a warning (configurable limits come with the margin build).


- depends_on: P07, P08
- findings (24): OA-20 OA-27 OA-28 SR-06 SR-14 APIUI-01 SR-03 BM-09 BM-27 BM-28 PO-18 ORC-1 BM-11 USX-19 APIUI-12 BM-12 PO-05 SR-05 BM-19 OA-19 BM-22 OA-31 PO-06 PO-17
- owns:
  - `finsim/finsim/terminal/products/private.py`
  - `finsim/finsim/terminal/data/private_companies.json`
  - `finsim/finsim/terminal/static/pages/private_markets.js`
  - `finsim/finsim/terminal/static/components/privateticket.js`
  - `finsim/finsim/terminal/risk.py`
  - `finsim/finsim/terminal/static/pages/risk.js`
  - `finsim/finsim/terminal/static/pages/cash.js`
  - `finsim/tests/test_terminal_private.py`
  - `finsim/tests/test_terminal_private_listed.py`
  - `finsim/tests/test_terminal_risk.py`
  - `finsim/tests/test_terminal_units_consumers.py`

PRIVATE MARKETS
- OA-20: privateticket.js docks (registers T.ticketKinds.PRIVATE, C11 / C23); instrument Buy / Sell, Positions Close and Orders 'New
  order' route there (P10 cross-items); no Hedge for private names.
- OA-27: the companies grid full width with the negotiated ticket in the dock; no clipping at 1100 px.
- OA-28: Preview with no price says 'enter the negotiated price per share'; the PRIVATE_TRADE sentence once; the trade result shows the
  sweep (funding.sweep, APIUI-13's private part).
- SR-06: POST /api/private/companies/{id}/status (register_routes, C1) and a 'Mark as listed' action calling private.set_status
  (ticker, listed_on, exchange, source URL).
- SR-14 / APIUI-01: tradeable_private(rt, iid) (C23) for P01's search filter (C6); listed / acquired names are not offered in the PRIVATE
  account (or greyed with the trade_in link); sync_master puts the common names on the listed rows' spec.aliases (SpaceX, xAI, Cursor;
  P06's search matches them); watchlist / round entries for no-longer-private companies refused (re-run checks\apiui p6_spacex_search,
  checks\specrev\p3_spacex_surfaces.py).
- SR-01 cross-item (P01 holds the id): private.validate calls books.require_private only for BUY / ROUND, never for SELL.
- instrument_extra for PRIVATE (C5): headline last-round mark with its date and source.

RISK
- SR-03: unpriced private positions enter the private bucket, concentration and PRIVATE_ILLIQUID at carrying value ('at cost (no
  mark)'); the UNPRICED text says NAV carries them at cost (P04's MONEY-5); private.preview calls risk.pretrade.
- BM-09: panels for exposure by class / account / currency (FX exposure), greeks by underlying, DV01 rows by instrument / tenor
  (bonds through C18), financing and liquidity, per-PB exposure (long MV, financed, headroom) + OTC contracts / IM / VM, a leverage
  tile and exposure by counterparty (C20).
- BM-27: the delta tile includes equity and futures delta (or is labelled 'options delta' beside an equity delta).
- BM-28: one plain sentence per scenario group under the table, wrapping, no developer text ('swaps.dv01_c', 'Build 2').
  (risk.js honours #/risk?section=financing from the News tiles, SHELL-27.)
- PO-18: the per-PB OTC table and PB financing block; account names; signed DV01; readable contract labels ('TRS AAPL').
- ORC-1: FUTURES_MAINTENANCE uses the signed held quantity: after = held +/- qty, maintenance x |after| (informational only).
- BM-06 / BM-10 cross-items: concentration judged at book level, not a single-account scope (P03); bond DV01 in dv01_rows and the
  rates scenarios, 'Build 2' notes removed (P07).

CASH & FINANCING PAGE
- BM-11 / USX-19 / APIUI-12: SWEEPS lists today's SWEEP_IN / SWEEP_OUT of the account (from the served transfers) with the linked fill.
- BM-12 / PO-05 / SR-05: 'OTC IM / VM posted / VM received' from otc_im / otc_vm_posted / otc_vm_received, with a link to each swap's VM
  history (re-run checks\specrev\p4_cash_tab_swap_vm.py).
- BM-19 / OA-19: the loan form only on Credit (elsewhere a link to the Credit page), FX convert replaced by a link to the FX desk (P06);
  no internal names in messages.
- BM-22 / OA-31: loan rows from GET /api/credit/loans (C26, P08): accrued, index value + as-of, spread, all-in rate; the repay toast
  reads paid_interest / paid_principal; a 'full payoff' option.
- PO-06: figures update in place; forms are never rebuilt by the 15 s poll (typed values survive).
- PO-17: PB financing on / off, headroom, limit and drawn next to Draw / Repay with the switch inline; SOFR value, all-in rate and
  accrued on the PB loan row; no raw field names.
- BM-34 part: the cash grid's headers wrap ('BEYOND OWN CASH' fully visible).

UI acceptance:
- Close on PRV-BYTEDANCE opens the negotiated SELL in the dock; 'Mark as listed' moves a company to listed; 'SpaceX' finds nothing tradeable in Private Markets and finds SPCX in US Exchange.
- Risk shows exposure by class / account / currency, greeks by underlying, DV01 rows incl. a held bond, financing & liquidity, per-PB and OTC blocks and counterparty exposure; figures tie to Portfolio.
- A held bond moves in the +/-100 bp scenarios; the futures maintenance notice on a closing order shows the margin after the close.
- Cash & financing: sweeps listed with their fills, VM posted / received shown, typed values survive 30 s, loan rows complete, PB financing state and headroom visible.

### P12 - PB OTC options and forwards, PB schedules and relationships

- depends_on: P09
- findings (7): PO-01 PO-14 PO-16 PO-19 PO-20 SHELL-28 SR-10
- owns:
  - `finsim/finsim/terminal/prime.py`
  - `finsim/finsim/terminal/products/otc_options.py`
  - `finsim/finsim/terminal/static/components/pbschedule.js`
  - `finsim/finsim/terminal/static/pages/markets_otc.js`
  - `finsim/finsim/terminal/static/components/otcticket.js`
  - `finsim/tests/test_terminal_prime.py`

OWNER: "cmon, OTC with prime broker isnt built either". Build OTC equity and FX options and FX / equity forwards with the prime broker
as counterparty under the recorded ISDA / CSA, priced from live spot and vols, daily VM under the CSA.

BUILD
1. products/otc_options.py (bound as rt.otc_options, C2; booked in otc_contracts whose kinds P01 widens, C4; dispatched by P09's
   _delegate, C21): EQ_OPTION (European / American on a US equity, ETF or index; vol = the Cboe chain IV interpolated at strike and
   expiry (marks.interpolate_iv), spot live, r SOFR / curve, q from the dividend events); FX_OPTION (European on a USD pair or cross,
   Garman-Kohlhagen; vol = the ATM IV of the CurrencyShares ETF chain on Cboe (FXE EUR, FXB GBP, FXY JPY, FXF CHF, FXC CAD, FXA AUD)
   labelled 'proxy', else 30-day realised vol from Yahoo daily labelled 'realised - no free implied vol'); EQ_FORWARD (F = S x
   e^((r - q) T), r from the PB financing schedule); FX_FORWARD (fx.forward_rate, C17). Preview: price / forward, greeks, the dealer
   half-width and IM from the PB schedule (C22), eligibility (ISDA + CSA + PBA recorded, NAV above the ECP floor); booking; premium
   settlement T+1 (equity) / T+2 (FX); daily VM on the model mark through SWAP_VM (C21); expiry / maturity (cash-settled at the observed
   close of the underlying, FX at the observed 17:00 ET close; deliverable FX forwards exchange both currencies at value date); early
   termination at model +/- half-width; OTC_EXPIRY job via add_product_job (C13). Every number labelled computed with its inputs.
2. pages/markets_otc.js registers T.marketPlugins.PB with tabs ['otc']: sub-tabs Swaps (P09's swapticket mounted with notes off) |
   Options | Forwards (components/otcticket.js) above one contracts table (all kinds); the eligibility reason above the tickets with
   Preview / Book disabled when !(pb_otc && ecp) (PO-14); no empty instrument grid, no add-symbol box, the SWAP_* notes once (PO-20);
   the PB account header shows 'ISDA Master / CSA / PBA with <dealer>, signed <date>' (PO-16).
3. prime.py: SR-10 IM fields per broker (irs_im_bp, trs_im_pct, xccy_im_pct, option_im_pct, forward_im_pct; assumed seeds that differ
   by broker), im_params (C22); FIELD_SPECS (label, unit, range); seed_schedule served through P01's C9; require_otc_kind accepts the
   new kinds; a 'Close relationship' route for flat PB accounts (POST /api/accounts/{id}/close via register_routes) calling account.close.
4. components/pbschedule.js (PO-19 / SHELL-28): seed column, labels instead of keys, rates typed in % or bp and converted to fractions on
   save, ranges validated in the form, 'opened <date>' until the first edit, tidy action row and select width; the agreements and the
   'Close relationship' button for flat PB accounts (PO-16).

FINDINGS: PO-01, PO-14, PO-16, PO-19, PO-20, SHELL-28, SR-10 (re-run checks\specrev\p11_tables_editable.py for the IM fields).
Hand-work in the result one OTC option preview (inputs -> price -> IM) and one VM day.

UI acceptance:
- Fund Book GS OTC tab shows Swaps / Options / Forwards; an AAPL 3M 95 % put prices from the Cboe IV with greeks and IM and books (offline with a fed chain); the contract appears with mark and VM.
- EURUSD 1M ATM FX option prices with the FXE proxy vol labelled; a 3M FX forward and an equity forward price and book.
- The next 17:05 VM run posts VM for the new kinds (phase-C style clock).
- Below the ECP floor the OTC tab shows the reason up front with Preview / Book disabled; no empty grid on the OTC tab.
- Settings > Prime brokers shows seed values, accepts '1.25 %', shows the agreements and closes a flat PB relationship.

## Already fixed (with evidence)

- **USX-01**: finsim/finsim/terminal/static/pages/positions.js closeSide() / keySide() and components/ticket.js CLOSE_SIDES + settleSide() (hotfix 14:56, BUILD_LOG line 71): Close on a long option prefills STC; before/after browser check with the original JS: BTO (before) / STC (after).
- **USX-02**: finsim/finsim/terminal/orders_preview.py _futures_effect() (hotfix 14:56): a closing futures order RELEASES initial margin, need = max(0, IM change + fee) = 0, only opening contracts count for the 25 % NAV confirm; check_futures_preview.py ALL PASS (BUILD_LOG line 71). The remaining FUTURES_MAINTENANCE notice text is ORC-1 (P11).
- **USX-03**: finsim/finsim/terminal/static/components/ticket.js resetOrder() + preview sequence bump on a new instrument (qty empty, MKT, DAY, no hedge, in-flight preview dropped) (hotfix 14:56; BUILD_LOG line 71: the stale SPY delta hedge / BUY 5 ESZ26 LMT 13.31 race reproduced before and gone after).
- **USX-04**: finsim/finsim/terminal/api_products.py markets_view equities tab: fill(EQUITY_KINDS, None, ...) lists every ACTIVE FULL listing the account trades (no 60 cap; MARKET_ROWS now only caps DEBT bonds, handled by OA-03 in P07); BUILD_LOG lines 61 / 67 / 69: 761 equities rendered, filter '1 of 761'; test_terminal_instruments_foreign asserts every ACTIVE FULL US equity (line 65).
- **USX-07**: finsim/finsim/terminal/static/core.js chain poller chainFast: a 202 is asked again after its retry_ms (500-5000 ms, at most 40 tries) instead of the next 30 / 60 s poll; BUILD_LOG line 69: chain 202 at 0.05 s -> 200 at 1.77 s -> drawn at 1.8 s.
- **USX-15**: finsim/finsim/terminal/config.py QUOTE_INTERVALS['BROWSE'] = 1200 and BROWSE_PER_MIN = 50; quotes/cache.py BROWSE class with a token bucket; api_products._list_quotes subscribes list rows BROWSE; BUILD_LOG lines 62 / 63: max 50 (41 with irregular ticks) BROWSE fetches per rolling 60 s, every name every 1,200 s; the real CLI serve cost 45-50 Yahoo requests a minute.
- **SHELL-01**: finsim/finsim/terminal/static/core.js mergeSessions(): the partial sessions of /api/quotes are merged into the full map instead of replacing it; BUILD_LOG line 69: header chips 5 of 5 and 12 session groups after the /api/quotes and /api/sessions polls (pre-change: collapsed to 'US EQ').
- **OA-01**: finsim/finsim/terminal/marks.py shared_reader / reading / MarkProvider.private_conn, products/private.py and private_credit.py read on the thread's one connection, accounting._conn() = shared_reader (fix 14:59, BUILD_LOG line 73): 8 concurrent pollers on book I holding private names + a private loan: 3,888 requests, 0 non-200, 0 mark failures; regression OneReaderServedTests in test_terminal_private_listed.
- **MONEY-1**: finsim/finsim/terminal/credit.py catch_up_accruals() + principal_at_close_c() (13:22, BUILD_LOG line 58): a repayment first posts every night owed through accounting day - 1 on the principal standing at that close; gate-check 14:05 (line 64) s13 18/18 (128.33), skeptic probe 122/122; tests aligned 14:07 (line 65). Follow-ups planned without the id: LOAN_MATURE job (P04), loan rows / payoff UI (P08 C26 + P11).
- **TX-6**: finsim/finsim/terminal/quotes/cache.py dirty-set persist() in one short writer transaction outside the command journal + prune(idle 1 h); quotes.persist_batch retired; db.py _compact_startup() (fix 14:52, BUILD_LOG line 70): 0 persist commands, logical growth ~0.07 KB/min after the list fills; a 605 MB bloated DB compacts to 3.4 MB at start.

## Deferred

None. Every open finding can be built honestly now with free sources; where a source is missing or blocked the package shows the item as LIMITED / unavailable with its reason instead of deferring it (UK gilts monthly from FRED, TIPS without a reachable index ratio, FX options on a proxy or realised vol, corporates without an OAS print).

## Not a problem

None. Duplicates (the same defect seen by several sweepers) are fixed once in one package and listed there together: BM-04 / SHELL-05 / APIUI-02, BM-07 / SHELL-04 / APIUI-03, BM-08 / SHELL-06, BM-14 / APIUI-14, BM-17 / USX-24 / OA-32, BM-01 / APIUI-04, BM-03 / SR-04 / PO-12, SHELL-07 / APIUI-15, SHELL-17 / PO-22 / BM-32, SHELL-16 / PO-15, BM-15 / USX-11, SHELL-24 / USX-37, USX-12 / ORC-4, USX-13 / APIUI-08, USX-14 / APIUI-06, USX-21 / APIUI-13, BM-34 / USX-29, OA-16 / APIUI-05, SR-14 / APIUI-01, BM-11 / USX-19 / APIUI-12, BM-12 / PO-05 / SR-05, BM-19 / OA-19, BM-22 / OA-31, ORC-6 / PO-02.

## Orchestrator follow-ups (ORC) as given

- **ORC-1**: risk.py ~796-800: the FUTURES_MAINTENANCE notice on a CLOSING future reads 'maintenance after the trade 31,800.00 (1 new + 1 held)'; use the signed held qty, after = held +/- qty, maintenance x |after| (informational notice, never a block).
- **ORC-2**: static/pages/instrument.js ~98 opens the ticket with side BUY on every visit (option pages then prompt 'choose a side'): pass no side, or BTO for options; markets.js / watchlists.js b/s keys pass BUY/SELL for every kind (option rows too): use the kind's own sides.
- **ORC-3**: accounting.reserved_c reserves the full notional for a working futures BUY even when it only covers a short: reserve initial margin on OPENING contracts only.
- **ORC-4**: Positions > Hedge opens a new BUY of the position's size: needs a hedge-only design (hedge candidates for the position, not a duplicate order).
- **ORC-5**: orders_preview.validate holds a raw pooled reader around accounting, compliance + risk pretrade, hedges, prime.schedule: use marks.shared_reader there.
- **ORC-6**: swaps.preview nests books.require_pb_otc / eligibility / swaps_trs.preview readers: use the shared reader (page loads exhausted the pool with swaps held).
- **ORC-7**: db.py: make Database.reader re-entrant per thread (defense in depth against nested pooled readers).
- **ORC-8**: FRED site maintenance serves HTML and the refresher logs 'unexpected FRED CSV header': detect it, show 'FRED unavailable (maintenance)', keep the last good values with their true as-of.
- **ORC-9**: stale version literals: T.VERSION = '1.0.0' in static/core.js:26 and the 1.0.0 POWERSHELL_SEQUENCE text in app.py:113-114: derive them from finsim.terminal.VERSION.
- **ORC-10**: MONEY-2 blocks shipping swaps (release copies exclude products/swaps.py, swaps_trs.py, components/swapticket.js): fix it and make the PB OTC products deployable, or say exactly what still blocks them.

## Coverage check

221 ids in the inputs; 221 placed exactly once (packages 211, already fixed 10, deferred 0, not a problem 0); duplicates none; missing none; unknown none; file-ownership clashes none.
