"""FINSIM1 P8 task 3 (TEST-08): the directive map - the owner's directive section 18 ("Required testing") lists 25 areas; each one
names the test methods that prove it, and this test fails when one of them disappears, is renamed or would be skipped here, so the
map cannot rot (the D_MAP of test_terminal_reconcile does the same for the D1-D12 acceptance tests).

"Passing" is the suite's job: every mapped test runs in ``tests/run_terminal_suite.py`` on py-3.12 and py-3.14, and the suite is
green.  ``python -m tests.test_terminal_directive_map --run`` (from the finsim folder) runs exactly the mapped tests, area by area,
and prints one line per area with its counts: the evidence line of the end-to-end proof (P8 task 5).

Section 18 ends "Verify the actual interface in a browser"; ``BROWSER_CHECK`` maps that sentence to the browser smoke test.
"""
from __future__ import annotations

import importlib
import inspect
import sys
import unittest

#: the 25 areas of directive section 18, in its order, each with [(test module, test method)]
DIR_MAP = {
    "1 Pending market orders": [
        ("test_terminal_orders", "test_market_and_gates"), ("test_terminal_orders", "test_session_rests_until_open"),
        ("test_terminal_orders", "test_stale_quote_then_limit_fills"), ("test_terminal_order_amend", "test_f_a_next_session_order_does_not_fill_before_its_session"),
        ("test_terminal_blotters", "test_open_lists_the_live_orders_with_their_stages")],
    "2 Pending limit orders": [
        ("test_terminal_blotters", "test_a_resting_limit_changes_no_position_and_no_nav_and_reserves_its_cost"),
        ("test_terminal_orders", "test_limit_rule_cells"), ("test_terminal_orders", "test_d6_session_limit_day_expiry"),
        ("test_terminal_order_amend", "test_b_amend_re_reserves_and_resets_the_first_look")],
    "3 Stop orders": [
        ("test_terminal_orders", "test_stop_cells"), ("test_terminal_orders", "test_stops_through_the_bus"),
        ("test_terminal_orders", "test_trailing_stop_sell_ratchets_and_fills"), ("test_terminal_orders", "test_trailing_stop_buy_variant")],
    "4 Partial fills": [
        ("test_terminal_orders", "test_partial_fills_cancel_and_avg"), ("test_terminal_orders", "test_option_partial_by_displayed_size"),
        ("test_terminal_blotters", "test_d_one_order_two_executions_one_parent"), ("test_terminal_order_amend", "test_amend_keeps_what_has_filled")],
    "5 Canceled orders": [
        ("test_terminal_orders", "test_partial_fills_cancel_and_avg"), ("test_terminal_api", "test_orders_history_keeps_cancelled_expired"),
        ("test_terminal_blotters", "test_status_filters"), ("test_terminal_order_amend", "test_c_replace_of_a_working_short_keeps_its_borrow_and_links_both_orders"),
        ("test_terminal_funds_queue", "test_cancel_of_a_queued_order_resolves_its_item")],
    "6 Rejected orders": [
        ("test_terminal_orders", "test_validation_codes"), ("test_terminal_api", "test_rejected_submit_is_recorded"),
        ("test_terminal_blotters", "test_rejections_from_every_ticket"), ("test_terminal_shorts", "test_d5_no_borrow_and_partial")],
    "7 Completed-trade history": [
        ("test_terminal_blotters", "test_the_union_lists_every_execution_once"), ("test_terminal_blotters", "test_every_directive_field_is_present"),
        ("test_terminal_blotters", "test_paging_and_the_csv"), ("test_terminal_blotters", "test_the_execution_drawer"),
        ("test_terminal_browser_smoke", "test_completed_trades_drawer_opens_with_every_tab")],
    "8 Settlement progression": [
        ("test_terminal_lifecycle", "test_settle_due_idempotent_and_unfunded"), ("test_terminal_equities", "test_19_3_buy_fee_settlement_and_pnl_today"),
        ("test_terminal_settlement_fails", "test_f_asof_settlement_posts_on_its_due_date"), ("test_terminal_settlement_fails", "test_settlement_state_words")],
    "9 Failed settlement": [
        ("test_terminal_settlement_fails", "test_a_simulated_fail_moves_no_cash_then_retry_resolves"),
        ("test_terminal_settlement_fails", "test_a_cash_compensation_and_buy_in_postings"),
        ("test_terminal_settlement_fails", "test_test03_unfunded_payable_fails_after_its_due_date_then_resolves"),
        ("test_terminal_settlement_fails", "test_test03_an_otc_payment_follows_the_same_path")],
    "10 Historical imports": [
        ("test_terminal_import", "test_dry_run_books_restates_and_leaves_the_source_byte_identical"),
        ("test_terminal_import", "test_completed_trades_rows_carry_the_labels"), ("test_terminal_import", "test_report_statuses_beside_a_writer_leave_the_file_byte_identical"),
        ("test_terminal_asof_listed", "test_bto_orcl_books_the_owner_terms_on_the_trade_date"),
        ("test_terminal_otc_asof", "test_trs_orcl_short_booked_at_the_dealt_reset_with_its_lifecycle")],
    "11 Duplicate-import prevention": [
        ("test_terminal_import", "test_apply_writes_the_backup_first_then_twice_posts_nothing"),
        ("test_terminal_import", "test_a_second_dry_run_on_the_result_posts_nothing"),
        ("test_terminal_import", "test_a_second_apply_writes_nothing_and_keeps_the_first_applys_record"),
        ("test_terminal_asof_listed", "test_replay_posts_nothing_and_a_reused_key_is_refused"),
        ("test_terminal_otc_asof", "test_duplicate_line_key_replay_and_reuse")],
    "12 Equity long accounting": [
        ("test_terminal_equities", "test_19_3_buy_fee_settlement_and_pnl_today"), ("test_terminal_equities", "test_19_4_fifo_sell_with_realised"),
        ("test_terminal_accounting", "test_equity_buy_positions_nav_pnl_today"),
        ("test_terminal_fund_reconcile", "test_mixed_fund_two_days_of_lifecycle_reconciles_after_every_step")],
    "13 Physical short accounting": [
        ("test_terminal_shorts", "test_19_5_short_borrow_accruals_mark_cover"), ("test_terminal_shorts", "test_d4_collateral_marks_both_ways"),
        ("test_terminal_shorts", "test_prove_short_regression"),
        ("test_terminal_fund_reconcile", "test_mixed_fund_two_days_of_lifecycle_reconciles_after_every_step")],
    "14 Futures variation margin": [
        ("test_terminal_futures", "test_19_8_im_two_days_vm_close_out"), ("test_terminal_futures", "test_19_8_unfunded_vm_variant"),
        ("test_terminal_margin", "test_futures_vm_call_met_with_its_sweep_and_an_overdraft_call_met_later"),
        ("test_terminal_fund_reconcile", "test_mixed_fund_two_days_of_lifecycle_reconciles_after_every_step")],
    "15 Options premiums": [
        ("test_terminal_asof_listed", "test_bto_orcl_books_the_owner_terms_on_the_trade_date"),
        ("test_terminal_asof_listed", "test_qqq_bear_put_spread_is_one_command_with_one_package_row"),
        ("test_terminal_options", "test_19_7_and_s7_pins"), ("test_terminal_options", "test_d8_expiry_exercise_lapse_assignment")],
    "16 TRS financing": [
        ("test_terminal_swaps_trs", "test_trs_19_16_financing_dividend_vm_and_reset"),
        ("test_terminal_otc_asof", "test_trs_orcl_short_booked_at_the_dealt_reset_with_its_lifecycle"),
        ("test_terminal_otc_asof", "test_trs_meta_short_vm_by_date")],
    "17 CDS valuation": [
        ("test_terminal_cds", "test_risky_annuity_upfront_and_cs01_by_hand"), ("test_terminal_cds", "test_vm_two_days_against_the_model"),
        ("test_terminal_otc_asof", "test_cdx_ig_upfront_to_cost_rebate_initial_payment_and_im"),
        ("test_terminal_otc_asof", "test_hy_constituent_default_at_40_percent_recovery")],
    "18 IRS valuation": [
        ("test_terminal_swaps", "test_preview_par_dealt_mark0_dv01_im_schedule"), ("test_terminal_swaps", "test_vm_ctm_day1_day2_zero_crossings_nav_pnl_l22"),
        ("test_terminal_otc_asof", "test_irs_pay_fixed_calibrated_curve_spread_and_vm_on_each_dates_curve")],
    "19 Cash transfers": [
        ("test_terminal_treasury", "test_transfer_treasury_to_us_exchange"), ("test_terminal_sweeps", "test_transfer_confirm_sweep_in_and_refusal"),
        ("test_terminal_sweeps", "test_d2_buy_100_aapl_zero_cash"), ("test_terminal_lifecycle", "test_11_5_sweep_out_numbers"),
        ("test_terminal_collateral", "test_i_asof_transfer")],
    "20 Collateral movements": [
        ("test_terminal_collateral", "test_b_restricted_net_per_csa_available_sweep_and_credit_base"),
        ("test_terminal_collateral", "test_d_pledge_eligible_value_encumbrance_and_release"), ("test_terminal_shorts", "test_d4_collateral_marks_both_ways"),
        ("test_terminal_fund_reconcile", "test_mixed_fund_two_days_of_lifecycle_reconciles_after_every_step")],
    "21 Prime-broker balances": [
        ("test_terminal_prime", "test_d10_goldman_buy"), ("test_terminal_prime", "test_financing_draws_up_to_headroom_interest_and_isolation"),
        ("test_terminal_prime", "test_19_23_pb_credit_interest_86_67"),
        ("test_terminal_reconcile", "test_d10_goldman_and_pb_swaps_with_daily_vm_through_the_api"),
        ("test_terminal_fund_reconcile", "test_mixed_fund_two_days_of_lifecycle_reconciles_after_every_step")],
    "22 Historical NAV reconstruction": [
        ("test_terminal_asof_restate", "test_split_per_account_and_lifetime"), ("test_terminal_asof_restate", "test_seam_and_m4_on_the_latest_chain"),
        ("test_terminal_asof_restate", "test_nav_history_restated_original_split_and_returns"),
        ("test_terminal_import", "test_dry_run_books_restates_and_leaves_the_source_byte_identical")],
    "23 Current NAV calculation": [
        ("test_terminal_accounting", "test_balance_sheet_ties_and_memo"), ("test_terminal_reconcile", "test_d1_d2_d12_through_the_api_in_one_db"),
        ("test_terminal_portfolio_contract", "test_portfolio_answer_has_the_shape_the_page_reads"),
        ("test_terminal_fund_reconcile", "test_mixed_fund_two_days_of_lifecycle_reconciles_after_every_step")],
    "24 Database persistence": [
        ("test_terminal_persistence", "test_restart_between_every_step"),
        ("test_terminal_reconcile", "test_d3_restart_keeps_everything_and_the_lock_refuses_a_second_server"),
        ("test_terminal_core", "test_reopen_same_file_keeps_state_and_backs_up"),
        ("test_terminal_core", "test_open_widens_a_103_otc_contracts_table_keeping_rows_children_and_indexes")],
    "25 Application startup": [
        ("test_terminal_launch", "test_help_exits_0_from_the_repo_root_and_from_the_finsim_folder"),
        ("test_terminal_launch", "test_offline_serve_from_the_repo_root_ready_second_instance_and_shutdown"),
        ("test_terminal_launch", "test_a_database_in_the_1031_schema_starts_migrated_synced_backed_up_and_ok"),
        ("test_terminal_server", "test_duplicate_server_exit_3")],
}
#: section 18's last sentence: "Verify the actual interface in a browser. Do not claim completion based only on backend tests."
BROWSER_CHECK = [("test_terminal_browser_smoke", "test_every_main_route_renders_without_errors"),
                 ("test_terminal_browser_smoke", "test_pending_filters_list_the_seeded_orders"),
                 ("test_terminal_browser_smoke", "test_completed_trades_drawer_opens_with_every_tab"),
                 # P8R1-04 / P8R1-01 (fix round 2): the blotters fit 1440 px with the ticket dock closed and open (plan rule 5), and the
                 # browser never asks for a favicon the server does not have (0 console errors over the whole session)
                 ("test_terminal_browser_smoke", "test_the_blotters_fit_1440_with_the_ticket_dock_closed_and_open"),
                 ("test_terminal_browser_smoke", "test_the_browser_never_asks_for_a_favicon")]


