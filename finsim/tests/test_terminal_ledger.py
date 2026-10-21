"""WP-00 ledger tests (TERMINAL_DESIGN.md §19.10): balance validation, idem keys, the rounding plug, FIFO, L3, CLOSED accounts."""
from __future__ import annotations

import random
import shutil
import tempfile
import unittest
from decimal import Decimal

from tests.terminal_helpers import DEBT, TREASURY, US_EXCHANGE, assert_integrity, bal, buy, fund, make_runtime, post_entry, sell
from finsim.terminal.ledger import CHART, natural


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-ledger-")
        self.rt = make_runtime(self.tmp)
        self.assertTrue(fund(self.rt, US_EXCHANGE, 100000000).ok)      # 1,000,000.00 to US Exchange

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _entries(self) -> int:
        with self.rt.db.reader() as conn:
            return conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0]

    def test_unbalanced_entry_rejected_nothing_written(self):
        n = self._entries()
        # +100.00 debit and only -90.00 credit in USD
        r = post_entry(self.rt, [[US_EXCHANGE, "1230", "USD", 10000], [US_EXCHANGE, "2300", "USD", -9000]], "t:unbalanced")
        self.assertFalse(r.ok)
        self.assertEqual(500, r.http)
        self.assertEqual("UNBALANCED", r.code)
        self.assertEqual(n, self._entries())
        with self.rt.db.reader() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM commands WHERE request_id=?", (r.body["request_id"],)).fetchone())
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM attention WHERE code='COMMAND_FAILED' AND resolved_ts IS NULL").fetchone()[0])

    def test_idem_key_reuse_is_duplicate_posting(self):
        # Dr 1230 accrued interest 12.34 / Cr 4300 interest income 12.34 (instrument '' is not allowed on 4300: it is per instrument -> use AAPL)
        r1 = post_entry(self.rt, [[US_EXCHANGE, "1230", "USD", 1234], [US_EXCHANGE, "4300", "USD", -1234, "AAPL"]], "t:idem:1", kind="ACCRUAL")
        self.assertTrue(r1.ok, r1.body)
        n = self._entries()
        r2 = post_entry(self.rt, [[US_EXCHANGE, "1230", "USD", 1234], [US_EXCHANGE, "4300", "USD", -1234, "AAPL"]], "t:idem:1", kind="ACCRUAL")
        self.assertEqual(409, r2.http)
        self.assertEqual("DUPLICATE_POSTING", r2.code)
        self.assertEqual(n, self._entries())
        self.assertEqual(1234, bal(self.rt, US_EXCHANGE, "1230"))
        self.assertEqual(1234, bal(self.rt, US_EXCHANGE, "4300", inst="AAPL"))

    def test_rounding_plug_on_a_foreign_entry(self):
        # §19.10: EUR +33.33 (1230), +33.33 (5000 AAPL), +33.34 (1220 AAPL), -100.00 (2300) at the snapshot rate 1.1238:
        # fx_c gives 37.46, 37.46, 37.47 (sum 112.39) while the largest line alone would round to -112.38; the plug makes it -112.39
        r = post_entry(self.rt, [[US_EXCHANGE, "1230", "EUR", 3333], [US_EXCHANGE, "5000", "EUR", 3333, "AAPL"], [US_EXCHANGE, "1220", "EUR", 3334, "AAPL"],
                                 [US_EXCHANGE, "2300", "EUR", -10000]], "t:plug", kind="ACCRUAL")
        self.assertTrue(r.ok, r.body)
        with self.rt.db.reader() as conn:
            rows = conn.execute("SELECT line_no, base_c, fx_rate, fx_source FROM journal_lines WHERE entry_id=? ORDER BY line_no", (r.body["entry_id"],)).fetchall()
        self.assertEqual([3746, 3746, 3747, -11239], [row["base_c"] for row in rows])
        self.assertEqual({"1.1238"}, {row["fx_rate"] for row in rows})
        self.assertEqual({"fixed (test)"}, {row["fx_source"] for row in rows})
        assert_integrity(self, self.rt, "ledger")          # L1 holds (Σ base = 0)
        # an explicit deal rate on the lines wins over the snapshot: 1,000,000.00 EUR at 1.12378 = 1,123,780.00
        r2 = post_entry(self.rt, [[US_EXCHANGE, "1230", "EUR", 100000000, None, "1.12378"], [US_EXCHANGE, "2300", "EUR", -100000000, None, "1.12378"]], "t:deal", kind="FX")
        self.assertTrue(r2.ok, r2.body)
        with self.rt.db.reader() as conn:
            rows = conn.execute("SELECT base_c, fx_rate, fx_source FROM journal_lines WHERE entry_id=? ORDER BY line_no", (r2.body["entry_id"],)).fetchall()
        self.assertEqual([112378000, -112378000], [row["base_c"] for row in rows])
        self.assertEqual("deal", rows[0]["fx_source"])

    def test_closed_account_refused(self):
        with self.rt.db.writer() as tx:
            tx.x("UPDATE accounts SET status='CLOSED', closed_ts=? WHERE account_id=?", (tx.ts, DEBT))
        n = self._entries()
        r = post_entry(self.rt, [[DEBT, "1230", "USD", 100], [DEBT, "4300", "USD", -100, "AAPL"]], "t:closed", kind="ACCRUAL")
        self.assertEqual(500, r.http)
        self.assertEqual("ACCOUNT_CLOSED", r.code)
        self.assertEqual(n, self._entries())

    def test_unknown_gl_and_missing_instrument(self):
        r = post_entry(self.rt, [[US_EXCHANGE, "9999", "USD", 100], [US_EXCHANGE, "1010", "USD", -100]], "t:gl")
        self.assertEqual("UNKNOWN_GL", r.code)
        # 4300 is per instrument: a line without instrument_id is refused
        r = post_entry(self.rt, [[US_EXCHANGE, "1230", "USD", 100], [US_EXCHANGE, "4300", "USD", -100]], "t:inst")
        self.assertEqual("INSTRUMENT_REQUIRED", r.code)
        r = post_entry(self.rt, [[US_EXCHANGE, "1230", "usd", 100], [US_EXCHANGE, "4300", "usd", -100, "AAPL"]], "t:ccy")
        self.assertEqual("BAD_CURRENCY", r.code)

    def test_relieve_fifo(self):
        # §19.10: lots 1,000 @ 10.00 (cost 10,000.00) and 500 @ 11.00 (cost 5,500.00); relieve 1,200 with proceeds 13,200.00 (price 11.00):
        # proceeds pro rata by qty: 13,200 x 1,000/1,200 = 11,000.00 and 2,200.00; costs 10,000.00 and 5,500 x 200/500 = 2,200.00;
        # realised +1,000.00 and 0.00
        self.assertTrue(buy(self.rt, US_EXCHANGE, "AAPL", 1000, "10.00", fee_c=0).ok)
        self.rt.clock.advance(60)
        self.assertTrue(buy(self.rt, US_EXCHANGE, "AAPL", 500, "11.00", fee_c=0).ok)
        r = sell(self.rt, US_EXCHANGE, "AAPL", 1200, "11.00", fee_c=0)
        self.assertTrue(r.ok, r.body)
        self.assertEqual(1320000, r.body["gross_c"])
        self.assertEqual([(1000, 1000000, 1100000, 100000), (200, 220000, 220000, 0)],
                         [(x["qty"], x["cost_relieved_c"], x["proceeds_c"], x["realised_c"]) for x in r.body["reliefs"]])
        with self.rt.db.reader() as conn:
            lot = conn.execute("SELECT qty_remaining, cost_c FROM lots WHERE account_id=? AND instrument_id='AAPL' AND qty_remaining > 0", (US_EXCHANGE,)).fetchall()
            self.assertEqual([(300, 330000)], [(x["qty_remaining"], x["cost_c"]) for x in lot])   # 500 - 200 = 300 @ 11.00 = 3,300.00
            pos = conn.execute("SELECT qty, cost_c FROM v_positions WHERE account_id=? AND instrument_id='AAPL'", (US_EXCHANGE,)).fetchone()
            self.assertEqual((300, 330000), (pos["qty"], pos["cost_c"]))
        self.assertEqual(330000, bal(self.rt, US_EXCHANGE, "1100", inst="AAPL"))               # L9: 10,000 + 5,500 - 12,200 = 3,300.00
        self.assertEqual(100000, bal(self.rt, US_EXCHANGE, "4000", inst="AAPL"))               # realised +1,000.00
        assert_integrity(self, self.rt, "ledger")
        # relieving more than the lots hold is refused and rolled back
        r = sell(self.rt, US_EXCHANGE, "AAPL", 301, "11.00", fee_c=0)
        self.assertEqual(500, r.http)
        self.assertEqual("INSUFFICIENT_LOTS", r.code)
        self.assertEqual(330000, bal(self.rt, US_EXCHANGE, "1100", inst="AAPL"))

    def test_relieve_fifo_residual_cent_on_last_relief(self):
        # three lots of 1 share @ 1.00; sell 3 @ 0.3333 -> gross to_c(0.9999) = 1.00; pro rata 1.00/3 = 0.333.. -> 0.33, 0.33 and the last takes 1.00 - 0.66 = 0.34
        for _ in range(3):
            self.assertTrue(buy(self.rt, US_EXCHANGE, "SPY", 1, "1.00", fee_c=0).ok)
            self.rt.clock.advance(1)
        r = sell(self.rt, US_EXCHANGE, "SPY", 3, "0.3333", fee_c=0)
        self.assertTrue(r.ok, r.body)
        self.assertEqual(100, r.body["gross_c"])
        self.assertEqual([33, 33, 34], [x["proceeds_c"] for x in r.body["reliefs"]])
        self.assertEqual([100, 100, 100], [x["cost_relieved_c"] for x in r.body["reliefs"]])
        self.assertEqual([-67, -67, -66], [x["realised_c"] for x in r.body["reliefs"]])    # 0.33 - 1.00, 0.33 - 1.00, 0.34 - 1.00
        self.assertEqual(-200, r.body["realised_c"])                                        # 1.00 - 3.00
        self.assertEqual(0, bal(self.rt, US_EXCHANGE, "1100", inst="SPY"))
        assert_integrity(self, self.rt, "ledger")

    def test_gl_balances_equal_sum_of_lines_after_random_entries(self):
        # 100 balanced entries with deterministic pseudo-random amounts (the amounts are inputs, not expectations):
        # even i: Dr 1010 x / Cr 4300 AAPL x (income); odd i: Dr 5000 AAPL x / Cr 1010 x (fee)
        rnd = random.Random(1)
        for i in range(100):
            x = rnd.randint(1, 100000)
            lines = [[US_EXCHANGE, "1010", "USD", x], [US_EXCHANGE, "4300", "USD", -x, "AAPL"]] if i % 2 == 0 else \
                    [[US_EXCHANGE, "5000", "USD", x, "AAPL"], [US_EXCHANGE, "1010", "USD", -x]]
            r = post_entry(self.rt, lines, f"t:rnd:{i}", kind="ACCRUAL")
            self.assertTrue(r.ok, r.body)
        with self.rt.db.reader() as conn:
            sums = {(r["account_id"], r["gl"], r["currency"], r["inst"]): (r["a"], r["b"]) for r in conn.execute(
                "SELECT l.account_id, l.gl, l.currency, CASE WHEN c.per_instrument=1 THEN COALESCE(l.instrument_id,'') ELSE '' END inst, SUM(amount_c) a, SUM(base_c) b"
                " FROM journal_lines l JOIN chart c ON c.gl=l.gl GROUP BY 1,2,3,4").fetchall()}
            bals = {(r["account_id"], r["gl"], r["currency"], r["instrument_id"]): (r["balance_c"], r["base_c"]) for r in conn.execute(
                "SELECT account_id, gl, currency, instrument_id, balance_c, base_c FROM gl_balances").fetchall()}
        self.assertEqual(sums, bals)
        assert_integrity(self, self.rt, "ledger")

    def test_natural_sign_helper(self):
        self.assertEqual(100, natural("1010", 100))      # asset: debit-positive
        self.assertEqual(100, natural("2100", -100))     # liability: credit stored negative, natural positive
        self.assertEqual(100, natural("3000", -100))     # equity
        self.assertEqual(100, natural("4000", -100))     # income
        self.assertEqual(100, natural("5000", 100))      # expense
        self.assertEqual(52, len(CHART))


if __name__ == "__main__":
    unittest.main()
