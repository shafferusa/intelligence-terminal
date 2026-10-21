"""WP-00 books / lifetimes / eligibility tests (TERMINAL_DESIGN.md §13.10, §19.14 first run and eligibility; the reset itself is WP-23)."""
from __future__ import annotations

import shutil
import tempfile
import unittest

from tests.terminal_helpers import (CREDIT, DEBT, FOREIGN_FX, PRIVATE, T1, TREASURY, US_EXCHANGE, assert_integrity, bal, deposit, execute, make_runtime,
                                    pb, rid)
from finsim.terminal import books, config
from finsim.terminal.commands import RejectError


class BooksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-books-")
        self.rt = make_runtime(self.tmp, books="real")

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _has_account(self, aid: str) -> bool:
        with self.rt.db.reader() as conn:
            return conn.execute("SELECT 1 FROM accounts WHERE account_id=?", (aid,)).fetchone() is not None

    def test_first_run_three_books(self):
        rt = self.rt
        with rt.db.reader() as conn:
            lifetimes = [tuple(r) for r in conn.execute("SELECT lifetime_id, book_code, seq, status, starting_capital_c FROM lifetimes ORDER BY lifetime_id").fetchall()]
            self.assertEqual([("F1", "F", 1, "OPEN", 2500000000), ("I1", "I", 1, "OPEN", 100000000000), ("P1", "P", 1, "OPEN", 100000000)], lifetimes)
            rids = {r[0] for r in conn.execute("SELECT request_id FROM commands WHERE kind='treasury.init_capital'").fetchall()}
            self.assertEqual({"init:capital:P1", "init:capital:F1", "init:capital:I1"}, rids)
            names = {r[0]: r[1] for r in conn.execute("SELECT book_code, name FROM books").fetchall()}
            self.assertEqual({"P": "Personal Book", "F": "Fund Book", "I": "Institutional Book"}, names)
        # init:capital:P1 1,000,000.00, F1 25,000,000.00, I1 1,000,000,000.00; ALL_BOOKS 1,026,000,000.00
        self.assertEqual(100000000, bal(rt, "P1:TREASURY", "1010"))
        self.assertEqual(2500000000, bal(rt, "F1:TREASURY", "1010"))
        self.assertEqual(100000000000, bal(rt, "I1:TREASURY", "1010"))
        self.assertEqual(102600000000, rt.accounting.nav("ALL_BOOKS").nav_c)
        # catalogue accounts: PRIVATE only for the INSTITUTION profile at creation
        for lid in ("P1", "F1", "I1"):
            for role in ("TREASURY", "US_EXCHANGE", "DEBT", "CREDIT", "FOREIGN_FX"):
                self.assertTrue(self._has_account(f"{lid}:{role}"), f"{lid}:{role}")
        self.assertTrue(self._has_account("I1:PRIVATE"))
        self.assertFalse(self._has_account("P1:PRIVATE"))
        self.assertFalse(self._has_account("F1:PRIVATE"))
        # F1:PRIVATE is created by the first unlock_private call (what GET /api/books triggers): 25,000,000 >= qp_threshold_c[FUND] 25,000,000
        created = books.unlock_private(rt)
        self.assertEqual(["F1:PRIVATE"], created)
        self.assertTrue(self._has_account("F1:PRIVATE"))
        self.assertFalse(self._has_account("P1:PRIVATE"))
        self.assertEqual([], books.unlock_private(rt))                   # idempotent
        # a second start re-posts nothing
        self.assertEqual([], books.ensure_first_run(rt))
        with rt.db.reader() as conn:
            self.assertEqual(3, conn.execute("SELECT COUNT(*) FROM commands WHERE kind='treasury.init_capital'").fetchone()[0])
            self.assertEqual(3, conn.execute("SELECT COUNT(*) FROM lifetimes").fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM attention WHERE code='PRIVATE_MARKETS_UNLOCKED'").fetchone()[0])
        assert_integrity(self, rt, "full")

    def test_eligibility_matrix(self):
        e = books.eligibility(self.rt, "P1")
        self.assertEqual((100000000, False, False, False, False, 2500000000, 500000000, "INDIVIDUAL"), (e.nav_c, e.ecp, e.pb_otc, e.private_markets, e.has_relationships, e.threshold_c, e.qp_threshold_c, e.profile))
        self.assertIn("below the ECP floor 10,000,000.00", e.pb_otc_reason)
        self.assertIn("25,000,000.00", e.pb_otc_reason)
        self.assertIn("5,000,000.00", e.private_reason)
        self.assertFalse(e.can_onboard)
        f = books.eligibility(self.rt, "F1")
        self.assertEqual((2500000000, True, True, True, True), (f.nav_c, f.ecp, f.pb_otc, f.private_markets, f.can_onboard))
        self.assertIn("meets the PB/OTC threshold", f.pb_otc_reason)
        i = books.eligibility(self.rt, "I1")
        self.assertEqual((True, True, True, "institutional profile"), (i.pb_otc, i.private_markets, i.can_onboard, i.pb_otc_reason))
        self.assertIn("institutional", i.private_reason)
        # nav_c may be supplied (the sidebar passes the NAV it already has)
        self.assertTrue(books.eligibility(self.rt, "P1", nav_c=2500000000).pb_otc)
        with self.assertRaises(RejectError):
            books.eligibility(self.rt, "Z9")

    def test_deposit_to_threshold_unlocks_and_relationships_persist(self):
        rt = self.rt
        # deposit 24,000,000.00 into P1 -> NAV 25,000,000.00 -> pb_otc True; a new relationship may be opened
        self.assertTrue(deposit(rt, 2400000000, lifetime="P1").ok)
        e = books.eligibility(rt, "P1")
        self.assertEqual((2500000000, True, True, True, True), (e.nav_c, e.ecp, e.pb_otc, e.can_onboard, e.ecp))
        books.require_pb_otc(rt, "P1:US_EXCHANGE")        # no raise
        books.require_onboard(rt, "P1")                   # no raise
        self.assertTrue(e.private_markets)                # 25,000,000 >= the Personal QP threshold 5,000,000
        # simulate WP-23's onboarding record: three agreements for GS
        with rt.db.writer() as tx:
            cid = tx.one("SELECT id FROM commands ORDER BY id DESC LIMIT 1")["id"]
            for kind in ("ISDA_MASTER", "CSA", "PBA"):
                tx.x("INSERT INTO agreements(lifetime_id, broker, kind, signed_date, command_id, note) VALUES ('P1','GS',?, '2026-10-20', ?, 'test')", (kind, cid))
        # withdraw 1,000,000.00 -> NAV 24,000,000.00: pb_otc stays True through has_relationships; a second broker needs the threshold
        self.assertTrue(execute(rt, "treasury.withdraw", {"lifetime_id": "P1", "amount_c": 100000000}).ok)
        e = books.eligibility(rt, "P1")
        self.assertEqual((2400000000, True, True, False, True), (e.nav_c, e.pb_otc, e.has_relationships, e.can_onboard, e.ecp))
        self.assertIn("existing relationships stay open", e.pb_otc_reason)
        books.require_pb_otc(rt, "P1:US_EXCHANGE")        # an OTC booking only re-checks the ECP floor
        with self.assertRaises(RejectError) as cm:
            books.require_onboard(rt, "P1")
        self.assertEqual("NOT_ELIGIBLE", cm.exception.code)
        self.assertIn("new ones need the threshold", cm.exception.message)
        # NAV below the ECP floor refuses OTC bookings even with relationships: withdraw 15,000,000.00 -> 9,000,000.00
        self.assertTrue(execute(rt, "treasury.withdraw", {"lifetime_id": "P1", "amount_c": 1500000000}).ok)
        e = books.eligibility(rt, "P1")
        self.assertEqual((900000000, False, True), (e.nav_c, e.ecp, e.pb_otc))
        with self.assertRaises(RejectError) as cm:
            books.require_pb_otc(rt, "P1:US_EXCHANGE")
        self.assertEqual("NOT_ELIGIBLE", cm.exception.code)
        self.assertIn("ECP floor", cm.exception.message)
        self.assertEqual(900000000, cm.exception.details["nav_c"])
        assert_integrity(self, rt, "full")

    def test_require_pb_otc_and_require_private_messages(self):
        rt = self.rt
        with self.assertRaises(RejectError) as cm:
            books.require_pb_otc(rt, "P1:US_EXCHANGE")
        self.assertEqual("NOT_ELIGIBLE", cm.exception.code)
        self.assertIn("below the ECP floor 10,000,000.00", cm.exception.message)
        self.assertIn("Eligible Contract Participant", cm.exception.message)        # the ELIGIBILITY sentence
        self.assertEqual({"lifetime_id": "P1", "nav_c": 100000000, "threshold_c": 2500000000, "ecp_floor_c": 1000000000, "ecp": False}, cm.exception.details)
        with self.assertRaises(RejectError) as cm:
            books.require_private(rt, "P1")
        self.assertEqual("NOT_ELIGIBLE_QP", cm.exception.code)
        self.assertIn("unlock at 5,000,000.00 NAV (now 1,000,000.00)", cm.exception.message)
        self.assertIn("qualified-purchaser", cm.exception.message)                  # the QP sentence
        books.require_pb_otc(rt, "F1:US_EXCHANGE")
        books.require_private(rt, "F1")
        books.require_pb_otc(rt, "I1:TREASURY")
        books.require_private(rt, "I1")
        with self.assertRaises(ValueError):
            books.require_pb_otc(rt, "not-an-account")
        # a closed lifetime: LIFETIME_CLOSED
        with rt.db.writer() as tx:
            tx.x("UPDATE lifetimes SET status='CLOSED', ended_ts=? WHERE lifetime_id='F1'", (tx.ts,))
            tx.x("UPDATE accounts SET status='CLOSED', closed_ts=? WHERE lifetime_id='F1'", (tx.ts,))
        with self.assertRaises(RejectError) as cm:
            books.require_pb_otc(rt, "F1:US_EXCHANGE")
        self.assertEqual("LIFETIME_CLOSED", cm.exception.code)
        with self.assertRaises(RejectError) as cm:
            books.require_private(rt, "F1")
        self.assertEqual("LIFETIME_CLOSED", cm.exception.code)

    def test_unlock_private_after_4m_deposit(self):
        rt = self.rt
        self.assertEqual([], books.unlock_private(rt, "P1"))
        self.assertFalse(self._has_account("P1:PRIVATE"))
        # deposit 4,000,000.00 into P1 -> NAV 5,000,000.00 = qp_threshold_c[INDIVIDUAL] -> P1:PRIVATE created once
        self.assertTrue(deposit(rt, 400000000, lifetime="P1").ok)
        self.assertTrue(books.eligibility(rt, "P1").private_markets)
        self.assertEqual(["P1:PRIVATE"], books.unlock_private(rt, "P1"))
        self.assertTrue(self._has_account("P1:PRIVATE"))
        with rt.db.reader() as conn:
            row = conn.execute("SELECT role, kind, name, status, lifetime_id, book_code FROM accounts WHERE account_id='P1:PRIVATE'").fetchone()
            self.assertEqual(("PRIVATE", "TRADING", "Private Markets", "OPEN", "P1", "P"), tuple(row))
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM events WHERE type='ACCOUNT_OPENED' AND account_id='P1:PRIVATE'").fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM commands WHERE request_id='unlock:private:P1'").fetchone()[0])
        self.assertEqual([], books.unlock_private(rt, "P1"))
        books.require_private(rt, "P1")        # no raise
        assert_integrity(self, rt, "full")

    def test_cross_book_transfer_and_l25(self):
        r = execute(self.rt, "treasury.transfer", {"from_account": "P1:TREASURY", "to_account": "F1:US_EXCHANGE", "currency": "USD", "amount_c": 100})
        self.assertEqual((400, "CROSS_BOOK"), (r.http, r.code))
        r = execute(self.rt, "treasury.transfer", {"from_account": "P1:TREASURY", "to_account": "P1:US_EXCHANGE", "currency": "USD", "amount_c": 100})
        self.assertTrue(r.ok, r.body)
        assert_integrity(self, self.rt, "full")      # L25: no entry mixes lifetimes

    def test_ids_and_account_kind(self):
        self.assertEqual("P2", books.lifetime_id("P", 2))
        self.assertEqual("P1:US_EXCHANGE", books.account_id("P1", "US_EXCHANGE"))
        self.assertEqual(("I1", "PB_GS"), books.split_account_id("I1:PB_GS"))
        self.assertEqual("P", books.book_code_of("P12"))
        with self.assertRaises(ValueError):
            books.split_account_id("US_EXCHANGE")
        self.assertEqual("TREASURY", books.account_kind_for({"kind": "TREASURY", "role": "TREASURY"}))
        self.assertEqual("PB", books.account_kind_for({"kind": "PB", "role": "PB_GS"}))
        self.assertEqual("US_EXCHANGE", books.account_kind_for({"kind": "TRADING", "role": "US_EXCHANGE"}))
        self.assertEqual("PRIVATE", books.account_kind_for({"kind": "TRADING", "role": "PRIVATE"}))
        inst = self.rt.instruments
        self.assertTrue(inst.tradeable_in(inst.get("AAPL"), "US_EXCHANGE"))
        self.assertTrue(inst.tradeable_in(inst.get("AAPL"), "PB"))
        self.assertFalse(inst.tradeable_in(inst.get("AAPL"), "DEBT"))
        self.assertFalse(inst.tradeable_in(inst.get("AAPL"), "TREASURY"))
        self.assertTrue(inst.tradeable_in(inst.get("EURUSD"), "TREASURY"))
        self.assertTrue(inst.tradeable_in(inst.get("EURUSD"), "FOREIGN_FX"))
        self.assertFalse(inst.tradeable_in(inst.get("JPM-29"), "DEBT"))          # bonds: Build 2, no account kinds yet
        self.assertTrue(inst.tradeable_in(inst.get("ESZ26"), "US_EXCHANGE"))

    def test_book_defaults_apply_to_new_lifetimes_only(self):
        # a changed default starting capital applies when the NEXT lifetime of the book is created (the reset, WP-23); P1 is unchanged
        with self.rt.db.writer() as tx:
            from finsim.terminal.db import set_setting
            set_setting(tx.conn, "book_defaults", {"P": {"name": "Personal Book", "starting_capital_c": 200000000}})
        self.assertEqual(100000000, bal(self.rt, "P1:TREASURY", "1010"))
        with self.rt.db.writer() as tx:
            tx.x("UPDATE lifetimes SET status='CLOSED', ended_ts=? WHERE lifetime_id='P1'", (tx.ts,))
            tx.x("UPDATE accounts SET status='CLOSED', closed_ts=? WHERE lifetime_id='P1'", (tx.ts,))
        self.assertEqual(["P2"], books.ensure_first_run(self.rt))
        self.assertEqual(200000000, bal(self.rt, "P2:TREASURY", "1010"))
        self.assertEqual(100000000, bal(self.rt, "P1:TREASURY", "1010"))
        # ALL_BOOKS consolidates the OPEN lifetimes only: 2,000,000 + 25,000,000 + 1,000,000,000
        self.assertEqual(102700000000, self.rt.accounting.nav("ALL_BOOKS").nav_c)
        self.assertEqual(["P2", "F1", "I1"], self.rt.accounting.nav("ALL_BOOKS").lifetime_ids)
        assert_integrity(self, self.rt, "full")

    def test_ensure_first_run_heals_an_open_lifetime_without_capital(self):
        # the stand-alone create_lifetime commits the lifetime row + accounts first and posts the capital in a second
        # transaction; simulate a crash between the two for a fourth book Q (2,000,000.00): the row is OPEN, Q1:TREASURY
        # holds 0.00 and there is no init:capital:Q1 command.  The next ensure_first_run must post exactly that capital
        # (1010 200,000,000 cents, 3000 200,000,000 cents), leave P1/F1/I1 untouched (3 -> 4 init commands) and L6 must tie.
        rt = self.rt
        with rt.db.writer() as tx:
            tx.x("INSERT INTO books(book_code, name, profile, starting_capital_c, sort_order, created_ts) VALUES ('Q','Quarantine Book','FUND',200000000,40,?)", (tx.ts,))
            books._insert_lifetime_and_accounts(tx.conn, tx.ts, books.Book("Q", "Quarantine Book", "FUND", 200000000), 1, 200000000, None)
        with rt.db.reader() as conn:
            self.assertEqual("OPEN", conn.execute("SELECT status FROM lifetimes WHERE lifetime_id='Q1'").fetchone()[0])
            self.assertIsNone(conn.execute("SELECT 1 FROM commands WHERE request_id='init:capital:Q1'").fetchone())
        self.assertEqual(0, bal(rt, "Q1:TREASURY", "1010"))
        self.assertEqual([], books.ensure_first_run(rt))                 # nothing newly created: Q1 already exists, it is healed
        self.assertEqual(200000000, bal(rt, "Q1:TREASURY", "1010"))
        self.assertEqual(200000000, bal(rt, "Q1:TREASURY", "3000"))
        self.assertEqual(100000000, bal(rt, "P1:TREASURY", "1010"))      # untouched
        with rt.db.reader() as conn:
            self.assertEqual(4, conn.execute("SELECT COUNT(*) FROM commands WHERE kind='treasury.init_capital'").fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM journal_entries WHERE idem_key='capital:initial:Q1'").fetchone()[0])
        # 1,026,000,000 + 2,000,000 = 1,028,000,000.00 across the four open lifetimes
        self.assertEqual(102800000000, rt.accounting.nav("ALL_BOOKS").nav_c)
        self.assertEqual([], books.ensure_first_run(rt))                 # a further start re-posts nothing
        with rt.db.reader() as conn:
            self.assertEqual(4, conn.execute("SELECT COUNT(*) FROM commands WHERE kind='treasury.init_capital'").fetchone()[0])
        assert_integrity(self, rt, "full")


class DefaultRuntimeTests(unittest.TestCase):
    def test_make_runtime_default_is_t1_with_aliases(self):
        tmp = tempfile.mkdtemp(prefix="fst-books-t1-")
        rt = make_runtime(tmp)
        try:
            self.assertEqual(("T1:TREASURY", "T1:US_EXCHANGE", "T1:DEBT", "T1:CREDIT", "T1:FOREIGN_FX", "T1:PRIVATE", "T1:PB_GS"),
                             (TREASURY, US_EXCHANGE, DEBT, CREDIT, FOREIGN_FX, PRIVATE, pb("GS")))
            self.assertEqual(1000000000, bal(rt, TREASURY, "1010"))
            with rt.db.reader() as conn:
                self.assertEqual([("T1", "T", "OPEN")], [tuple(r) for r in conn.execute("SELECT lifetime_id, book_code, status FROM lifetimes").fetchall()])
                self.assertEqual([("T", "Test Book", "FUND", 1000000000)], [tuple(r) for r in conn.execute("SELECT book_code, name, profile, starting_capital_c FROM books").fetchall()])
            # the Test Book is a FUND at 10,000,000: PB/OTC eligible only through the threshold (25M) -> False; private at 25M -> False
            e = books.eligibility(rt, T1)
            self.assertEqual((True, False, False, False), (e.ecp, e.pb_otc, e.private_markets, e.can_onboard))
            self.assertEqual(1000000000, rt.accounting.nav("ALL").nav_c)
            self.assertFalse(rt.offline is False)
            self.assertTrue(rt.db_path.startswith(tmp))
            self.assertTrue(rid().startswith("ui:"))
        finally:
            rt.close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