def _find(module: str, method: str):
    """(TestCase class, function) of ``tests.<module>``'s test method, or (None, None)."""
    mod = importlib.import_module(f"tests.{module}")
    for c in vars(mod).values():
        if inspect.isclass(c) and issubclass(c, unittest.TestCase) and c.__module__ == mod.__name__ and callable(getattr(c, method, None)):
            return c, getattr(c, method)
    return None, None


def _has_test(module: str, method: str) -> bool:
    return _find(module, method)[0] is not None


def _skip_reason(module: str, method: str):
    """Why the test would be skipped on this machine (a skip decorator on the class or the method), else None."""
    cls, fn = _find(module, method)
    for obj in (cls, fn):
        if obj is not None and getattr(obj, "__unittest_skip__", False):
            return getattr(obj, "__unittest_skip_why__", "skipped")
    return None


class DirectiveMapTests(unittest.TestCase):
    def test_every_section_18_area_names_existing_tests(self):
        self.assertEqual(25, len(DIR_MAP))
        self.assertEqual([str(i) for i in range(1, 26)], [k.split(" ", 1)[0] for k in DIR_MAP])
        for area, refs in DIR_MAP.items():
            self.assertTrue(refs, f"{area}: no test mapped")
        missing = [(a, m, t) for a, refs in DIR_MAP.items() for m, t in refs if not _has_test(m, t)]
        self.assertEqual([], missing)
        self.assertEqual([], [(m, t) for m, t in BROWSER_CHECK if not _has_test(m, t)])

    def test_every_area_has_a_test_that_runs_here(self):
        """Each area keeps at least one mapped test that is not skipped on this machine (the browser smoke test skips only where no
        browser exists, so area 7 keeps its API tests too)."""
        bare = [a for a, refs in DIR_MAP.items() if all(_skip_reason(m, t) for m, t in refs)]
        self.assertEqual([], bare)


