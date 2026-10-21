"""WP-15 integration tests: the cross-package interface requests the integrator applied (TERMINAL_WORKPLAN.md WP-15;
docs/design_proposals/BUILD_NOTES.md "WP-15 integration").  Each test pins one request with hand-worked numbers; nothing is
pasted from the code's output.  No network: FakeClock, FixedMarks, fixed discount factors, temp databases.

- IRS OFF_MARKET (WP-17 observation): a client fixed rate better than the PB quote is refused; the quote and a worse rate deal.
- TRS / XCCY variation margin valued AS OF its own date (the underlying's / the pair's CLOSE of that date, recorded or observed
  at the close) and CAUGHT_UP when the only price is dated after it (swaps.h_vm with ``valuation_date``).
- The VM reference re-based by a RESET row ordered by command, even when the reset posted no journal entry.
- contract_view: TRS / XCCY side and specifics (swaps_trs.contract_detail) and the per-currency balances of a contract.
- The instruments OTC contracts observe without holding them (TRS underlyings, XCCY FX pairs): recorded by MARKS_RECORD, pinned
  at HELD by the quote worker, and their dividends fetched.
- HOUSEKEEPING runs the private master sync (private.reseed) when the private module is bound.

The §19.15 / §19.16 fixtures come from the package tests (tests.test_terminal_swaps / tests.test_terminal_swaps_trs).
"""
from __future__ import annotations

import json
import shutil
import tempfile
import types
import unittest
from datetime import date
from decimal import Decimal

from tests import test_terminal_swaps as sw
from tests import test_terminal_swaps_trs as st
from tests.terminal_helpers import T1, assert_integrity, bal, deposit, execute, fund
from finsim.terminal.clock import epoch_of
from finsim.terminal.commands import RejectError
from finsim.terminal.instruments import otc_reference_ids
from finsim.terminal.lifecycle_eod import recorded_set
from finsim.terminal.products import swaps, swaps_trs
from finsim.terminal.quotes.worker import QuoteWorker, RatesRefresher

D = Decimal


def record_close(rt, iid: str, ds: str, price: str, hhmm: str) -> None:
    """What MARKS_RECORD writes for a close back-filled from daily bars: kind CLOSE, method daily_close, quote_time = the close."""
    rows = [{"instrument_id": iid, "mark_date": ds, "kind": "CLOSE", "price": price, "currency": "USD", "quality": "close",
             "source": "Yahoo daily bars (observed close)", "method": "daily_close", "quote_time": epoch_of(date.fromisoformat(ds), hhmm), "note": "recorded late"}]
    r = execute(rt, "lifecycle.marks_record", {"rows": rows}, f"test:integ:marks:{iid}:{ds}", actor="lifecycle")
    assert r.ok, r.body


