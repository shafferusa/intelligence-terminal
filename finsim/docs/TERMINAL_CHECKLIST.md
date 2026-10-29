# FinSim Live Terminal — integration checklist (WP-15, 2026-10-21)

What has to be true before a release of the terminal goes to the owner's machine, how each item is checked, and where the
evidence is.  WP-15 (integration) wrote this page; WP-16 (verification and deployment) and the owner use it.  Nothing on
this page is done against the live app: every check runs on the repo copy, on temp homes, with the network off.

## 1. Automated gates

| gate | command (from `finsim/`) | at WP-15 |
|---|---|---|
| terminal suite, Python 3.12 | `py -3.12 <scratchpad>/run_suite.py` (every `tests/test_terminal_*.py`, one process) | 505 OK, 1 skipped (Build 2 marks pin), 44 files, swap tests included, 166 s |
| terminal suite, Python 3.14 | `py -3.14 <scratchpad>/run_suite.py` | 505 OK, 1 skipped, 44 files, 186 s |
| one file | `py -3.12 -m unittest tests.test_terminal_<name>` | — |
| offline smoke (real server) | `py -3.12 <scratchpad>/wp15/smoke.py <finsim> <checks>` (§4) | 53 pass / 0 fail |
| ticket JS on real payloads | `cscript //nologo //E:jscript <scratchpad>/wp15/js/harness.js <dir> <swapticket.js>` | every TRS / XCCY path, no exception |

Rules every gate keeps: standard library only; `Decimal` / integer cents for money; tests never touch the network (fake
providers) and always use temp homes; Python 3.12 has no tzdata (zones through `finsim.tzfallback.get_zone`).

## 2. Acceptance tests D1-D13 (BUILD_PROMPT_2026-10-20.md §D) — where each is proved

`tests/test_terminal_reconcile.py::DMapTests` fails if any method named here disappears.

| D | proved by (hand-worked numbers in each) |
|---|---|
| D1 Treasury 10,000,000 → Add funds 2,500,000 → 12,500,000; NAV +2,500,000 as a capital flow, P&L 0 | `test_terminal_treasury::test_d1_initial_capital_and_deposit`; the three books' capitals `test_terminal_books::test_first_run_three_books`; through the API in one DB `test_terminal_reconcile::test_d1_d2_d12_through_the_api_in_one_db` |
| D2 Buy 100 AAPL with 0 cash: sweep = 100 × price + commission, linked to the fill, T+1, NAV − commission | `test_terminal_sweeps::test_d2_buy_100_aapl_zero_cash` (33,950.00 + 1.00 = 33,951.00; NAV 9,999,999.00); `test_terminal_api::test_preview_submit_order_fills_transactions`; `test_terminal_reconcile::test_d1_d2_d12_…` (sweep linked to the fill id, settled Wed 00:35, NAV 12,499,999.00) |
| D3 Restart between steps, nothing lost or duplicated; a second server refuses | `test_terminal_persistence::test_restart_between_every_step`; `test_terminal_server::test_duplicate_server_exit_3`; `test_terminal_reconcile::test_d3_restart_keeps_everything_and_the_lock_refuses_a_second_server`; the real server in §4 (restart keeps the PB account, the swap, its IM and VM; replays answer the stored result) |
| D4 Short 1,000 with borrow; collateral 102 %; +10 % → call 1.02 × 1,000 × ΔP; −5 % → excess back; 3 nights over a weekend | `test_terminal_shorts::test_d4_collateral_marks_both_ways` (5,100.00 call, 2,805.00 back) |
| D5 No borrow → rejected; partial borrow → short scaled down | `test_terminal_shorts::test_d5_no_borrow_and_partial` |
| D6 Outside hours queues and fills at the first quote after the open; buy limit only at ≤ limit; DAY expires; cancelled / expired stay | `test_terminal_orders::test_d6_session_limit_day_expiry`; `test_terminal_api::test_orders_history_keeps_cancelled_expired` |
| D7 Stock + protective put from the pop-up, together; a partial fill resizes the hedge | `test_terminal_orders::test_d7_hedge_package_resize` (12,000 → 120, 21,000 → 210); `test_terminal_hedges` |
| D8 Option expiry on the real date: ITM long call exercises, OTM lapses, short ITM assigned | `test_terminal_options::test_d8_expiry_exercise_lapse_assignment` |
| D9 Borrow EUR 1,000,000, convert the same day; T+2; repaying needs EUR | `test_terminal_fx::test_d9_eur_loan_and_spot` |
| D10 Goldman account: its commission, DVP, margin per PB, exposure per PB | `test_terminal_prime::test_d10_goldman_buy`; `test_terminal_risk::test_exposure_per_pb`; `test_terminal_reconcile::test_d10_goldman_and_pb_swaps_with_daily_vm_through_the_api` (fee 3.00, settlement row in F1:PB_GS, the PB row on Risk) |
| D11 Rule 201 / 13D-G / self-trading notices; none blocks | `test_terminal_risk::test_d11_compliance_notices` |
| D12 integrity() after every step; a corrupted entry fails and shows under "Requires your attention" | `test_terminal_bus::test_d12_corrupted_balance_rolls_back`; `test_terminal_news::test_attention_shows_integrity_failure`; `test_terminal_reconcile` (integrity after every step; +0.01 on one balance → L3, ERROR attention, the book held, restored → clean) |
| D13 The Desk tests pass exactly as before | WP-16's baseline diff (`tests/test_fs2_*`, `tests/test_desk_*` against the scratchpad `baseline/`); not part of WP-15 |

