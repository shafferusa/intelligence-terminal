"""FINSIM1 P4 Part B (C14, OMS-10; acceptance 3): the OTC quote request (RFQ) and the simulated confirmation.

The PB OTC desks ask for a quote (``otc.rfq.request``); the simulated dealer is the RFQ_DEALER lifecycle job, registered from
``swaps.bind`` through ``lifecycle_eod.add_product_job``: on the next scheduler tick it answers with the model quote labelled
"indicative model quote, simulated dealer" (QUOTED, good for 120 s) and it expires a quote nobody accepted.  ``otc.rfq.accept`` books
within the validity at the quoted terms; ``otc.rfq.decline`` closes the request.  A contract booked now starts PENDING_CONFIRM and the
OTC_CONFIRM job confirms it at 17:05 ET of its business day ("simulated confirmation").  The matcher is never started (there is none in
these runtimes): the dealer is a lifecycle job.  Both jobs are quiet when idle and start forward (15.9 item 8).

Hand-worked figures (the swap tests' §19.15 fixture: T1:PB_GS with 100,000.00 settled cash, a flat 4.00 % curve as the design's discount
factors, the GS half-width 1 bp): the IRS pay fixed 10,000,000.00 2y is quoted at par 0.04000 + 0.00010 = 0.04010; mark0 = 10,000,000 x
(0.04000 - 0.04010) x 1.90386435 = -1,903.86; DV01 1,903.86; IM 1,903.86 x 50 = 95,193.00 from the account's own cash (no sweep).  On the
day-2 curve (par 0.04100, annuity 1.901555575) an accepted quote still books at 0.04010 - the dealer honours its quote within the
validity - and its mark0 is 10,000,000 x (0.04100 - 0.04010) x 1.901555575 = 17,114.00.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import types
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import T1, assert_integrity, bal, deposit, execute, fund, make_runtime, pb
from finsim.terminal import lifecycle, lifecycle_eod, prime
from finsim.terminal.clock import epoch_of, ny_of
from finsim.terminal.products import swaps
from finsim.terminal.quotes.rates import CurveStore, RateStore

D = Decimal
PB_GS = pb("GS")
TUE = date(2026, 10, 20)
DAY1_DFS = {"0.5": "0.98039216", "1.0": "0.96116878", "1.5": "0.94232233", "2.0": "0.92384543"}
DAY2_DFS = {"0.5": "0.97991181", "1.0": "0.96022716", "1.5": "0.94093744", "2.0": "0.92203474"}
TERMS = {"notional": "10000000.00", "currency": "USD", "pay_fixed": True, "tenor_months": 24, "fixed_rate": None, "fixed_basis": "30/360", "fixed_months": 6,
         "float_index": "SOFR", "float_basis": "ACT/360", "float_months": 6, "fixing": "TERM"}


def at(hhmmss: str) -> int:
    """Tue 2026-10-20 at hh:mm:ss ET as an epoch."""
    hh, mm, ss = (int(x) for x in hhmmss.split(":"))
    return epoch_of(TUE, f"{hh:02d}:{mm:02d}") + ss


def runtime(tmp):
    """make_runtime + prime + swaps BOUND (the rfqs table, the RFQ_DEALER / OTC_CONFIRM jobs) + the scheduler built after the bind; SOFR
    3.87 % observed 2026-10-19; the day-1 curve.  No matcher anywhere."""
    rt = make_runtime(tmp, at="2026-10-20T14:00:00-04:00")
    prime.register(rt.bus)
    prime.bind(rt)
    rt.rates = RateStore()
    rt.rates.put("SOFR", [(date(2026, 10, 19), D("3.87"))], persist=False)
    rt.curve = CurveStore()
    rt.curve.set_fixed_dfs(DAY1_DFS, date(2026, 10, 19))
    swaps.register(rt.bus, rt)
    rt.swaps = swaps
    swaps.bind(rt)
    rt.providers = types.SimpleNamespace(yahoo=rt.fp)
    lifecycle.register(rt.bus, rt)
    rt.lifecycle = lifecycle.LifecycleScheduler(rt.bus, rt.db, rt, rt.clock, rt.log)
    return rt


def q(rt, sql, params=()):
    with rt.db.reader() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(rt, sql, params=()):
    rows = q(rt, sql, params)
    return rows[0] if rows else None


def rfq(rt, rid="RFQ-000001"):
    return one(rt, "SELECT * FROM rfqs WHERE rfq_id=?", (rid,))


def keys(rt, fn, *extra):
    with rt.db.reader() as conn:
        return fn(conn, rt.clock.epoch(), *extra)


class RfqTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-rfq-")
        self.rt = runtime(self.tmp)
        rt = self.rt
        self.assertTrue(deposit(rt, 1510000000).ok)                  # T1 25,100,000.00: past the PB / OTC threshold
        r = execute(rt, "account.open_pb", {"lifetime_id": T1, "broker": "GS"})
        self.assertTrue(r.ok, r.body)
        self.assertTrue(fund(rt, PB_GS, 10000000).ok)                # 100,000.00 settled in T1:PB_GS

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def request(self, rid="ui:rfq-0001", terms=None):
        r = execute(self.rt, "otc.rfq.request", {"account_id": PB_GS, "kind": "IRS", "terms": dict(terms or TERMS)}, rid)
        self.assertTrue(r.ok, r.body)
        return r.body

    def tick(self, hhmmss, job="RFQ_DEALER"):
        return self.rt.lifecycle.tick(at=at(hhmmss), only=job)

    def test_jobs_are_registered_from_bind_and_quiet_when_idle(self):
        rt = self.rt
        names = [j.name for j in lifecycle_eod.REGISTRY]
        self.assertIn("RFQ_DEALER", names)
        self.assertIn("OTC_CONFIRM", names)
        self.assertIn("RFQ_DEALER", lifecycle_eod.PRODUCT_JOBS)
        # no request, no pending contract: no due key, so the import cutoff's due-keys hash never moves with their ticks (15.9 item 8)
        self.assertEqual([], keys(rt, swaps.rfq_keys))
        self.assertEqual([], keys(rt, swaps.confirm_keys, rt.cal))
        out = rt.lifecycle.tick(at=at("17:10:00"))
        self.assertEqual([], [i for i in out["ran"] if i["job"] in ("RFQ_DEALER", "OTC_CONFIRM")])
        self.assertEqual(0, len(q(rt, "SELECT 1 FROM jobs_done WHERE job IN ('RFQ_DEALER','OTC_CONFIRM')")))

    def test_request_quote_accept_books_at_the_quote_and_confirms_at_1705(self):
        rt = self.rt
        b = self.request()
        self.assertEqual(("RFQ-000001", "REQUESTED"), (b["rfq_id"], b["status"]))
        self.assertEqual(("AWAITING_DEALER_QUOTE", "PAY FIXED", 1000000000, False), (b["rfq"]["stage"], b["rfq"]["side"], b["rfq"]["notional_c"],
                                                                                    b["rfq"]["actions"]["accept"]))
        self.assertEqual([("RFQ-000001", "QUOTE")], keys(rt, swaps.rfq_keys))
        self.assertEqual([("RFQ-000001", "REQUESTED")], [(r["rfq_id"], r["status"]) for r in swaps.rfq_rows(rt, f"{T1}:ALL")])
        # nothing is booked by a request: no contract, no IM
        self.assertEqual((0, 0), (len(q(rt, "SELECT 1 FROM otc_contracts")), bal(rt, PB_GS, "1330")))
        # the dealer job answers on the next tick (no matcher): the model quote 0.04010, good for 120 s from 14:00:30
        out = self.tick("14:00:30")
        self.assertEqual([("RFQ-000001:QUOTE", "DONE")], [(i["key"], i["status"]) for i in out["ran"]])
        r = rfq(rt)
        quote = json.loads(r["quote_json"])
        self.assertEqual(("QUOTED", at("14:00:30"), at("14:02:30"), 120), (r["status"], r["quoted_ts"], r["expires_ts"], r["ttl_s"]))
        self.assertEqual(({"fixed_rate": "0.04010"}, "indicative model quote, simulated dealer"), (quote["firm"], quote["label"]))
        self.assertEqual("pay fixed 4.0100 % (model par 4.0000 % + 1 bp half-width)", quote["words"])
        self.assertTrue(r["quote_label"].startswith("pay fixed 4.0100 % (model par 4.0000 % + 1 bp half-width) - indicative model quote, simulated dealer"))
        self.assertEqual((-190386, 9519300), (quote["preview"]["mark0_c"], quote["preview"]["im_c"]))
        self.assertEqual([], keys(rt, swaps.rfq_keys))                          # a valid quote waits for the owner: no key until it lapses
        v = swaps.rfq_rows(rt, f"{T1}:ALL")[0]                                   # the clock is the tick's 14:00:30: 120 s left
        self.assertEqual(("AWAITING_ACCEPTANCE", True, True, 120), (v["stage"], v["actions"]["accept"], v["actions"]["decline"], v["valid_s"]))
        # accept within the validity: booked at the quoted 0.04010 through otc.book - IM 95,193.00 from own cash, the contract PENDING_CONFIRM
        rt.clock.set(at("14:01:00"))
        a = execute(rt, "otc.rfq.accept", {"rfq_id": "RFQ-000001"}, "ui:rfq-accept-0001")
        self.assertTrue(a.ok, a.body)
        cid = a.body["contract_id"]
        row = one(rt, "SELECT * FROM otc_contracts WHERE contract_id=?", (cid,))
        terms = json.loads(row["terms_json"])
        self.assertEqual(("SWP-000001", "0.04010", -190386, 9519300, "PENDING_CONFIRM", at("14:01:00"), "2026-10-20"),
                         (cid, terms["fixed_rate"], row["mark_c"], row["im_c"], row["confirm_status"], row["entered_ts"], row["trade_date"]))
        self.assertEqual((9519300, 10000000 - 9519300), (bal(rt, PB_GS, "1330", inst=cid), bal(rt, PB_GS, "1010")))
        self.assertEqual(("ACCEPTED", cid, at("14:01:00")), (rfq(rt)["status"], rfq(rt)["contract_id"], rfq(rt)["closed_ts"]))
        self.assertEqual(swaps.PENDING_CONFIRM_LABEL, a.body["contract"]["confirm_label"])
        self.assertEqual([], swaps.rfq_rows(rt, f"{T1}:ALL"))                    # nothing open any more
        # a replay of the same accept is the stored answer; a new accept is refused - the quote is consumed
        again = execute(rt, "otc.rfq.accept", {"rfq_id": "RFQ-000001"}, "ui:rfq-accept-0001")
        self.assertTrue(again.replayed)
        r2 = execute(rt, "otc.rfq.accept", {"rfq_id": "RFQ-000001"}, "ui:rfq-accept-0002")
        self.assertEqual((409, "RFQ_NOT_QUOTED"), (r2.http, r2.code))
        self.assertEqual(1, len(q(rt, "SELECT 1 FROM otc_contracts")))
        # the simulated confirmation: not before 17:05 ET of the business day, then CONFIRMED once
        self.assertEqual([], keys(rt, swaps.confirm_keys, rt.cal))
        rt.clock.set(at("17:05:00"))
        self.assertEqual([(cid, "2026-10-20")], keys(rt, swaps.confirm_keys, rt.cal))
        out = self.tick("17:06:00", job="OTC_CONFIRM")
        self.assertEqual([(f"{cid}:2026-10-20", "DONE")], [(i["key"], i["status"]) for i in out["ran"]])
        row = one(rt, "SELECT confirm_status, confirmed_ts FROM otc_contracts WHERE contract_id=?", (cid,))
        self.assertEqual(("CONFIRMED", at("17:06:00")), (row["confirm_status"], row["confirmed_ts"]))
        self.assertEqual([], keys(rt, swaps.confirm_keys, rt.cal))
        view = swaps.contract_view(rt, cid)
        self.assertEqual(("CONFIRMED", "CONFIRMED - simulated confirmation"), (view["confirm_status"], view["confirm_label"]))
        # the contract timeline: requested -> quoted -> accepted -> booked -> IM posted -> the first SOFR fixing (recorded at booking) -> confirmed
        tl = swaps.contract_timeline(rt, cid)
        self.assertEqual(["REQUESTED", "QUOTED", "ACCEPTED", "BOOKED", "IM", "FIX", "CONFIRMED"], [s["step"] for s in tl])
        self.assertEqual([False] * 7, [s["asof"] for s in tl])        # a dealt contract: every step at its clock time
        assert_integrity(self, rt)

    def test_display_only_differences_of_1_0_6(self):
        """15.9 item 7: what 1.0.6 changes on contracts booked before it is display text only.  The model notes carry no internal code
        (V104R2-BW-02 b); a contract booked live by 1.0.5 (no confirmation record) says so and the 17:05 job never confirms it (new jobs
        start forward); the IRS preview discloses that its par curve is the Treasury curve used as a SOFR proxy (V104R2-OTC-06)."""
        from finsim.terminal.products import swaps_trs
        rt = self.rt
        self.assertNotIn("SWAP_MARK", swaps.MODEL + swaps_trs.TRS_MODEL + swaps_trs.XCCY_MODEL)
        pv = swaps.preview(rt, PB_GS, "IRS", dict(TERMS))
        self.assertEqual(swaps.IRS_CURVE_NOTE, pv["curve_note"])
        self.assertIn("Treasury par curve (treasury.gov) used as a proxy for the SOFR swap curve", pv["curve_note"])
        r = execute(rt, "otc.book", {"account_id": PB_GS, "kind": "IRS", "terms": dict(TERMS)}, "ui:legacy-book-0001")
        self.assertTrue(r.ok, r.body)
        cid = r.body["contract_id"]
        with rt.db.writer() as tx:                 # what a 1.0.5 live booking left: no confirmation record
            tx.conn.execute("UPDATE otc_contracts SET confirm_status=NULL, confirmed_ts=NULL WHERE contract_id=?", (cid,))
        v = swaps.contract_view(rt, cid)
        self.assertEqual((None, None, "no confirmation record: booked before confirmations were simulated"),
                         (v["confirm_status"], v["confirm_label"], v["confirm_note"]))
        texts = [v["mark"]["note"], v["mark"]["inputs"]["model"], v["terms"]["model"]]      # the words on screen ("conventions" are note keys)
        self.assertEqual([False] * 3, ["SWAP_MARK" in t for t in texts], texts)
        self.assertEqual([], keys(rt, swaps.confirm_keys, rt.cal))
        out = rt.lifecycle.tick(at=at("17:06:00"), only="OTC_CONFIRM")
        self.assertEqual([], out["ran"])
        self.assertIsNone(one(rt, "SELECT confirm_status FROM otc_contracts WHERE contract_id=?", (cid,))["confirm_status"])

    def test_a_quote_dealt_after_the_cut_off_reads_in_order_without_false_instants(self):
        """P8R1-03: the contract timeline of a quote dealt Tue 10-20 at 21:09 ET (after the 17:00 ET cut-off: its IM is dated Wed 10-21).
        Every step recorded on its own day shows its New York date with its clock time - a real instant - and the IM says its business date
        in words; the rows read in time order.  (The old timeline dated the IM 2026-10-21 beside the clock time 21:09:00 of 10-20, an
        instant that never happened, and listed it before the 10-21 17:05 confirmation.)"""
        rt = self.rt
        rt.clock.set(at("21:08:00"))
        self.request()
        self.tick("21:08:30")                                                    # quoted 0.04010, valid until 21:10:30
        rt.clock.set(at("21:09:00"))
        a = execute(rt, "otc.rfq.accept", {"rfq_id": "RFQ-000001"}, "ui:rfq-late-accept")
        self.assertTrue(a.ok, a.body)
        cid = a.body["contract_id"]
        self.assertEqual("2026-10-21", one(rt, "SELECT business_date FROM journal_entries WHERE idem_key=?", (f"im:{cid}:open",))["business_date"])
        # Wed 10-21: the simulated confirmation at 17:06 (its business day is the booking's, 10-21) and the first VM at 17:10 (P8R1-02: 10-21,
        # not the closed 10-20) - mark -1,903.86 on the 10-19 curve
        self.assertEqual([(f"{cid}:2026-10-21", "DONE")], [(i["key"], i["status"]) for i in rt.lifecycle.tick(at=epoch_of(date(2026, 10, 21), "17:06"),
                                                                                                                   only="OTC_CONFIRM")["ran"]])
        self.assertEqual([(f"{cid}:2026-10-21", "DONE")], [(i["key"], i["status"]) for i in rt.lifecycle.tick(at=epoch_of(date(2026, 10, 21), "17:10"),
                                                                                                                   only="SWAP_VM")["ran"]])
        tl = swaps.contract_timeline(rt, cid)
        wed = epoch_of(date(2026, 10, 21), "17:06")
        self.assertEqual([("REQUESTED", "2026-10-20", at("21:08:00"), "2026-10-21", False),
                          ("QUOTED", "2026-10-20", at("21:08:30"), "2026-10-21", False),
                          ("ACCEPTED", "2026-10-20", at("21:09:00"), "2026-10-21", False),
                          ("BOOKED", "2026-10-20", at("21:09:00"), "2026-10-21", True),            # its entries carry 10-21: said in words
                          ("IM", "2026-10-20", at("21:09:00"), "2026-10-21", True),               # posted after the cut-off: said in words
                          ("FIX", "2026-10-20", at("21:09:00"), "2026-10-20", False),              # the first SOFR fixing, for the start 10-20
                          ("CONFIRMED", "2026-10-21", wed, "2026-10-22", False),
                          ("VM", "2026-10-21", wed + 240, "2026-10-21", False)],
                         [(s["step"], s["date"], s["ts"], s["business_date"], s["after_cutoff"]) for s in tl])
        self.assertEqual([False] * 8, [s["asof"] for s in tl])
        # the shape every row keeps: a date and the clock time of that same New York date (a real instant), in time order
        self.assertTrue(all(ny_of(s["ts"]).date().isoformat() == s["date"] for s in tl), [(s["step"], s["date"], s["ts"]) for s in tl])
        self.assertEqual(sorted((s["date"], s["ts"]) for s in tl), [(s["date"], s["ts"]) for s in tl])
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(swaps.__file__))), "static", "components", "swapticket.js"),
                  encoding="ascii") as fh:
            js = fh.read()
        self.assertIn("' (business date ' + r.business_date + ': posted after the 17:00 ET cut-off)'", js)
        self.assertIn("' (recorded ' + (T.fmt && T.fmt.date ? T.fmt.date(r.recorded_ts) + ' ' : '') + nyTime(r.recorded_ts) + ' ET)'", js)
        assert_integrity(self, rt)

    def test_a_quote_past_its_validity_is_refused_then_expired_by_the_job(self):
        rt = self.rt
        self.request()
        self.tick("14:00:30")                                                    # QUOTED, valid until 14:02:30
        rt.clock.set(at("14:02:30"))
        r = execute(rt, "otc.rfq.accept", {"rfq_id": "RFQ-000001"}, "ui:rfq-late-0001")
        self.assertEqual((409, "QUOTE_EXPIRED"), (r.http, r.code))
        self.assertIn("valid until 14:02:30 ET", r.body["error"]["message"] if "error" in r.body else r.message)
        # nothing written by the refusal: no contract, the request still QUOTED until the dealer job closes it
        self.assertEqual((0, "QUOTED"), (len(q(rt, "SELECT 1 FROM otc_contracts")), rfq(rt)["status"]))
        self.assertEqual([("RFQ-000001", "EXPIRE")], keys(rt, swaps.rfq_keys))
        out = self.tick("14:03:00")
        self.assertEqual([("RFQ-000001:EXPIRE", "DONE")], [(i["key"], i["status"]) for i in out["ran"]])
        row = rfq(rt)
        self.assertEqual(("EXPIRED", at("14:03:00")), (row["status"], row["closed_ts"]))
        self.assertEqual("not accepted within 120 s (valid until 14:02:30 ET)", row["reason"])
        self.assertEqual([], keys(rt, swaps.rfq_keys))
        r = execute(rt, "otc.rfq.accept", {"rfq_id": "RFQ-000001"}, "ui:rfq-late-0002")
        self.assertEqual((409, "QUOTE_EXPIRED"), (r.http, r.code))
        self.assertEqual(0, len(q(rt, "SELECT 1 FROM otc_contracts")))

    def test_decline_and_refusals(self):
        rt = self.rt
        self.request()
        r = execute(rt, "otc.rfq.accept", {"rfq_id": "RFQ-000001"}, "ui:rfq-early-0001")      # not quoted yet
        self.assertEqual((409, "RFQ_NOT_QUOTED"), (r.http, r.code))
        d = execute(rt, "otc.rfq.decline", {"rfq_id": "RFQ-000001"}, "ui:rfq-decline-0001")
        self.assertTrue(d.ok, d.body)
        self.assertEqual(("DECLINED", "declined by you"), (rfq(rt)["status"], rfq(rt)["reason"]))
        self.assertEqual([], keys(rt, swaps.rfq_keys))
        d2 = execute(rt, "otc.rfq.decline", {"rfq_id": "RFQ-000001"}, "ui:rfq-decline-0002")
        self.assertEqual((409, "RFQ_CLOSED"), (d2.http, d2.code))
        u = execute(rt, "otc.rfq.accept", {"rfq_id": "RFQ-000999"}, "ui:rfq-unknown-0001")
        self.assertEqual((404, "RFQ_UNKNOWN"), (u.http, u.code))
        # a request is checked like a booking: a better-than-quote rate is refused (OFF_MARKET), nothing stored
        bad = execute(rt, "otc.rfq.request", {"account_id": PB_GS, "kind": "IRS", "terms": dict(TERMS, fixed_rate="0.04000")}, "ui:rfq-bad-0001")
        self.assertEqual((400, "OFF_MARKET"), (bad.http, bad.code))
        self.assertEqual(1, len(q(rt, "SELECT 1 FROM rfqs")))
        cal = execute(rt, "otc.rfq.request", {"account_id": PB_GS, "kind": "IRS", "terms": dict(TERMS, calibrate_curve_spread_to_rate=True)}, "ui:rfq-bad-0002")
        self.assertEqual((400, "TERMS"), (cal.http, cal.code))

    def test_the_dealer_does_not_quote_without_its_inputs(self):
        rt = self.rt
        self.request()
        rt.curve = CurveStore()                                                    # the curve is gone before the dealer answers
        out = self.tick("14:00:30")
        self.assertEqual([("RFQ-000001:QUOTE", "DONE")], [(i["key"], i["status"]) for i in out["ran"]])
        row = rfq(rt)
        self.assertEqual("EXPIRED", row["status"])
        self.assertTrue(row["reason"].startswith("the simulated dealer could not quote: no par curve has been observed yet"), row["reason"])
        self.assertIsNone(row["quote_json"])

    def test_an_accepted_quote_is_firm_within_its_validity(self):
        """The curve moves between the quote and the acceptance (the day-2 factors): the accepted quote still books at 0.04010 - the dealer
        honours its quote within the 120 s - and the contract is marked on the curve now: mark0 = 10,000,000 x (0.04100 - 0.04010) x
        1.901555575 = 17,114.00."""
        rt = self.rt
        self.request()
        self.tick("14:00:30")
        rt.curve.set_fixed_dfs(DAY2_DFS, date(2026, 10, 20))
        rt.clock.set(at("14:01:30"))
        a = execute(rt, "otc.rfq.accept", {"rfq_id": "RFQ-000001"}, "ui:rfq-firm-0001")
        self.assertTrue(a.ok, a.body)
        row = one(rt, "SELECT * FROM otc_contracts WHERE contract_id=?", (a.body["contract_id"],))
        self.assertEqual(("0.04010", 1711400), (json.loads(row["terms_json"])["fixed_rate"], row["mark_c"]))
        # without a quote the same terms would deal at the new par + 1 bp: 0.04110 (the quote is what the owner accepted)
        self.assertEqual("0.04110", swaps.preview(rt, PB_GS, "IRS", TERMS)["dealt_rate"])

    def test_pending_blotter_lists_requested_and_quoted(self):
        """C7: P2's Pending blotter reads ``swaps.rfq_rows`` - a REQUESTED request is AWAITING DEALER QUOTE, a QUOTED one AWAITING YOUR
        ACCEPTANCE (with the quote in words), and neither after it is accepted / declined / expired."""
        rt = self.rt
        try:
            from finsim.terminal import blotters
        except ImportError:                                                        # the blotter module is P2's; without it the rows are this module's
            blotters = None
        self.request()
        self.request(rid="ui:rfq-0002", terms=dict(TERMS, notional="5000000.00"))
        self.tick("14:00:30")                                                      # both quoted
        execute(rt, "otc.rfq.decline", {"rfq_id": "RFQ-000002"}, "ui:rfq-decline-x")
        r3 = self.request(rid="ui:rfq-0003")
        self.assertEqual("RFQ-000003", r3["rfq_id"])
        rows = {r["rfq_id"]: r for r in swaps.rfq_rows(rt, f"{T1}:ALL")}
        self.assertEqual({"RFQ-000001": "QUOTED", "RFQ-000003": "REQUESTED"}, {k: v["status"] for k, v in rows.items()})
        self.assertEqual(["RFQ-000001", "RFQ-000002", "RFQ-000003"], sorted(r["rfq_id"] for r in swaps.rfq_rows(rt, f"{T1}:ALL", status="all")))
        if blotters is not None and callable(getattr(blotters, "pending_rows", None)):
            with rt.db.reader() as conn:
                got, _notes = blotters.pending_rows(rt, conn, lifetime_ids=[T1], status="open")
            st = {r["rfq_id"]: r["stage"] for r in got if r.get("row_kind") == "rfq"}
            self.assertEqual({"RFQ-000001": "AWAITING_ACCEPTANCE", "RFQ-000003": "AWAITING_DEALER_QUOTE"}, st)


if __name__ == "__main__":
    unittest.main()


class FirmQuoteTests(unittest.TestCase):
    """C14 with the owner's other kinds (the as-of tests' runtime: F1, the Goldman PB account, the FRED prints, the 10-21 .. 10-26 curves, the
    CDX basis calibrated IG -24.5 bp, Wed 2026-10-28 10:00 ET).  An accepted quote books at its quoted price whatever moved since:
    - TRS pay 100 ORCL quoted at the 09:59 print 128.00 (notional 12,800.00, IM 10 % = 1,280.00); ORCL prints 128.50 before the acceptance:
      the contract books reset 128.00 and its intraday model value is -100 x (128.50 - 128.00) = -50.00 (the short loses what the price rose);
    - CDX IG buy protection 1,000,000.00 quoted at the 10-26 proxy 0.90 % = 90 bp + basis -24.5 = mid 65.5 bp + half-spread 0.5 = 66.0 bp; the
      basis is edited to -25.5 bp before the acceptance: it still books 66.0 bp (a fresh quote would deal 65.0 bp)."""

    def setUp(self):
        from tests import test_terminal_otc_asof as asof
        self.asof = asof
        self.tmp = tempfile.mkdtemp(prefix="fst-rfq-firm-")
        self.rt = asof.runtime(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def quote(self, kind, terms, rid):
        rt = self.rt
        r = execute(rt, "otc.rfq.request", {"account_id": self.asof.PB, "kind": kind, "terms": terms}, rid)
        self.assertTrue(r.ok, r.body)
        rt.lifecycle.tick(at=rt.clock.epoch() + 30, only="RFQ_DEALER")
        row = one(rt, "SELECT * FROM rfqs WHERE rfq_id=?", (r.body["rfq_id"],))
        self.assertEqual("QUOTED", row["status"], row["reason"])
        return r.body["rfq_id"], json.loads(row["quote_json"])

    def test_trs_books_the_quoted_reset_price(self):
        rt = self.rt
        rt.marks.set("ORCL", "128.00", "delayed", epoch_of(date(2026, 10, 28), "09:59"))
        rid, q1 = self.quote("TRS", {"underlying_id": "ORCL", "units": 100, "receiver": False, "reset_months": 1, "tenor_months": 12}, "ui:rfq-trs-0001")
        self.assertEqual((D("128.00"), 1280000, 128000), (D(q1["firm"]["reset_px"]), q1["preview"]["notional_c"], q1["preview"]["im_c"]))
        rt.marks.set("ORCL", "128.50", "delayed", epoch_of(date(2026, 10, 28), "10:00") + 40)
        rt.clock.set(rt.clock.epoch() + 30)
        a = execute(rt, "otc.rfq.accept", {"rfq_id": rid}, "ui:rfq-trs-accept")
        self.assertTrue(a.ok, a.body)
        cid = a.body["contract_id"]
        row = one(rt, "SELECT reset_px, notional_c, im_c, confirm_status FROM otc_contracts WHERE contract_id=?", (cid,))
        self.assertEqual((D("128.00"), 1280000, 128000, "PENDING_CONFIRM"), (D(row["reset_px"]), row["notional_c"], row["im_c"], row["confirm_status"]))
        # NAV carries the model value at the booking at once: the overlay -50.00, measured on the 10:00:40 print
        self.assertEqual(-5000, one(rt, "SELECT mark_c FROM otc_contracts WHERE contract_id=?", (cid,))["mark_c"])
        pos = swaps.contract_view(rt, cid)["position"]
        self.assertEqual((-5000, "as of the booking (no variation margin yet)", "computed"), (pos["mv_c"], pos["mark_label"], pos["quality"]))
        assert_integrity(self, rt)

    def test_cdx_books_the_quoted_spread(self):
        rt = self.rt
        rid, q1 = self.quote("CDX", {"index": "IG", "side": "BUY_PROTECTION", "notional": "1000000.00"}, "ui:rfq-cdx-0001")
        self.assertEqual("66.0000", q1["firm"]["dealt_spread_bp"])
        self.assertTrue(q1["words"].startswith("66.0000 bp (mid 65.5000 bp); you receive USD"), q1["words"])
        r = execute(rt, "cdx.terms", {"account_id": self.asof.PB, "index": "IG", "basis_bp": "-25.5"}, "ui:cdx-basis-edit")
        self.assertTrue(r.ok, r.body)
        self.assertEqual("65.0000", swaps.preview(rt, self.asof.PB, "CDX", {"index": "IG", "side": "BUY_PROTECTION", "notional": "1000000.00"})["deal_spread_bp"])
        a = execute(rt, "otc.rfq.accept", {"rfq_id": rid}, "ui:rfq-cdx-accept")
        self.assertTrue(a.ok, a.body)
        terms = json.loads(one(rt, "SELECT terms_json FROM otc_contracts WHERE contract_id=?", (a.body["contract_id"],))["terms_json"])
        self.assertEqual("66.0000", terms["deal_spread_bp"])
        assert_integrity(self, rt)

    def test_a_repriced_kind_books_on_the_prints_it_was_quoted_from(self):
        """An OTC equity forward (re-priced at booking, no firm price of its own): the quote records the ORCL print it was priced from
        (128.00) and the acceptance books on that print, though ORCL printed 131.00 since - the strike booked is the strike quoted.  An input
        that is not a print (here the dealer's forward half-width in the PB schedule) moved since a second quote: 409 QUOTE_MOVED, nothing
        written."""
        from finsim.terminal.products import otc_options
        rt = self.rt
        rt.otc_options = otc_options
        otc_options.register(rt.bus, rt)
        rt.marks.set("ORCL", "128.00", "delayed", epoch_of(date(2026, 10, 28), "09:59"))
        terms = {"underlying_id": "ORCL", "units": 100, "side": "BUY", "tenor": "3M"}
        rid, q1 = self.quote("EQ_FORWARD", terms, "ui:rfq-fwd-0001")
        self.assertIsNone(q1["firm"])
        self.assertEqual(D("128.00"), D(q1["pinned"]["mark"]["ORCL"]["value"]))
        k_quoted = q1["compare"]["strike"]
        # OTC-23 (1.0.6 fixer): the quote spells the deal out - side, quantity, underlying, maturity, the strike and the mid forward
        self.assertTrue(q1["words"].startswith("you buy 100 ORCL forward for 2027-01-28 at " + str(k_quoted) + " (mid forward "), q1["words"])
        rt.marks.set("ORCL", "131.00", "delayed", epoch_of(date(2026, 10, 28), "10:00") + 40)
        fresh = swaps.preview(rt, self.asof.PB, "EQ_FORWARD", terms)["strike"]
        self.assertNotEqual(D(k_quoted), D(fresh))                    # a fresh quote now would strike higher
        rt.clock.set(rt.clock.epoch() + 30)
        n = (len(q(rt, "SELECT 1 FROM otc_contracts")), len(q(rt, "SELECT 1 FROM journal_entries")))
        a = execute(rt, "otc.rfq.accept", {"rfq_id": rid}, "ui:rfq-fwd-accept")
        self.assertTrue(a.ok, a.body)
        row = one(rt, "SELECT kind, terms_json, confirm_status FROM otc_contracts WHERE contract_id=?", (a.body["contract_id"],))
        self.assertEqual(("EQ_FORWARD", D(k_quoted), "PENDING_CONFIRM"), (row["kind"], D(json.loads(row["terms_json"])["strike"]), row["confirm_status"]))
        self.assertEqual("ACCEPTED", one(rt, "SELECT status FROM rfqs WHERE rfq_id=?", (rid,))["status"])
        self.assertEqual(n[0] + 1, len(q(rt, "SELECT 1 FROM otc_contracts")))
        # a second quote, then the dealer's forward half-width is changed before the acceptance: the re-priced strike differs - refused
        rid2, q2 = self.quote("EQ_FORWARD", terms, "ui:rfq-fwd-0002")
        sched = execute(rt, "account.update_schedule", {"account_id": self.asof.PB, "forward_hw_bp": "25"}, "ui:pb-fwd-hw")
        self.assertTrue(sched.ok, sched.body)
        n = (len(q(rt, "SELECT 1 FROM otc_contracts")), len(q(rt, "SELECT 1 FROM journal_entries")))
        a2 = execute(rt, "otc.rfq.accept", {"rfq_id": rid2}, "ui:rfq-fwd-accept-2")
        self.assertEqual((409, "QUOTE_MOVED"), (a2.http, a2.code), a2.body)
        self.assertEqual("strike", a2.body["error"]["details"]["differences"][0]["term"])
        self.assertEqual(n, (len(q(rt, "SELECT 1 FROM otc_contracts")), len(q(rt, "SELECT 1 FROM journal_entries"))))
        self.assertEqual("QUOTED", one(rt, "SELECT status FROM rfqs WHERE rfq_id=?", (rid2,))["status"])
        assert_integrity(self, rt)


class _AppliedImport(unittest.TestCase):
    """A copy that holds an APPLIED import: the mini batch of the import tests (``tests/fixtures/terminal/import``) dry-run and applied by
    ``importer.apply`` on Tue 2026-10-27 10:00 ET in a seeded home (built once per class), cloned for each test and opened by the real
    ``server.build_runtime`` at 11:00 ET."""

    @classmethod
    def setUpClass(cls):
        import os
        from tests import test_terminal_import as ti
        from finsim.terminal import importer as im
        from finsim.terminal.clock import FakeClock
        cls.ti, cls.im, cls.os = ti, im, os
        cls.root = tempfile.mkdtemp(prefix="fst-rfq-import-")
        seed_home = os.path.join(cls.root, "seed")
        ti.seed(seed_home)
        cls.home = os.path.join(cls.root, "home")
        ti.clone(seed_home, cls.home)
        pack = ti.write_pack_for(os.path.join(cls.root, "pack.json"))
        clock = FakeClock(ti.APPLY_AT)
        with ti.quiet():
            d = im.dry_run(ti.MINI_BATCH, cls.home, pack, work_dir=os.path.join(cls.root, "dry1"), clock=clock)
        assert d["status"] == "DONE", d.get("refused")
        with ti.quiet():
            a = im.apply(ti.MINI_BATCH, cls.home, pack, "test: the owner's go", dryrun_dir=os.path.join(cls.root, "dry1"), clock=clock)
        assert a["status"] == "DONE", a.get("refused")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-rfq-imported-")
        h = self.os.path.join(self.tmp, "home")
        self.ti.clone(self.home, h)
        self.rt = self.ti.build(h, "2026-10-27T11:00:00-04:00")

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class ImportedCopyTests(_AppliedImport):
    """15.9 item 8 (new jobs start forward; RFQ_DEALER is quiet when idle), on the applied-import copy:
    - idle, neither job has a due key, so the import's catch-up cutoff never moves with their ticks; a whole lifecycle tick quotes nothing and
      confirms nothing, and the imported contracts (as-of, CONFIRMED at the owner's instruction) keep their confirmation untouched;
    - a quote requested after the import books a contract that the 17:05 ET job confirms ("simulated confirmation"), still without touching them;
    - a simulated credit event on the imported CDX IG 1,000,000.00 buy protection (dealt 64 bp, upfront received 16,164.19, IM 2 % = 20,000.00):
      one of the 125 names on 10-27 at 40 % -> Dn = 1,000,000 / 125 = 8,000.00; protection 8,000 x 0.60 = 4,800.00; accrued premium on Dn
      09-21 .. 10-27 = 10 + 27 = 37 days: 8,000 x 1 % x 37 / 360 = 8.2222 -> 8.22; net 4,791.78 settling Wed 10-28; 2345 released 16,164.19 x
      8,000 / 1,000,000 = 129.31352 -> 129.31; IM 2 % x 992,000 = 19,840.00 (160.00 back to cash); the premium through night 10-27 on
      1,000,000 = to_c(1,000,000 x 1 % x 37 / 360) = 1,027.78, less 8.22 = 1,019.56 = to_c(992,000 x 1 % x 37 / 360) exactly."""

    def imported(self):
        return q(self.rt, "SELECT contract_id, booking, confirm_status, confirmed_ts, notional_c FROM otc_contracts WHERE booking='AS_OF' ORDER BY contract_id")

    def integrity_ok(self):
        """Integrity full ok; offline the copy's listed options have no Cboe mark, so NAV is partial and M1 says it skipped (a WARN, not mine)."""
        report = self.rt.accounting.integrity("full")
        self.assertTrue(report.ok, [p.to_dict() for p in report.problems])
        self.assertEqual([], [p.to_dict() for p in report.problems if not (p.id == "M1" and "NAV is partial" in (p.detail or ""))])

    def cmd(self, kind, payload, rid, actor="ui"):
        from finsim.terminal.commands import Command
        return self.rt.bus.execute(Command(kind, payload, rid, actor))

    def test_quiet_when_idle_and_the_imported_contracts_are_never_touched(self):
        rt = self.rt
        before = self.imported()
        self.assertEqual(3, len(before))
        self.assertTrue(all(r["confirm_status"] == "CONFIRMED" for r in before))
        self.assertEqual([], keys(rt, swaps.rfq_keys))
        self.assertEqual([], keys(rt, swaps.confirm_keys, rt.cal))
        self.assertEqual([], [k for k in self.im.due_keys(rt) if k.split(":", 1)[0] in ("RFQ_DEALER", "OTC_CONFIRM")])
        rt.lifecycle.tick(at=epoch_of(date(2026, 10, 27), "17:10"))
        self.assertEqual(0, len(q(rt, "SELECT 1 FROM jobs_done WHERE job IN ('RFQ_DEALER','OTC_CONFIRM')")))
        self.assertEqual(before, self.imported())

    def test_a_quote_after_the_import_books_and_is_confirmed_at_1705(self):
        rt = self.rt
        before = self.imported()
        terms = {"notional": "1000000.00", "pay_fixed": True, "tenor_months": 36, "fixed_months": 6, "fixed_basis": "ACT/360", "float_index": "SOFR",
                 "float_basis": "ACT/360", "float_months": 12, "fixing": "COMPOUND_ARREARS"}
        r = self.cmd("otc.rfq.request", {"account_id": "F1:PB_GS", "kind": "IRS", "terms": terms}, "ui:imp-rfq-0001")
        self.assertTrue(r.ok, r.body)
        rt.lifecycle.tick(at=epoch_of(date(2026, 10, 27), "11:00") + 30, only="RFQ_DEALER")
        self.assertEqual("QUOTED", one(rt, "SELECT status FROM rfqs")["status"])
        a = self.cmd("otc.rfq.accept", {"rfq_id": r.body["rfq_id"]}, "ui:imp-rfq-accept")
        self.assertTrue(a.ok, a.body)
        cid = a.body["contract_id"]
        self.assertEqual("PENDING_CONFIRM", one(rt, "SELECT confirm_status FROM otc_contracts WHERE contract_id=?", (cid,))["confirm_status"])
        self.assertEqual([], keys(rt, swaps.confirm_keys, rt.cal))                 # 11:00: before 17:05 ET
        rt.clock.set(epoch_of(date(2026, 10, 27), "17:05"))
        self.assertEqual([(cid, "2026-10-27")], keys(rt, swaps.confirm_keys, rt.cal))
        rt.lifecycle.tick(at=epoch_of(date(2026, 10, 27), "17:06"), only="OTC_CONFIRM")
        self.assertEqual("CONFIRMED", one(rt, "SELECT confirm_status FROM otc_contracts WHERE contract_id=?", (cid,))["confirm_status"])
        self.assertEqual(before, self.imported())
        self.integrity_ok()

    def test_a_credit_event_on_the_imported_cdx(self):
        rt = self.rt
        cid = one(rt, "SELECT contract_id FROM otc_contracts WHERE kind='CDX' AND booking='AS_OF'")["contract_id"]
        self.assertEqual((1616419, 2000000), (bal(rt, "F1:PB_GS", "2345", inst=cid), one(rt, "SELECT im_c FROM otc_contracts WHERE contract_id=?", (cid,))["im_c"]))
        r = self.cmd("cdx.credit_event", {"contract_id": cid, "reference_entity": "Gamma Industries", "recovery_pct": "40", "event_date": "2026-10-27"},
                     "ui:imp-credit-0001")
        self.assertTrue(r.ok, r.body)
        b = r.body
        self.assertEqual((800000, 480000, 822, 37, 479178, "2026-10-28", 99200000, 12931, 16000),
                         (b["defaulted_c"], b["protection_c"], b["accrued_c"], b["accrued_days"], b["net_c"], b["settle_date"], b["notional_after_c"],
                          b["cost_released_c"], b["im_released_c"]))
        acc = one(rt, "SELECT base_c, cum_c, accrued_through FROM accrual_state WHERE kind='swappay' AND ref_id=?", (cid,))
        self.assertEqual((99200000, 101956, "2026-10-27"), (acc["base_c"], acc["cum_c"], acc["accrued_through"]))
        self.assertEqual(101956, bal(rt, "F1:PB_GS", "2340", inst=cid))
        self.integrity_ok()


class RouteTests(_AppliedImport):
    """The routes of 1.0.6's OTC pieces on the same applied-import copy (the real runtime's Router): the Derivatives page (UI-11), the quote
    requests, the contract timeline and the credit event; a refused OTC preview answers 200 with its blocker (V104R2-BW-05), never a 4xx."""

    def h(self, method, path, query=None, body=None):
        return self.rt.router.handle(method, path, query, body, "127.0.0.1", None)

    def test_derivatives_tabs(self):
        st, body, _ = self.h("GET", "/api/derivatives", {"tab": "otc", "book": "F"})
        self.assertEqual(200, st, body)
        self.assertEqual({"TRS", "CDX", "IRS"}, {c["kind"] for c in body["contracts"]})
        self.assertTrue(all(c["confirm_status"] == "CONFIRMED" and c["asof"] for c in body["contracts"]))
        self.assertTrue(body["credit_event_offered"])
        self.assertEqual([], body["rfqs"])
        for tab in ("options", "futures", "servicing"):
            st, body, _ = self.h("GET", "/api/derivatives", {"tab": tab, "book": "F"})
            self.assertEqual((200, tab), (st, body.get("tab")), body)
        # the servicing tab: the CDX IG coupon of 2026-12-21 on 1,000,000 x 1 % x 91 / 360 (09-21 .. 12-21) = 2,527.777 -> 2,527.78, paid (buyer)
        cpn = [r for r in body["rows"] if r["event"] == "PAY" and r["kind"] == "OTC" and r["date"] == "2026-12-21" and "CDX" in r["what"]]
        self.assertEqual(["-2527.78"], [r["amount"] for r in cpn])
        st, body, _ = self.h("GET", "/api/derivatives", {"tab": "nope", "book": "F"})
        self.assertEqual((400, "TAB"), (st, body["error"]["code"]))

    def test_refused_previews_answer_200_with_the_reason(self):
        irs = {"notional": "1000000.00", "pay_fixed": True, "tenor_months": 36, "fixed_rate": "0.01000"}
        st, body, _ = self.h("POST", "/api/otc/preview", None, {"account_id": "F1:PB_GS", "kind": "IRS", "terms": irs})
        self.assertEqual((200, True, "OFF_MARKET", False), (st, body.get("refused"), body.get("code"), body.get("bookable")), body)
        self.assertEqual("OFF_MARKET", body["blockers"][0]["code"])
        st, body, _ = self.h("POST", "/api/otc/cdx/preview", None, {"account_id": "F1:PB_GS", "terms": {"index": "IG", "side": "BUY_PROTECTION", "notional": "5"}})
        self.assertEqual((200, True, "TERMS"), (st, body.get("refused"), body.get("code")), body)
        st, body, _ = self.h("POST", "/api/otc/preview", None, {"account_id": "F1:NOPE", "kind": "IRS", "terms": irs})
        self.assertEqual(404, st)                                                   # an unknown account is still not found

    def test_quote_request_routes_and_timeline(self):
        terms = {"notional": "1000000.00", "pay_fixed": True, "tenor_months": 36, "fixed_months": 6, "fixed_basis": "ACT/360", "float_index": "SOFR",
                 "float_basis": "ACT/360", "float_months": 12, "fixing": "COMPOUND_ARREARS"}
        st, body, _ = self.h("POST", "/api/otc/rfqs", None, {"request_id": "ui:route-rfq-0001", "account_id": "F1:PB_GS", "kind": "IRS", "terms": terms})
        self.assertEqual((200, "REQUESTED"), (st, body.get("status")), body)
        rid = body["rfq_id"]
        st, rows, _ = self.h("GET", "/api/otc/rfqs", {"book": "F"})
        self.assertEqual((200, [rid]), (st, [r["rfq_id"] for r in rows]))
        st, one_, _ = self.h("GET", f"/api/otc/rfqs/{rid}")
        self.assertEqual((200, None, "AWAITING_DEALER_QUOTE"), (st, one_["quote_preview"], one_["stage"]))
        st, body, _ = self.h("POST", f"/api/otc/rfqs/{rid}/accept", None, {"request_id": "ui:route-rfq-accept"})
        self.assertEqual((409, "RFQ_NOT_QUOTED"), (st, body["error"]["code"]))
        st, body, _ = self.h("POST", f"/api/otc/rfqs/{rid}/decline", None, {"request_id": "ui:route-rfq-decline"})
        self.assertEqual((200, "DECLINED"), (st, body["status"]))
        self.assertEqual(404, self.h("GET", "/api/otc/rfqs/RFQ-999999")[0])
        cid = one(self.rt, "SELECT contract_id FROM otc_contracts WHERE kind='TRS'")["contract_id"]
        st, body, _ = self.h("GET", f"/api/otc/contracts/{cid}/timeline")
        self.assertEqual(200, st)
        self.assertEqual(["BOOKED", "IM", "CONFIRMED"], [x["step"] for x in body["timeline"]][:3])
        # the as-of booking's ts is its as-of dealt instant (it orders the steps); the page shows "as of" there, never a clock time.  P8R1-03:
        # its IM and confirmation were recorded at the import, on a later day than the value date they belong to, so they are "as of" that
        # date too, with the time they were recorded (``recorded_ts``) - never the import's clock time beside the value date
        tl = body["timeline"]
        self.assertEqual([True, True, True], [x["asof"] for x in tl][:3])
        self.assertEqual([None], list({x["recorded_ts"] for x in tl[:1]}))
        self.assertTrue(all(ny_of(x["recorded_ts"]).date().isoformat() > x["date"] for x in tl[1:3]), tl[:3])
        self.assertTrue(all(x["asof"] or ny_of(x["ts"]).date().isoformat() == x["date"] for x in tl if x["ts"] is not None), tl)
        self.assertEqual(404, self.h("GET", "/api/otc/contracts/SWP-999999/timeline")[0])
        st, body, _ = self.h("POST", f"/api/otc/contracts/{cid}/credit-event", None, {"request_id": "ui:route-credit-0001", "reference_entity": "X", "recovery_pct": "40",
                                                                                     "event_date": "2026-10-27"})
        self.assertEqual((400, "KIND"), (st, body["error"]["code"]))                # a TRS has no credit event