class IrsOffMarketTests(unittest.TestCase):
    """§19.15 deal in T1:PB_GS (a 15,100,000.00 deposit takes T1 to 25,100,000.00, past the 25,000,000.00 PB/OTC threshold)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-integ-irs-")
        self.rt = sw.runtime(self.tmp)
        rt = self.rt
        self.assertTrue(deposit(rt, 1510000000).ok)
        self.assertTrue(execute(rt, "account.open_pb", {"lifetime_id": T1, "broker": "GS"}).ok)
        self.assertTrue(fund(rt, sw.PB_GS, 10000000).ok)                                  # 100,000.00 settled in T1:PB_GS

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_rate_better_than_the_quote_is_refused_and_a_worse_one_deals(self):
        rt = self.rt
        # §19.15 curve: par 0.04000; GS half-width 1.0 bp x otc_spread_mult 1.0 = 0.00010 -> pay-fixed quote 0.04010, receive-fixed quote 0.03990.
        # Paying 0.04009 (< 0.04010) or receiving 0.03991 (> 0.03990) would be a day-one gain no dealer gives: 400 OFF_MARKET with the quote
        for terms, quote in ((dict(sw.TERMS, fixed_rate="0.04009"), "0.04010"), (dict(sw.TERMS, pay_fixed=False, fixed_rate="0.03991"), "0.03990")):
            with self.assertRaises(RejectError) as cm:
                swaps.preview(rt, sw.PB_GS, "IRS", terms)
            self.assertEqual(("OFF_MARKET", quote, "0.04000"), (cm.exception.code, cm.exception.details["quote"], cm.exception.details["par_rate"]))
        # at the quote: dealt as quoted; mark0 = -10,000,000 x 0.00010 x 1.90386435 = -1,903.86435 -> -1,903.86 on either side
        p = swaps.preview(rt, sw.PB_GS, "IRS", dict(sw.TERMS, fixed_rate="0.04010"))
        self.assertEqual(("0.04010", -190386, True), (p["dealt_rate"], p["mark0_c"], p["fixed_rate_given"]))
        p = swaps.preview(rt, sw.PB_GS, "IRS", dict(sw.TERMS, pay_fixed=False, fixed_rate="0.03990"))
        self.assertEqual(("0.03990", -190386), (p["dealt_rate"], p["mark0_c"]))
        # a rate WORSE for the client deals as given: receive 3.900 % -> mark0 = -1 x 10,000,000 x (0.04000 - 0.03900) x 1.90386435 = -19,038.6435 -> -19,038.64
        p = swaps.preview(rt, sw.PB_GS, "IRS", dict(sw.TERMS, pay_fixed=False, fixed_rate="0.039"))
        self.assertEqual(("0.03900", -1903864), (p["dealt_rate"], p["mark0_c"]))
        # the booking refuses the same way and writes nothing: no contract, no IM, the 100,000.00 untouched
        r = execute(rt, "otc.book", {"account_id": sw.PB_GS, "kind": "IRS", "terms": dict(sw.TERMS, fixed_rate="0.04009")}, "ui:integ-irs-off-0001")
        self.assertEqual((400, "OFF_MARKET"), (r.http, r.code))
        self.assertEqual(([], 10000000, 0), (sw.q(rt, "SELECT 1 FROM otc_contracts"), bal(rt, sw.PB_GS, "1010"), bal(rt, sw.PB_GS, "1330")))
        assert_integrity(self, rt, "full")


class TrsXccyIntegrationTests(unittest.TestCase):
    """§19.16 deals in F1:PB_GS (books="real": the Fund Book starts at the 25,000,000.00 PB/OTC threshold)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-integ-trs-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def xccy(self):
        rt = self.rt = st.runtime(self.tmp, st.XCCY_AT)                                     # Wed 2026-05-20 14:00 ET
        rt.curve.set_fixed_dfs(st.USD401, date(2026, 5, 19))
        rt.rates.put("ECBDFR", [(date(2026, 5, 19), D("2.49"))], persist=False)
        r = st.book(rt, dict(st.XCCY_TERMS), "ui:integ-xccy-0001", kind="XCCY")             # receive USD 11,238,000.00 / pay EUR 10,000,000.00 at 1.1238
        self.assertTrue(r.ok, r.body)
        return rt

    # ---- SWAP_VM of a TRS: the close of its own date, else CAUGHT_UP -----------------------------------------------------------------
    def test_trs_vm_values_at_the_close_of_its_own_date_or_catches_up(self):
        rt = self.rt = st.runtime(self.tmp, st.TRS_AT)                                      # Mon 2026-11-02 14:00 ET; AAPL 339.50 delayed
        self.assertTrue(st.book(rt).ok)                                                       # receive the total return on 10,000 AAPL @ 339.50
        # Wed 11-11 10:00: the only AAPL print is today's 343.00 (09:58) and nothing is recorded for Mon 11-09, so the VM of 11-09 cannot be
        # valued at 11-09's close: CAUGHT_UP (quiet, terminal) - nothing posted, never at another day's price
        rt.clock.set("2026-11-11T10:00:00-05:00")
        rt.marks.set("AAPL", "343.00", "delayed", epoch_of(date(2026, 11, 11), "09:58"))
        r = st.vm(rt, "2026-11-09")
        self.assertEqual(("SKIPPED", "CAUGHT_UP", False), (r.body["status"], r.body["code"], r.body["posted"]))
        jd = st.one(rt, "SELECT status, note FROM jobs_done WHERE job='SWAP_VM' AND key=?", (f"{st.SWP1}:2026-11-09",))
        self.assertEqual(("SKIPPED", True), (jd["status"], jd["note"].startswith("CAUGHT_UP")))
        self.assertEqual(([], []), (st.q(rt, "SELECT 1 FROM otc_vm"), st.q(rt, "SELECT 1 FROM attention WHERE ref_id=? AND resolved_ts IS NULL", (f"{st.SWP1}:2026-11-09",))))
        with rt.db.reader() as conn:
            self.assertNotIn((st.SWP1, "2026-11-09"), swaps.vm_keys(conn, rt.clock.epoch()))         # terminal: never retried
        # MARKS_RECORD back-fills Tue 11-10's close 341.00 from the daily bars; the VM of 11-10 is valued at THAT close, not at today's 343.00:
        # mark 10,000 x (341.00 - 339.50) = 15,000.00, reference 0 -> 15,000.00 received (Dr 1010 / Cr 2330); today's print would give 35,000.00
        record_close(rt, "AAPL", "2026-11-10", "341.00", "16:00")
        r = st.vm(rt, "2026-11-10")
        self.assertEqual(("DONE", 1500000, 1500000), (r.body["status"], r.body["mark_c"], r.body["delta_c"]))
        self.assertEqual(("recorded CLOSE (marks)", "341", "2026-11-10"), (r.body["inputs"]["underlying"]["basis"], r.body["inputs"]["S"], r.body["inputs"]["valuation_date"]))
        self.assertEqual([("1010", "USD", None, 1500000), ("2330", "USD", st.SWP1, -1500000)], st.lines_by_idem(rt, f"swapvm:{st.SWP1}:2026-11-10"))
        # Wed 17:10: the 16:00 print 343.00 is 11-11's close -> mark 10,000 x (343.00 - 339.50) = 35,000.00 - reference 15,000.00 = 20,000.00 received
        rt.clock.set("2026-11-11T17:10:00-05:00")
        rt.marks.set("AAPL", "343.00", "delayed", epoch_of(date(2026, 11, 11), "16:00"))
        r = st.vm(rt, "2026-11-11")
        self.assertEqual((3500000, 1500000, 2000000), (r.body["mark_c"], r.body["reference_c"], r.body["delta_c"]))
        self.assertEqual((3500000, 3500000), (st.contract(rt)["vm_balance_c"], bal(rt, st.PB, "2330", inst=st.SWP1)))
        assert_integrity(self, rt, "full")

    # ---- SWAP_VM of an XCCY: the pair's recorded 17:00 close of its date, else CAUGHT_UP ----------------------------------------------
    def test_xccy_vm_uses_the_recorded_fx_close_of_its_date(self):
        rt = self.xccy()
        rt.clock.set("2026-05-20T17:10:00-04:00")
        self.assertEqual(-431865, st.vm(rt, "2026-05-20").body["mark_c"])                    # day 1 at the deal rate 1.1238: the §19.16 mark0 -4,318.65
        # Thu 05-21: the EURUSD 17:00 close 1.1300 is recorded; the live print is still Wednesday's 1.1238.  The mark at 1.1300 (4 periods left):
        # USD leg +11,238,000 x (0.04000 - 0.04010) x 1.903633365 = -1,123.8 x 1.903633365 = -2,139.303175587;
        # EUR leg -10,000,000 x (0.02500 - 0.02490) x 1.9392669142 x 1.1300 = -1,939.2669142 x 1.13 = -2,191.371613046;
        # sum -4,330.674788633 -> -4,330.67; delta -4,330.67 - (-4,318.65) = -12.02 posted (Dr 1320 / Cr 1010)
        record_close(rt, "EURUSD", "2026-05-21", "1.1300", "17:00")
        rt.clock.set("2026-05-21T17:10:00-04:00")
        r = st.vm(rt, "2026-05-21")
        self.assertEqual(("DONE", -433067, -1202, "1.13"), (r.body["status"], r.body["mark_c"], r.body["delta_c"], r.body["inputs"]["fx"]["usd_per_unit"]))
        self.assertEqual([("1320", "USD", st.SWP1, 1202), ("1010", "USD", None, -1202)], st.lines_by_idem(rt, f"swapvm:{st.SWP1}:2026-05-21"))
        # Mon 05-25 10:00: a live EURUSD print of 05-25 and no close recorded for Fri 05-22 -> the VM of 05-22 is CAUGHT_UP, nothing posted
        rt.clock.set("2026-05-25T10:00:00-04:00")
        rt.marks.set("EURUSD", "1.1350", "delayed", epoch_of(date(2026, 5, 25), "09:58"))
        r = st.vm(rt, "2026-05-22")
        self.assertEqual(("SKIPPED", "CAUGHT_UP"), (r.body["status"], r.body["code"]))
        self.assertIsNone(st.entry_by_idem(rt, f"swapvm:{st.SWP1}:2026-05-22"))
        assert_integrity(self, rt, "full")

    # ---- the VM reference: a RESET re-bases it by command order, with or without a journal entry --------------------------------------
    def test_vm_reference_rebased_by_a_reset_that_posted_no_entry(self):
        rt = self.rt = st.runtime(self.tmp, st.TRS_AT)
        self.assertTrue(st.book(rt).ok)
        st.set_close(rt, "AAPL", "341.00", "2026-11-10")
        rt.clock.set("2026-11-10T17:10:00-05:00")
        self.assertEqual(1500000, st.vm(rt, "2026-11-10").body["mark_c"])                     # 10,000 x (341.00 - 339.50) = 15,000.00
        with rt.db.reader() as conn:
            vm_cmd = int(conn.execute("SELECT j.command_id FROM otc_vm v JOIN journal_entries j ON j.entry_id=v.entry_id").fetchone()[0])
            self.assertEqual(1500000, swaps._reference_mark_c(conn, st.SWP1))
        # a RESET row that posted no entry (STM, zero financing, no dividend) but ran after the VM's command re-bases the reference to 0
        with rt.db.writer() as tx:
            tx.conn.execute("INSERT INTO otc_resets(contract_id, event_date, kind, amount_c, detail_json, entry_id) VALUES (?,?,'RESET',0,?,NULL)",
                            (st.SWP1, "2026-12-02", json.dumps({"post_mark_c": 0, "command_id": vm_cmd + 1})))
        with rt.db.reader() as conn:
            self.assertEqual((0, 0), (swaps._reference_mark_c(conn, st.SWP1), swaps_trs._vm_reference(conn, st.SWP1)))     # one definition
        # the same row ordered BEFORE the VM's command does not re-base it
        with rt.db.writer() as tx:
            tx.conn.execute("UPDATE otc_resets SET detail_json=? WHERE contract_id=? AND kind='RESET'", (json.dumps({"post_mark_c": 0, "command_id": vm_cmd - 1}), st.SWP1))
        with rt.db.reader() as conn:
            self.assertEqual(1500000, swaps._reference_mark_c(conn, st.SWP1))

    # ---- the contracts view: TRS / XCCY side, specifics and per-currency balances --------------------------------------------------------
    def test_contract_view_shows_the_trs_specifics(self):
        rt = self.rt = st.runtime(self.tmp, st.TRS_AT)
        self.assertTrue(st.book(rt).ok)
        v = swaps.contract_view(rt, st.SWP1)
        # a receiver: never read as an IRS PAY_FIXED / RECEIVE_FIXED; IM 10 % x 10,000 x 339.50 = 339,500.00 in 1330
        self.assertEqual(("TRS", "RECEIVE_TOTAL_RETURN", None, None), (v["kind"], v["side"], v["pay_fixed"], v["fixed_rate"]))
        self.assertEqual(("AAPL", 10000, "339.5", 339500000), (v["detail"]["underlying_id"], v["detail"]["units"], v["detail"]["reset_px"], v["detail"]["notional_c"]))
        self.assertEqual({"USD": {"im_c": 33950000}}, v["balances_by_ccy"])

    def test_contract_view_shows_the_xccy_legs_per_currency(self):
        rt = self.xccy()
        rt.clock.set("2026-05-21T00:40:00-04:00")
        for kind in ("swaprcv", "swappay"):
            self.assertTrue(st.accrue(rt, kind, "2026-05-20").ok)
        v = swaps.contract_view(rt, st.SWP1)
        self.assertEqual(("XCCY", "RECEIVE_USD_PAY_EUR"), (v["kind"], v["side"]))
        # USD: receive leg night 1 11,238,000 x 0.04 / 360 = 1,248.6667 -> 1,248.67 (1225); the lent notional 11,238,000.00 (1360); IM 2 % x 11,238,000 = 224,760.00
        # EUR: pay leg night 1 10,000,000 x 0.025 / 360 = 694.4444 -> 694.44 (2340); the borrowed notional 10,000,000.00 (2360) - in EUR, not in row.currency
        self.assertEqual({"USD": {"accrued_receive_c": 124867, "notional_lent_c": 1123800000, "im_c": 22476000},
                          "EUR": {"accrued_pay_c": 69444, "notional_borrowed_c": 1000000000}}, v["balances_by_ccy"])
        legs = v["detail"]["legs"]
        self.assertEqual((("USD", 124867, 1123800000), ("EUR", 69444, 1000000000)),
                         ((legs["receive"]["currency"], legs["receive"]["accrued_c"], legs["receive"]["notional_balance_c"]),
                          (legs["pay"]["currency"], legs["pay"]["accrued_c"], legs["pay"]["notional_balance_c"])))

    # ---- what OTC contracts observe without holding it: recorded, pinned, its dividends fetched ----------------------------------------
    def test_trs_underlying_and_xccy_pair_are_recorded_and_pinned(self):
        rt = self.rt = st.runtime(self.tmp, st.XCCY_AT)                                     # Wed 2026-05-20 14:00 ET, F1 NAV 25,000,000.00
        rt.curve.set_fixed_dfs(st.USD401, date(2026, 5, 19))
        rt.rates.put("ECBDFR", [(date(2026, 5, 19), D("2.49"))], persist=False)
        # a TRS on AAPL (held nowhere; its mark 0 keeps NAV at the threshold), then the cross-currency swap on EUR in the same PB account
        self.assertTrue(st.book(rt, request_id="ui:integ-trs-0001").ok)
        self.assertTrue(st.book(rt, dict(st.XCCY_TERMS), "ui:integ-xccy-0002", kind="XCCY").ok)
        with rt.db.reader() as conn:
            self.assertEqual({"AAPL": [st.PB], "EURUSD": [st.PB]}, otc_reference_ids(conn))
            rec = recorded_set(conn, rt.instruments)
        self.assertTrue({"AAPL", "EURUSD", "SPY"} <= set(rec), sorted(rec))                 # SPY: the performance benchmark, always
        # MARKS_RECORD wants Wednesday's closes once their record time has passed (US 16:20, FX 17:05)
        rt.clock.set("2026-05-21T10:00:00-04:00")
        with rt.db.reader() as conn:
            due = rt.lifecycle.job("MARKS_RECORD").due(conn, rt.clock, rt.cal, rt.sessions)
        self.assertTrue({"AAPL:2026-05-20", "EURUSD:2026-05-20"} <= set(due), due)
        # the quote worker's 30-second scan pins both at HELD priority (their marks drive SWAP_VM, the NAV overlay and the resets) ...
        pins = {}

        class Cache:
            def pin(self, ids, prio):
                pins.setdefault(prio, set()).update(ids)

            def unpin(self, ids, prio):
                pins.setdefault(prio, set()).difference_update(ids)

        chains = types.SimpleNamespace(set_pins=lambda unders: None)
        worker = types.SimpleNamespace(db=rt.db, cache=Cache(), instruments=rt.instruments, _scanned={}, chains=chains, log=None)
        QuoteWorker._scan_pins(worker, rt.clock.epoch())
        self.assertTrue({"AAPL", "EURUSD"} <= pins.get("HELD", set()), pins)
        # ... and the dividends slot asks Yahoo for the TRS underlying's events (a receiver is owed them), naming the account
        self.assertEqual([st.PB], RatesRefresher.held_names(worker)["AAPL"])
        # nothing once the contracts are gone
        for cid in (st.SWP1, "SWP-000002"):
            with rt.db.writer() as tx:
                tx.conn.execute("UPDATE otc_contracts SET status='TERMINATED' WHERE contract_id=?", (cid,))
        with rt.db.reader() as conn:
            self.assertEqual({}, otc_reference_ids(conn))


class HousekeepingPrivateSyncTests(unittest.TestCase):
    """HOUSEKEEPING runs services.private.reseed (the master sync: listings, forward status moves) when the private module is bound."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-integ-hk-")
        self.rt = sw.runtime(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_housekeeping_calls_private_reseed(self):
        rt = self.rt
        calls = []

        def reseed(tx, services):
            calls.append((tx.conn is not None, services is rt))
            return 2                                                                            # "two new master rows"
        rt.private = types.SimpleNamespace(reseed=reseed)
        out = rt.lifecycle.tick(at="2026-10-21T00:20:00-04:00", only="HOUSEKEEPING")
        self.assertEqual([("2026-10-21", "DONE")], [(i["key"], i["status"]) for i in out["ran"]])
        self.assertEqual([(True, True)], calls)
        note = sw.one(rt, "SELECT note FROM jobs_done WHERE job='HOUSEKEEPING' AND key='2026-10-21'")["note"]
        self.assertIn("private rows 2", note)


if __name__ == "__main__":
    unittest.main()