## 3. The reconciliation suite (`tests/test_terminal_reconcile.py`, §19.12)

One database per test, the REAL runtime (`server.build_runtime`, every module bound as `serve` binds it) driven through the
HTTP API the UI uses (FakeClock, FakeProvider), four books (T1 10,000,000; P1 1,000,000; F1 25,000,000; I1
1,000,000,000).  After every step, per open lifetime: integrity full without an ERROR (L1-L25); balance sheet assets −
liabilities = NAV with rounding 0; `NAV = capital + advances + realised + income − financing − fees + unrealised +
fx_result` to the cent for the lifetime AND each account (swap overlays included); Σ accounts' NAV = the lifetime's NAV;
advances (3100) net to 0 over the lifetime and Treasury's advances = Σ accounts' 3100; the trial balance is 0 per currency
natively and 0 in base; `transactions()` lists every journal entry of every account exactly once; `pnl(ITD)` residual 0.00
and = NAV − capital; `ALL_BOOKS` = Σ lifetimes.  The flows: D1 / D2 / D12 in T1; D3 restart; D10 with an IRS (booked,
VM day 1 −4,491.29 posted, day 2 at 4.10 % a zero crossing to +40,316.09, NAV 25,040,313.09), a TRS on a name held
nowhere and a cross-currency swap in I1 (their references pinned and recorded); Private Markets without SpaceX; the jobs
API; the bonds route and setting.

## 4. Offline smoke on a fresh temp home (real server; the result of the WP-15 run)

Phase A — the CLI exactly as prescribed: `py -3.12 -m finsim serve --offline --port 0 --home <scratchpad>\checks\wp15-smoke-A-*`.
Phase B — the same server code (`finsim.terminal.server.serve`, through `wp15/smoke_driver.py`) with a FakeClock at Wed
2026-10-21 17:10 ET and a FakeProvider holding a par curve (flat 4.00 %) and SOFR 3.87 % — the offline CLI has no curve,
so a swap can only be priced, and the 17:05 VM key is only due, with that injection.

| check | result |
|---|---|
| A: ready on an ephemeral port; health `finsim-terminal` 1.0.1, offline, integrity ok | pass |
| A: every module bound (orders … swaps, swaps_trs, private, private_credit, dividends, bonds, lifecycle) | pass |
| A: books P / F / I | pass |
| A: Fund Book onboards Goldman → F1:PB_GS + ISDA Master, CSA, PBA; Personal Book refused NOT_ELIGIBLE with the reason | pass |
| A: PB OTC tab `available` (IRS / TRS / XCCY); IRS preview → 409 NO_CURVE (nothing invented offline); TRS preview reaches WP-26 (409 NO_QUOTE, no 501) | pass |
| A: Private Markets: 21 private names, SpaceX / Cerebras / xAI / Anysphere only under "listed" ("Listed 2026-06-12: SPCX on Nasdaq - trade it in US Exchange"); the account's markets view without them | pass |
| A: jobs include SWAP_* / TRS_RESET / XCCY_EVENTS / BOND_*; `jobs/run {"job": "SWAP_VM"}` runs only that job | pass |
| A: integrity full ok; shutdown → exit 0, both pids gone; restart → PB account persisted, onboarding replays, integrity ok; shutdown, gone | pass |
| B: IRS 5y preview par 4.000 %, dealt 4.010 %, mark0 −4,491.29, DV01 4,491.29, IM 224,564.50 (hand-worked: A = 4.491292505) | pass |
| B: booked SWP-000001, IM swept from F1:TREASURY; NAV(F1) 24,995,508.71 | pass |
| B: `jobs/run SWAP_VM` → the 10-21 key DONE; VM −4,491.29 posted (CTM); ledger Dr 1320 / Cr 1010 4,491.29; NAV unchanged; integrity ok | pass |
| B: shutdown, restart: the contract, IM, VM and NAV identical; SWAP_VM has nothing left (never twice); the booking id replays; integrity ok | pass |
| no process left (tasklist) | pass (53 / 53) |

