"""FINSIM1 P6 (phase 1, Part A) settlement fail / resolve tests (C15, OMS-09, TEST-03; acceptance 1 (a) and (f)).

Directive section 3: Executed -> Pending Settlement -> Settled, or Executed -> Settlement Failed -> Resolved.
- an unfunded payable that misses its due date becomes FAILED (its ERROR attention stays, it keeps retrying) and RESOLVED when
  later funding settles it - also for an OTC payment row (``ref_type`` otc);
- the owner's simulated counterparty fail (off by default): nothing moves while FAILED; retry / cash compensation / buy-in;
- an as-of settlement (booking AS_OF: the historical import) posts on its due date, its funding sweep too.

Every expected number is worked by hand in a comment.  No network: FakeClock, FixedMarks, a temp database; the lifecycle runs
through ``tick()``."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest

from tests.terminal_helpers import T1, TREASURY, US_EXCHANGE, assert_integrity, bal, buy, deposit, execute, fund
from tests import test_terminal_collateral as tcol
from tests import test_terminal_lifecycle as lc
from tests import test_terminal_orders as sc
from finsim.terminal import collateral, lifecycle

TUE_10 = "2026-10-20T10:00:00-04:00"


def runtime(tmp, at=TUE_10):
    rt = tcol.runtime(tmp, at=at)
    # the booking columns the as-of bookings carry (P2 C8, P4 C10): the identical statements, idempotent
    rt.db.ensure_schema_ext("test", ["ALTER TABLE fills ADD COLUMN booking TEXT", "ALTER TABLE otc_contracts ADD COLUMN booking TEXT"])
    return rt


def aapl(rt):
    now = rt.clock.epoch()
    rt.marks.set_quote(sc.rec("AAPL", "339.50", now - 120, now, bar=(now - 180, 120000)))


def srow(rt, sid):
    return sc.one(rt, "SELECT * FROM settlements WHERE settle_id=?", (sid,))


def settle_id_of(rt, fill_id):
    return sc.one(rt, "SELECT settle_id FROM settlements WHERE fill_id=?", (fill_id,))["settle_id"]


def ok(tc, r):
    tc.assertTrue(r.ok, r.body)
    return r.body


def open_items(rt, code, ref=None):
    sql, p = "SELECT * FROM attention WHERE code=? AND resolved_ts IS NULL", [code]
    if ref:
        sql += " AND ref_id=?"
        p.append(ref)
    return sc.q(rt, sql, p)


class SettlementFailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-sfail-")
        self.rt = runtime(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _buy(self, qty):
        r = sc.submit(self.rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="BUY", qty=qty, order_type="MARKET")
        self.assertEqual("FILLED", r.body["order"]["status"], r.body)
        return settle_id_of(self.rt, r.body["fills"][0]["fill_id"])

    # ---- the owner's simulated counterparty fail ----------------------------------------------------------------------------------
    def test_a_simulated_fail_moves_no_cash_then_retry_resolves(self):
        rt = self.rt
        ok(self, fund(rt, US_EXCHANGE, 50000000))
        aapl(rt)
        # Tue 10:00 BUY 100 AAPL at 339.50: gross 33,950.00 + fee 1.00 = payable 33,951.00 due Wed 10-21 (nothing moves at the fill)
        sid = self._buy(100)
        self.assertEqual((-3395100, "2026-10-21", "PENDING"), (srow(rt, sid)["amount_c"], srow(rt, sid)["due_date"], srow(rt, sid)["status"]))
        r = execute(rt, "settlement.fail", {"settle_id": sid, "reason": "the seller did not deliver"})
        body = ok(self, r)
        s = srow(rt, sid)
        self.assertEqual(("FAILED", "SIMULATED", "simulated counterparty fail: the seller did not deliver", "PENDING"),
                         (s["fail_state"], s["fail_kind"], s["fail_reason"], s["status"]))
        self.assertEqual("FAILED", body["state"]["stage"])
        self.assertIn("no cash moves until resolved", body["state"]["label"])
        self.assertEqual(1, len(open_items(rt, "SETTLEMENT_FAILED", sid)))
        self.assertEqual((409, "ALREADY_FAILED"), (execute(rt, "settlement.fail", {"settle_id": sid}).http, execute(rt, "settlement.fail", {"settle_id": sid}).code))
        # Wed 00:35: SETTLE_DUE skips the failed row - nothing moves: settled cash 500,000.00, the payable 33,951.00 still open
        out = lc.tick(rt, "2026-10-21T00:35:00-04:00", only="SETTLE_DUE")
        self.assertEqual([], out["ran"])
        self.assertEqual((50000000, 3395100, "PENDING"), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "2100"), srow(rt, sid)["status"]))
        assert_integrity(self, rt, "full")
        # retry: back to normal settlement; the next run settles it (Dr 2100 / Cr 1010 33,951.00) and it is RESOLVED (RETRY)
        body = ok(self, execute(rt, "settlement.resolve", {"settle_id": sid, "action": "retry"}))
        self.assertIn("retry requested", body["state"]["label"])
        out = lc.tick(rt, only="SETTLE_DUE")
        self.assertEqual([(sid, "DONE")], [(i["key"], i["status"]) for i in out["ran"]])
        s = srow(rt, sid)
        self.assertEqual(("SETTLED", "RESOLVED", "RETRY"), (s["status"], s["fail_state"], s["resolution"]))
        self.assertEqual((rt.clock.epoch(), rt.clock.epoch()), (s["settled_ts"], s["resolved_ts"]))
        self.assertEqual((50000000 - 3395100, 0), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "2100")))
        self.assertEqual([], open_items(rt, "SETTLEMENT_FAILED", sid))
        self.assertEqual(("RESOLVED", "resolved 2026-10-21: settled on retry"), (collateral.settlement_state(s)["stage"], collateral.settlement_state(s)["label"]))
        self.assertEqual(["SETTLEMENT_FAILED", "SETTLEMENT_RETRY", "SETTLEMENT_RESOLVED"],
                         [e["type"] for e in sc.q(rt, "SELECT type FROM events WHERE ref_type='settlement' AND ref_id=? AND type LIKE 'SETTLEMENT_%' ORDER BY id", (sid,))])
        self.assertEqual((409, "NOT_FAILED"), (execute(rt, "settlement.resolve", {"settle_id": sid, "action": "retry"}).http,
                                               execute(rt, "settlement.resolve", {"settle_id": sid, "action": "retry"}).code))
        self.assertEqual((409, "NOT_PENDING"), (execute(rt, "settlement.fail", {"settle_id": sid}).http, execute(rt, "settlement.fail", {"settle_id": sid}).code))
        assert_integrity(self, rt, "full")

    def test_a_cash_compensation_and_buy_in_postings(self):
        rt = self.rt
        ok(self, fund(rt, US_EXCHANGE, 50000000))
        aapl(rt)
        s1 = self._buy(100)            # payable 100 x 339.50 + 1.00 = 33,951.00
        s2 = self._buy(20)             # payable 20 x 339.50 + 1.00 = 6,791.00
        r = sc.submit(rt, account_id=US_EXCHANGE, instrument_id="AAPL", side="SELL", qty=50, order_type="MARKET")
        self.assertEqual("FILLED", r.body["order"]["status"], r.body)
        s3 = settle_id_of(rt, r.body["fills"][0]["fill_id"])        # receivable 50 x 339.50 - 1.00 = 16,974.00
        self.assertEqual((-3395100, -679100, 1697400), (srow(rt, s1)["amount_c"], srow(rt, s2)["amount_c"], srow(rt, s3)["amount_c"]))
        for sid in (s1, s2, s3):
            ok(self, execute(rt, "settlement.fail", {"settle_id": sid}))
        nav0 = rt.accounting.nav(US_EXCHANGE).nav_c
        cash0 = bal(rt, US_EXCHANGE, "1010")
        self.assertEqual(50000000, cash0)
        # s1 cash compensation 12.34: the payable settles (Dr 2100 / Cr 1010 33,951.00) and the counterparty pays 12.34 for the delay (Dr 1010 / Cr 4300)
        body = ok(self, execute(rt, "settlement.resolve", {"settle_id": s1, "action": "cash_compensation", "amount": "12.34"}))
        self.assertEqual(("CASH_COMPENSATION", 1234), (body["resolution"], body["amount_c"]))
        self.assertEqual([("2100", 3395100, None), ("1010", -3395100, None)], tcol.sh.lines_of(rt, f"settle:{s1}"))
        self.assertEqual([("1010", 1234, None), ("4300", -1234, "AAPL")], tcol.sh.lines_of(rt, f"settlefail:comp:{s1}"))
        # s2 buy-in at a cost of 25.00 above the original price: the payable settles (6,791.00) and the cost posts separately (Dr 5700 / Cr 1010)
        body = ok(self, execute(rt, "settlement.resolve", {"settle_id": s2, "action": "buy_in", "cost": "25.00"}))
        self.assertEqual("BUY_IN", body["resolution"])
        self.assertEqual([("2100", 679100, None), ("1010", -679100, None)], tcol.sh.lines_of(rt, f"settle:{s2}"))
        self.assertEqual([("5700", 2500, "AAPL"), ("1010", -2500, None)], tcol.sh.lines_of(rt, f"settlefail:buyin:{s2}"))
        # s3 is a receipt: a buy-in does not apply; cash compensation 5.00: Dr 1010 / Cr 1200 16,974.00 + Dr 1010 / Cr 4300 5.00
        r = execute(rt, "settlement.resolve", {"settle_id": s3, "action": "buy_in", "cost": "1.00"})
        self.assertEqual((409, "BUY_IN_NOT_APPLICABLE"), (r.http, r.code))
        ok(self, execute(rt, "settlement.resolve", {"settle_id": s3, "action": "cash_compensation", "amount_c": 500}))
        self.assertEqual([("1010", 1697400, None), ("1200", -1697400, None)], tcol.sh.lines_of(rt, f"settle:{s3}"))
        # cash: 500,000.00 - 33,951.00 + 12.34 - 6,791.00 - 25.00 + 16,974.00 + 5.00 = 476,224.34; NAV moves only by the P&L of the fails:
        # +12.34 - 25.00 + 5.00 = -7.66 (settling cash legs is NAV-neutral)
        self.assertEqual(47622434, bal(rt, US_EXCHANGE, "1010"))
        self.assertEqual(nav0 - 766, rt.accounting.nav(US_EXCHANGE).nav_c)
        self.assertEqual([("RESOLVED", "CASH_COMPENSATION"), ("RESOLVED", "BUY_IN"), ("RESOLVED", "CASH_COMPENSATION")],
                         [(srow(rt, s)["fail_state"], srow(rt, s)["resolution"]) for s in (s1, s2, s3)])
        self.assertEqual([], open_items(rt, "SETTLEMENT_FAILED"))
        r = execute(rt, "settlement.resolve", {"settle_id": s1, "action": "cash_compensation", "amount": "1.00"})
        self.assertEqual((409, "NOT_FAILED"), (r.http, r.code))
        assert_integrity(self, rt, "full")

    def test_fail_refusals(self):
        rt = self.rt
        r = execute(rt, "settlement.fail", {"settle_id": "S-999999"})
        self.assertEqual((400, "NOT_FOUND"), (r.http, r.code))
        # an FX deal settles both legs together: its legs cannot be failed by hand
        fx = ok(self, execute(rt, "test.cash_settlement", {"account_id": US_EXCHANGE, "amount_c": -100000, "gl": "2350", "due_date": "2026-10-30",
                                                             "fx_deal_id": "D-000001"}))
        r = execute(rt, "settlement.fail", {"settle_id": fx["settle_id"]})
        self.assertEqual((409, "FX_DEAL_LEG"), (r.http, r.code))
        r = execute(rt, "settlement.resolve", {"settle_id": fx["settle_id"], "action": "forgive"})
        self.assertEqual((400, "ACTION"), (r.http, r.code))
        assert_integrity(self, rt, "full")

    # ---- TEST-03: an unfunded payable that misses its due date --------------------------------------------------------------------
    def _drain(self):
        """US Exchange's own cash charged away and Treasury drawn down to 100.00 (the unfunded set-up of the lifecycle test)."""
        rt = self.rt
        cash = bal(rt, US_EXCHANGE, "1010")
        if cash:
            ok(self, execute(rt, "test.post", {"kind": "FEE", "memo": "charge away", "idem_key": f"test:charge:{rt.clock.epoch()}",
                                               "lines": [[US_EXCHANGE, "5000", "USD", cash, "AAPL"], [US_EXCHANGE, "1010", "USD", -cash]]}))
        ok(self, execute(rt, "treasury.withdraw", {"lifetime_id": T1, "amount_c": bal(rt, TREASURY, "1010") - 10000, "currency": "USD"}))
        self.assertEqual((0, 10000), (bal(rt, US_EXCHANGE, "1010"), bal(rt, TREASURY, "1010")))

    def test_test03_unfunded_payable_fails_after_its_due_date_then_resolves(self):
        rt = self.rt
        ok(self, fund(rt, US_EXCHANGE, 3395100))
        aapl(rt)
        sid = self._buy(100)                                       # payable 33,951.00 due Wed 10-21, own cash covers it today
        self._drain()
        # Wed 00:35 (accounting day 10-21 = the due date): unfunded -> SKIPPED with its ERROR item; not failed yet (the day is not over)
        out = lc.tick(rt, "2026-10-21T00:35:00-04:00", only="SETTLE_DUE")
        self.assertEqual([(sid, "SKIPPED", "SETTLEMENT_UNFUNDED")], [(i["key"], i["status"], i["code"]) for i in out["ran"]])
        self.assertEqual((None, "PENDING"), (srow(rt, sid)["fail_state"], srow(rt, sid)["status"]))
        self.assertEqual(1, len(open_items(rt, "SETTLEMENT_UNFUNDED", sid)))
        # Wed 17:05: the 17:00 ET cut-off of the due date has passed (accounting day 10-22) and it is still unfunded -> FAILED, still retried
        rt.clock.set("2026-10-21T17:05:00-04:00")
        out = lc.tick(rt, only="SETTLE_DUE")
        self.assertEqual([(sid, "SKIPPED")], [(i["key"], i["status"]) for i in out["ran"]])
        s = srow(rt, sid)
        self.assertEqual(("FAILED", "UNFUNDED", "PENDING", rt.clock.epoch()), (s["fail_state"], s["fail_kind"], s["status"], s["failed_ts"]))
        self.assertTrue(s["fail_reason"].startswith("not funded on its due date (2026-10-21): "), s["fail_reason"])
        self.assertEqual(1, len(open_items(rt, "SETTLEMENT_UNFUNDED", sid)))                      # the same ERROR item, kept
        self.assertEqual(("FAILED", "failed - not funded on its due date (2026-10-21); retried automatically until it is funded"),
                         (collateral.settlement_state(s)["stage"], collateral.settlement_state(s)["label"]))
        self.assertIn("marked failed", lc.jobs_done(rt, "SETTLE_DUE")[0]["note"])
        # the counterparty's remedies do not apply to the fund's own fail; the state is no integrity failure (C9g)
        r = execute(rt, "settlement.resolve", {"settle_id": sid, "action": "cash_compensation", "amount": "1.00"})
        self.assertEqual((409, "NOT_A_COUNTERPARTY_FAIL"), (r.http, r.code))
        assert_integrity(self, rt, "full")
        # Thu 10:00 a deposit of 40,000.00 (Treasury 40,100.00); the next retry sweeps exactly 33,951.00 linked to the settlement and settles it,
        # dated the day it settles (10-22): RESOLVED (SETTLED), the ERROR item resolved
        rt.clock.set("2026-10-22T10:00:00-04:00")
        ok(self, deposit(rt, 4000000))
        rt.clock.advance(lifecycle.SKIP_RETRY_S + 1)
        out = lc.tick(rt, only="SETTLE_DUE")
        self.assertEqual([(sid, "DONE")], [(i["key"], i["status"]) for i in out["ran"]])
        s = srow(rt, sid)
        self.assertEqual(("SETTLED", "RESOLVED", "SETTLED", s["settled_ts"]), (s["status"], s["fail_state"], s["resolution"], s["resolved_ts"]))
        sweep = sc.one(rt, "SELECT amount_c, linked_type, linked_id FROM transfers WHERE kind='SWEEP_IN' AND linked_type='settlement'")
        self.assertEqual((3395100, "settlement", sid), (sweep["amount_c"], sweep["linked_type"], sweep["linked_id"]))
        self.assertEqual("2026-10-22", sc.one(rt, "SELECT business_date FROM journal_entries WHERE idem_key=?", (f"settle:{sid}",))["business_date"])
        self.assertEqual(([], 4010000 - 3395100), (open_items(rt, "SETTLEMENT_UNFUNDED", sid), bal(rt, TREASURY, "1010")))
        self.assertEqual("resolved 2026-10-22: settled after the fail", collateral.settlement_state(s)["label"])
        assert_integrity(self, rt, "full")

    def test_test03_an_otc_payment_follows_the_same_path(self):
        rt = self.rt
        # an OTC payment row (ref_type otc: the shape of a CDX initial payment) of 51,075.90 owed, due Wed 10-21, nothing to pay it with
        x = ok(self, execute(rt, "test.cash_settlement", {"account_id": US_EXCHANGE, "amount_c": -5107590, "due_date": "2026-10-21", "ref_type": "otc",
                                                            "ref_id": "CDX-T001"}))
        sid = x["settle_id"]
        self._drain()
        lc.tick(rt, "2026-10-21T00:35:00-04:00", only="SETTLE_DUE")
        self.assertIsNone(srow(rt, sid)["fail_state"])
        rt.clock.set("2026-10-21T17:05:00-04:00")
        lc.tick(rt, only="SETTLE_DUE")
        self.assertEqual(("FAILED", "UNFUNDED", "otc", "CDX-T001"), (srow(rt, sid)["fail_state"], srow(rt, sid)["fail_kind"], srow(rt, sid)["ref_type"],
                                                                    srow(rt, sid)["ref_id"]))
        rt.clock.set("2026-10-22T10:00:00-04:00")
        ok(self, deposit(rt, 6000000))
        rt.clock.advance(lifecycle.SKIP_RETRY_S + 1)
        lc.tick(rt, only="SETTLE_DUE")
        s = srow(rt, sid)
        self.assertEqual(("SETTLED", "RESOLVED", "SETTLED"), (s["status"], s["fail_state"], s["resolution"]))
        self.assertEqual(0, bal(rt, US_EXCHANGE, "2100"))
        assert_integrity(self, rt, "full")

    # ---- C15: an as-of settlement posts on its due date --------------------------------------------------------------------------
    def test_f_asof_settlement_posts_on_its_due_date(self):
        rt = self.rt
        aapl(rt)
        # Tue 10-20: two fills of 100 AAPL at 339.50 + fee 1.00 (payables 33,951.00 due Wed 10-21) in a US Exchange with no cash; the first carries
        # booking AS_OF (an as-of listed fill of the import), the second is an ordinary fill
        f1 = ok(self, buy(rt, US_EXCHANGE, "AAPL", 100, "339.50"))
        f2 = ok(self, buy(rt, US_EXCHANGE, "AAPL", 100, "339.50"))
        with rt.db.writer() as tx:
            tx.conn.execute("UPDATE fills SET booking='AS_OF' WHERE fill_id=?", (f1["fill_id"],))
        # an as-of OTC receipt too: the CDX initial payment of 51,075.90 received, due Thu 10-22, its contract booked AS_OF
        x = ok(self, execute(rt, "test.cash_settlement", {"account_id": US_EXCHANGE, "amount_c": 5107590, "due_date": "2026-10-22", "ref_type": "otc",
                                                            "ref_id": "CDX-T002", "contract_booking": "AS_OF"}))
        # the settlement run happens late, on Fri 10-23 10:00 (as in the import's catch-up)
        rt.clock.set("2026-10-23T10:00:00-04:00")
        t_before = bal(rt, TREASURY, "1010")
        out = lc.tick(rt, only="SETTLE_DUE")
        self.assertEqual({f1["settle_id"]: "DONE", f2["settle_id"]: "DONE", x["settle_id"]: "DONE"}, {i["key"]: i["status"] for i in out["ran"]})
        bd = lambda idem: sc.one(rt, "SELECT business_date FROM journal_entries WHERE idem_key=?", (idem,))["business_date"]   # noqa: E731
        # the as-of payable posts on its due date 10-21 and so does the Treasury sweep that funds it (own cash at the close of 10-21: 0.00);
        # the ordinary late settlement posts on the day it settles (10-23); the as-of receipt on its due date 10-22
        self.assertEqual(("2026-10-21", "2026-10-23", "2026-10-22"), (bd(f"settle:{f1['settle_id']}"), bd(f"settle:{f2['settle_id']}"), bd(f"settle:{x['settle_id']}")))
        sweeps = {s["linked_id"]: s for s in sc.q(rt, "SELECT * FROM transfers WHERE kind='SWEEP_IN' AND linked_type='settlement'")}
        self.assertEqual("2026-10-21", sweeps[f1["settle_id"]]["business_date"])
        self.assertEqual((3395100, "2026-10-21"), (sweeps[f1["settle_id"]]["amount_c"], bd(f"xfer:{sweeps[f1['settle_id']]['transfer_id']}")))
        # the ordinary one: at 10:00 on 10-23 the receipt of 51,075.90 (settled first: receivables run first) covers 33,951.00 -> no sweep
        self.assertNotIn(f2["settle_id"], sweeps)
        # settled_ts is when it really settled (Fri 10:00), whatever its business date
        self.assertEqual(rt.clock.epoch(), srow(rt, f1["settle_id"])["settled_ts"])
        # Treasury: one as-of sweep of 33,951.00
        self.assertEqual(t_before - 3395100, bal(rt, TREASURY, "1010"))
        # US Exchange: 33,951.00 swept in and paid; 51,075.90 received - 33,951.00 paid = 17,124.90
        self.assertEqual(1712490, bal(rt, US_EXCHANGE, "1010"))
        assert_integrity(self, rt, "full")

    def test_settlement_state_words(self):
        st = collateral.settlement_state
        self.assertEqual(("SETTLING", "pending settlement, due 2026-10-23", "settling"), tuple(st({"status": "PENDING", "due_date": "2026-10-23"}).values()))
        # a row of a database where the columns do not exist yet reads like any pending settlement
        self.assertEqual("SETTLING", st({"status": "PENDING", "due_date": "2026-10-23", "fail_state": None})["stage"])
        self.assertEqual(("SETTLED", "settled 2026-10-21", "filled"), tuple(st({"status": "SETTLED", "due_date": "2026-10-21", "settled_ts": 1792600000}).values()))
        s = st({"status": "SETTLED", "due_date": "2026-10-21", "settled_ts": 1792600000, "fail_state": "RESOLVED", "resolution": "BUY_IN"})
        self.assertEqual(("RESOLVED", "resolved 2026-10-21: bought in elsewhere"), (s["stage"], s["label"]))
        for words in (json.dumps(st({"status": "PENDING", "due_date": "2026-10-23", "fail_state": "FAILED", "fail_kind": k})) for k in ("UNFUNDED", "SIMULATED")):
            self.assertNotIn("_", words.replace("fail_", "").replace("\"chip\"", ""))       # trader words: no codes on screen


if __name__ == "__main__":
    unittest.main()