class _Outcomes(unittest.TestResult):
    """Each test's outcome by (module, method): PASS / FAIL / SKIP."""

    def __init__(self):
        super().__init__()
        self.by = {}

    @staticmethod
    def _key(test):
        return (type(test).__module__.split(".")[-1], getattr(test, "_testMethodName", str(test)))

    def addSuccess(self, test):
        super().addSuccess(test)
        self.by.setdefault(self._key(test), "PASS")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.by[self._key(test)] = "FAIL"

    def addError(self, test, err):
        super().addError(test, err)
        if hasattr(test, "_testMethodName"):
            self.by[self._key(test)] = "FAIL"

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self.by[self._key(test)] = "FAIL"

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.by[self._key(test)] = "SKIP"


def run_map(stream=sys.stdout) -> bool:
    """Run every mapped test once (ONE suite in module order, so each module's setUpModule / setUpClass fixtures run once, as in the
    suite), then print one line per area: how many of its tests passed, failed and were skipped."""
    pairs = sorted({p for refs in DIR_MAP.values() for p in refs} | set(BROWSER_CHECK))
    suite = unittest.TestSuite()
    for module, method in pairs:
        cls, _fn = _find(module, method)
        suite.addTest(cls(method))
    res = _Outcomes()
    suite.run(res)
    outcome = {p: res.by.get(p, "FAIL") for p in pairs}
    ok = True
    for area, refs in list(DIR_MAP.items()) + [("browser check", BROWSER_CHECK)]:
        got = [outcome[p] for p in refs]
        fails = [f"{m}.{t}" for (m, t), o in zip(refs, got) if o == "FAIL"]
        ok = ok and not fails and "PASS" in got
        print(f"{area}: {got.count('PASS')} passed, {len(fails)} failed, {got.count('SKIP')} skipped of {len(refs)}"
              + (f" - FAILED {', '.join(fails)}" if fails else ""), file=stream, flush=True)
    print(f"DIRECTIVE MAP {'PASS' if ok else 'FAIL'}: {len(pairs)} distinct tests, {sum(1 for o in outcome.values() if o == 'PASS')} passed",
          file=stream, flush=True)
    return ok


if __name__ == "__main__":
    if "--run" in sys.argv:
        sys.exit(0 if run_map() else 1)
    unittest.main()