## 5. Browser checklist (§19.12; run on a scratch home, every page at 1100 and 1440 px, console and network tabs open)

Start a scratch server (never the live home): `py -3.12 -m finsim serve --port 0 --home <scratch>` (online) or
`--offline` for the layout-only items.  Expected results in brackets.

1. Header [PAPER TRADING tag; the clock paints at once; integrity dot green]; sidebar book switcher [Personal 1,000,000.00 /
   Fund 25,000,000.00 / Institutional 1,000,000,000.00; Alt+B cycles; every page follows the book].
2. Daily News [real headlines (online); the "Requires your attention" strip with provider state, integrity and the
   working-orders count; offline: the news providers DOWN with their reason].
3. US Exchange → AAPL → ticket [preview within ~400 ms of typing stop; every price with its freshness chip; the hedge
   candidates]; in session submit 1 share [Positions and Transactions show it; the sweep-in linked]; F5 [unchanged];
   `stop` / restart [the position is still there].
4. Fund Book → "+ Open prime broker account" → Goldman [ISDA Master + CSA + PBA recorded; F1:PB_GS in the sidebar];
   its OTC tab: IRS preview [par, dealt, cost of dealing, DV01, IM, schedule, fixing], TRS preview [observed reset
   price with freshness, notional, financing rate and direction, reset dates, IM 10 %], cross-currency preview [deal FX
   with freshness, both legs' quote / par / rate, exchanges, IM 2 %]; blank XCCY rates deal at the quote; the contracts
   table and history after a booking.
5. Personal Book → "+ Open prime broker account" [refused with the plain-English ECP / threshold sentence].
6. Institutional Book → Private Markets [the private names grid; SpaceX only in the small "no longer private" panel, its
   line opening SPCX in US Exchange]; a secondary preview [negotiated price, fee, settlement lag, lock-up].
7. Settings → Reset lifetime on a scratch book [typed RESET; History lists the closed lifetime read-only with its final
   NAV; the new lifetime has its capital].
8. Positions drawer of a swap [the overlay and the live model mark, VM balance, IM]; of a dividend payer [dividend notes].
9. Risk [exposures, DV01, the PB row with headroom]; Accounting [balance sheet ties: memo NAV = capital + advances +
   retained]; Treasury [cash in decimal units, SOFR 3.87 %].
10. Console: no errors; network: no 4xx / 5xx other than the expected refusals above.

## 6. UI clean-up list (directive −3: every inconvenience found and what was done)

| found | fix | where / when |
|---|---|---|
| Foreign listings (London, Hong Kong …) seeded as USD US Exchange stocks with wrong Yahoo symbols | foreign listings in their own currency under Foreign Markets & FX, repaired in place at start | instruments.py, 1.0.1 |
| Sidebar showed "avail −347.29" for a resting order | available floored at 0; "reserved (Treasury sweep covers N)" beside it | accounting.py / sidebar.js, 1.0.1 |
| `status` printed providers DOWN before their first call | idle / ok / DOWN with the reason; times in ET | app.py, 1.0.1 |
| Markets poll rebuilt the page: filter cleared, a click on b / s swallowed | in-place updates keep filter, sort, selection, scroll; a pressed pointer holds the render | grid.js / markets.js, 1.0.1 |
| Header clock `--:--:--` on first paint | clock painted at load; ISO sync fixed | header.js, 1.0.1 |
| Treasury cash shown in cents as dollars | every cents dict converted on the wire | api_views.py, 1.0.1 |
| SOFR 387.00 % | one rate-unit definition; percent vs fraction formatters | quotes/rates.py / core.js / cash.js, 1.0.1 |
| A second order accepted on cash the first would sweep (then SUSPENDED) | buying power counts commitments; refused at submit | accounting.py, 1.0.1 |
| PB schedule editor zeroed the minimum commission on another field's Save | only edited fields sent; money parsed strictly | pbschedule.js / prime.py, 1.0.1 |
| Portfolio → Performance answered 400 | SOFR read as a fraction | performance.py, 1.0.1 |
| SpaceX in Private Markets (owner) | listed / acquired companies out of the private list; a small "no longer private" panel; the API markets view too | private.py (1.0.1) + api_products.py (WP-15) |
| Swap ticket showed TRS / XCCY with IRS fields (IM "|DV01| × 50 bp", par / dealt rows) and forced XCCY rates | kind-specific previews, blank rates = the quote, kind-aware confirmation | swapticket.js, WP-15 |
| Contracts table read every TRS as RECEIVE_FIXED and hid an XCCY's EUR accruals / notionals | side and specifics from WP-26; balances per currency | swaps.py / swapticket.js, WP-15 |
| A swap row's mark (live model) disagreed with its market value (the overlay) | the row shows the overlay; the drawer shows both | accounting.py / positions.js, WP-15 |
| TRS templates / the TRS overlay offered without the TRS module | gated on WP-26 | strategies.py / hedges.py, WP-15 |
| Dividend state invisible on a position | the drawer lists the dividends with their convention | api_views.py / positions.js, WP-15 |
| Ticket CSS injected from ticket.js | open (cosmetic) | — |
| 409 PREVIEW_STALE carries an unstored preview (one extra round trip) | open (works) | — |

## 7. For WP-16 / the next release (nothing deployed by WP-15)

- The release must now INCLUDE `products/swaps.py`, `products/swaps_trs.py` and `static/components/swapticket.js` (1.0.1
  excluded the swap modules); `products/bonds.py` / `bonds_jobs.py` are wired too (DEBT execution stays off until the
  owner sets `build2_bonds`).
- New since 1.0.1: the interface requests of BUILD_NOTES "WP-15 integration" (§ "Interface requests"), the bond jobs in the
  lifecycle (the monthly model benchmarks are listed on the first business day from the live curve — LIMITED rows on the
  Debt page), `jobs/run {job}`, the Private account markets view, the swap position row / drawer, the dividend notes.
- The deploy itself, the release copy, the browser pass on the scratch home (§5), the Desk baseline diff (D13) and the
  version bump are WP-16 / the owner's decision; the owner's typed go is required (no agent deploys).

## 8. Directive section 18 test map (TEST-08)

The owner's directive section 18 lists 25 test areas. `tests\test_terminal_directive_map.py` maps each one to the tests that prove it (DIR_MAP) and fails when a mapped test disappears, is renamed or would be skipped. `python -m tests.test_terminal_directive_map --run`, run from the finsim folder, runs exactly these tests and prints one line per area.

| Directive section 18 area | Tests (module.method) |
|---|---|
| 1 Pending market orders | test_terminal_orders.test_market_and_gates<br>test_terminal_orders.test_session_rests_until_open<br>test_terminal_orders.test_stale_quote_then_limit_fills<br>test_terminal_order_amend.test_f_a_next_session_order_does_not_fill_before_its_session<br>test_terminal_blotters.test_open_lists_the_live_orders_with_their_stages |
| 2 Pending limit orders | test_terminal_blotters.test_a_resting_limit_changes_no_position_and_no_nav_and_reserves_its_cost<br>test_terminal_orders.test_limit_rule_cells<br>test_terminal_orders.test_d6_session_limit_day_expiry<br>test_terminal_order_amend.test_b_amend_re_reserves_and_resets_the_first_look |
| 3 Stop orders | test_terminal_orders.test_stop_cells<br>test_terminal_orders.test_stops_through_the_bus<br>test_terminal_orders.test_trailing_stop_sell_ratchets_and_fills<br>test_terminal_orders.test_trailing_stop_buy_variant |
| 4 Partial fills | test_terminal_orders.test_partial_fills_cancel_and_avg<br>test_terminal_orders.test_option_partial_by_displayed_size<br>test_terminal_blotters.test_d_one_order_two_executions_one_parent<br>test_terminal_order_amend.test_amend_keeps_what_has_filled |
| 5 Canceled orders | test_terminal_orders.test_partial_fills_cancel_and_avg<br>test_terminal_api.test_orders_history_keeps_cancelled_expired<br>test_terminal_blotters.test_status_filters<br>test_terminal_order_amend.test_c_replace_of_a_working_short_keeps_its_borrow_and_links_both_orders<br>test_terminal_funds_queue.test_cancel_of_a_queued_order_resolves_its_item |
| 6 Rejected orders | test_terminal_orders.test_validation_codes<br>test_terminal_api.test_rejected_submit_is_recorded<br>test_terminal_blotters.test_rejections_from_every_ticket<br>test_terminal_shorts.test_d5_no_borrow_and_partial |
| 7 Completed-trade history | test_terminal_blotters.test_the_union_lists_every_execution_once<br>test_terminal_blotters.test_every_directive_field_is_present<br>test_terminal_blotters.test_paging_and_the_csv<br>test_terminal_blotters.test_the_execution_drawer<br>test_terminal_browser_smoke.test_completed_trades_drawer_opens_with_every_tab |
| 8 Settlement progression | test_terminal_lifecycle.test_settle_due_idempotent_and_unfunded<br>test_terminal_equities.test_19_3_buy_fee_settlement_and_pnl_today<br>test_terminal_settlement_fails.test_f_asof_settlement_posts_on_its_due_date<br>test_terminal_settlement_fails.test_settlement_state_words |
| 9 Failed settlement | test_terminal_settlement_fails.test_a_simulated_fail_moves_no_cash_then_retry_resolves<br>test_terminal_settlement_fails.test_a_cash_compensation_and_buy_in_postings<br>test_terminal_settlement_fails.test_test03_unfunded_payable_fails_after_its_due_date_then_resolves<br>test_terminal_settlement_fails.test_test03_an_otc_payment_follows_the_same_path |
| 10 Historical imports | test_terminal_import.test_dry_run_books_restates_and_leaves_the_source_byte_identical<br>test_terminal_import.test_completed_trades_rows_carry_the_labels<br>test_terminal_import.test_report_statuses_beside_a_writer_leave_the_file_byte_identical<br>test_terminal_asof_listed.test_bto_orcl_books_the_owner_terms_on_the_trade_date<br>test_terminal_otc_asof.test_trs_orcl_short_booked_at_the_dealt_reset_with_its_lifecycle |
| 11 Duplicate-import prevention | test_terminal_import.test_apply_writes_the_backup_first_then_twice_posts_nothing<br>test_terminal_import.test_a_second_dry_run_on_the_result_posts_nothing<br>test_terminal_import.test_a_second_apply_writes_nothing_and_keeps_the_first_applys_record<br>test_terminal_asof_listed.test_replay_posts_nothing_and_a_reused_key_is_refused<br>test_terminal_otc_asof.test_duplicate_line_key_replay_and_reuse |
| 12 Equity long accounting | test_terminal_equities.test_19_3_buy_fee_settlement_and_pnl_today<br>test_terminal_equities.test_19_4_fifo_sell_with_realised<br>test_terminal_accounting.test_equity_buy_positions_nav_pnl_today<br>test_terminal_fund_reconcile.test_mixed_fund_two_days_of_lifecycle_reconciles_after_every_step |
| 13 Physical short accounting | test_terminal_shorts.test_19_5_short_borrow_accruals_mark_cover<br>test_terminal_shorts.test_d4_collateral_marks_both_ways<br>test_terminal_shorts.test_prove_short_regression<br>test_terminal_fund_reconcile.test_mixed_fund_two_days_of_lifecycle_reconciles_after_every_step |
| 14 Futures variation margin | test_terminal_futures.test_19_8_im_two_days_vm_close_out<br>test_terminal_futures.test_19_8_unfunded_vm_variant<br>test_terminal_margin.test_futures_vm_call_met_with_its_sweep_and_an_overdraft_call_met_later<br>test_terminal_fund_reconcile.test_mixed_fund_two_days_of_lifecycle_reconciles_after_every_step |
| 15 Options premiums | test_terminal_asof_listed.test_bto_orcl_books_the_owner_terms_on_the_trade_date<br>test_terminal_asof_listed.test_qqq_bear_put_spread_is_one_command_with_one_package_row<br>test_terminal_options.test_19_7_and_s7_pins<br>test_terminal_options.test_d8_expiry_exercise_lapse_assignment |
| 16 TRS financing | test_terminal_swaps_trs.test_trs_19_16_financing_dividend_vm_and_reset<br>test_terminal_otc_asof.test_trs_orcl_short_booked_at_the_dealt_reset_with_its_lifecycle<br>test_terminal_otc_asof.test_trs_meta_short_vm_by_date |
| 17 CDS valuation | test_terminal_cds.test_risky_annuity_upfront_and_cs01_by_hand<br>test_terminal_cds.test_vm_two_days_against_the_model<br>test_terminal_otc_asof.test_cdx_ig_upfront_to_cost_rebate_initial_payment_and_im<br>test_terminal_otc_asof.test_hy_constituent_default_at_40_percent_recovery |
| 18 IRS valuation | test_terminal_swaps.test_preview_par_dealt_mark0_dv01_im_schedule<br>test_terminal_swaps.test_vm_ctm_day1_day2_zero_crossings_nav_pnl_l22<br>test_terminal_otc_asof.test_irs_pay_fixed_calibrated_curve_spread_and_vm_on_each_dates_curve |
| 19 Cash transfers | test_terminal_treasury.test_transfer_treasury_to_us_exchange<br>test_terminal_sweeps.test_transfer_confirm_sweep_in_and_refusal<br>test_terminal_sweeps.test_d2_buy_100_aapl_zero_cash<br>test_terminal_lifecycle.test_11_5_sweep_out_numbers<br>test_terminal_collateral.test_i_asof_transfer |
| 20 Collateral movements | test_terminal_collateral.test_b_restricted_net_per_csa_available_sweep_and_credit_base<br>test_terminal_collateral.test_d_pledge_eligible_value_encumbrance_and_release<br>test_terminal_shorts.test_d4_collateral_marks_both_ways<br>test_terminal_fund_reconcile.test_mixed_fund_two_days_of_lifecycle_reconciles_after_every_step |
| 21 Prime-broker balances | test_terminal_prime.test_d10_goldman_buy<br>test_terminal_prime.test_financing_draws_up_to_headroom_interest_and_isolation<br>test_terminal_prime.test_19_23_pb_credit_interest_86_67<br>test_terminal_reconcile.test_d10_goldman_and_pb_swaps_with_daily_vm_through_the_api<br>test_terminal_fund_reconcile.test_mixed_fund_two_days_of_lifecycle_reconciles_after_every_step |
| 22 Historical NAV reconstruction | test_terminal_asof_restate.test_split_per_account_and_lifetime<br>test_terminal_asof_restate.test_seam_and_m4_on_the_latest_chain<br>test_terminal_asof_restate.test_nav_history_restated_original_split_and_returns<br>test_terminal_import.test_dry_run_books_restates_and_leaves_the_source_byte_identical |
| 23 Current NAV calculation | test_terminal_accounting.test_balance_sheet_ties_and_memo<br>test_terminal_reconcile.test_d1_d2_d12_through_the_api_in_one_db<br>test_terminal_portfolio_contract.test_portfolio_answer_has_the_shape_the_page_reads<br>test_terminal_fund_reconcile.test_mixed_fund_two_days_of_lifecycle_reconciles_after_every_step |
| 24 Database persistence | test_terminal_persistence.test_restart_between_every_step<br>test_terminal_reconcile.test_d3_restart_keeps_everything_and_the_lock_refuses_a_second_server<br>test_terminal_core.test_reopen_same_file_keeps_state_and_backs_up<br>test_terminal_core.test_open_widens_a_103_otc_contracts_table_keeping_rows_children_and_indexes |
| 25 Application startup | test_terminal_launch.test_help_exits_0_from_the_repo_root_and_from_the_finsim_folder<br>test_terminal_launch.test_offline_serve_from_the_repo_root_ready_second_instance_and_shutdown<br>test_terminal_launch.test_a_database_in_the_1031_schema_starts_migrated_synced_backed_up_and_ok<br>test_terminal_server.test_duplicate_server_exit_3 |
| Browser check ("verify the actual interface in a browser") | test_terminal_browser_smoke.test_every_main_route_renders_without_errors<br>test_terminal_browser_smoke.test_pending_filters_list_the_seeded_orders<br>test_terminal_browser_smoke.test_completed_trades_drawer_opens_with_every_tab |
