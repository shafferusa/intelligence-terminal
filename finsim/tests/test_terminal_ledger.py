"""WP-00 ledger tests (TERMINAL_DESIGN.md §19.10): balance validation, idem keys, the rounding plug, FIFO, L3, CLOSED accounts; FINSIM1 C11
(P4): the new OTC GLs (4451 / 4452 / 4453 / 1335 / 2345), the business date from ``ctx.asof_date`` and the chart rows on a database built from
the 1.0.3.1 schema (tests/fixtures/terminal/schema_1031.sql, P1's fixture)."""
from __future__ import annotations

import os
import random
import shutil
import sqlite3
import tempfile
import types
import unittest
from decimal import Decimal

from tests.terminal_helpers import DEBT, TREASURY, US_EXCHANGE, assert_integrity, bal, buy, execute, fund, make_runtime, post_entry, sell
from finsim.terminal import config, ledger
from finsim.terminal.clock import FakeClock
from finsim.terminal.db import Database
from finsim.terminal.ledger import CHART, Entry, cr, dr, natural

SCHEMA_1031 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "terminal", "schema_1031.sql")
NEW_GLS = ("1335", "2345", "4451", "4452", "4453")


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
        # Build 2 P08: + 1420 / 2420 / 2520 / 4360 / 5350 (securities lent out) = 57; FINSIM1 C11: + 1335 / 2345 / 4451 / 4452 / 4453 = 62
        self.assertEqual(62, len(CHART))

    def test_otc_gls_kinds_and_groups(self):
        """C11 / PORTFOLIO-08: 1335 / 2345 are the per-contract cost basis (asset / liability), outside the lot-tied cost GLs (L9) and outside
        realised; 4451 / 4453 are financing (kind EXPENSE: a cost is positive, a financing RECEIVED is a negative cost, the sign the P&L explain
        subtracts), 4452 is income; 4450 stays the realised swap result."""
        kinds = {gl: (CHART[gl].kind, CHART[gl].per_instrument) for gl in NEW_GLS}
        self.assertEqual({"1335": ("ASSET", True), "2345": ("LIABILITY", True), "4451": ("EXPENSE", True), "4452": ("INCOME", True),
                          "4453": ("EXPENSE", True)}, kinds)
        self.assertEqual(("1335", "2345"), ledger.OTC_COST_GLS)
        for gl in ledger.OTC_COST_GLS:
            self.assertNotIn(gl, ledger.COST_LONG_GLS + ledger.COST_SHORT_GLS + ledger.REALISED_GLS + ledger.INCOME_GLS + ledger.FINANCING_GLS)
        self.assertIn("4451", ledger.FINANCING_GLS)
        self.assertIn("4453", ledger.FINANCING_GLS)
        self.assertIn("4452", ledger.INCOME_GLS)
        self.assertEqual(("4000", "4400", "4450"), ledger.REALISED_GLS)
        # a TRS payer RECEIVES 455.31 of financing: Cr 4451 455.31 -> stored -45531 -> natural (EXPENSE) -455.31, a negative cost; an IRS
        # payer's pay leg Dr 4453 983.33 -> natural +983.33, a cost; a dividend equivalent received Cr 4452 -> natural +, income
        self.assertEqual((-45531, 98333, 5000), (natural("4451", -45531), natural("4453", 98333), natural("4452", -5000)))

    def test_business_date_from_asof_date(self):
        """C11: ``ledger.post`` dates an entry ``entry.business_date``, else ``ctx.asof_date``, else the accounting day of now."""
        def h(ctx, payload):
            lines = [dr(US_EXCHANGE, "1230", "USD", 100), cr(US_EXCHANGE, "2300", "USD", 100)]
            ctx.post(Entry("ADJUST", "now", list(lines), "t:bd:now"))
            ctx.asof_date = payload["asof"]
            ctx.post(Entry("ADJUST", "as of", list(lines), "t:bd:asof"))
            ctx.post(Entry("ADJUST", "own date wins", list(lines), "t:bd:own", business_date="2026-10-19"))
            ctx.asof_date = None
            ctx.post(Entry("ADJUST", "now again", list(lines), "t:bd:after"))
            return {}
        self.rt.bus.register("test.asof", h)
        # the runtime's clock: Tue 2026-10-20 14:00 ET -> accounting day 2026-10-20
        r = execute(self.rt, "test.asof", {"asof": "2026-10-16"}, "t:asof-bd-0001")
        self.assertTrue(r.ok, r.body)
        with self.rt.db.reader() as conn:
            got = {row["idem_key"]: row["business_date"] for row in conn.execute("SELECT idem_key, business_date FROM journal_entries WHERE idem_key LIKE 't:bd:%'")}
        self.assertEqual({"t:bd:now": "2026-10-20", "t:bd:asof": "2026-10-16", "t:bd:own": "2026-10-19", "t:bd:after": "2026-10-20"}, got)
        # a date object is accepted as well (the funding module reads either form)
        self.assertEqual("2026-10-16", ledger.asof_business_date(types.SimpleNamespace(asof_date=__import__("datetime").date(2026, 10, 16))))
        self.assertIsNone(ledger.asof_business_date(types.SimpleNamespace()))


class ChartOnOldDatabaseTests(unittest.TestCase):
    """P4 acceptance 2 (C11, review finding F01): a database built from the 1.0.3.1 schema has no chart row for the new OTC codes; a journal
    line on one is refused with foreign keys on.  ``ledger.ensure_chart_rows`` (called from ``swaps.bind``) adds them on the raw file, and the
    repo code opening the file with swaps bound accepts one line on each of 4451, 4452, 4453, 1335 and 2345."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-chart-otc-")
        self.path = os.path.join(self.tmp, "terminal.db")
        with open(SCHEMA_1031, "r", encoding="utf-8") as f:
            sql = f.read()
        conn = sqlite3.connect(self.path)
        conn.executescript(sql)
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _parents(conn) -> None:
        conn.execute("INSERT INTO lifetimes(lifetime_id, book_code, seq, name, started_ts, starting_capital_c, status) VALUES ('F1','F',1,'Fund Book 1',0,0,'OPEN')")
        conn.execute("INSERT INTO accounts(account_id, book_code, lifetime_id, role, name, kind, opened_ts) VALUES ('F1:PB_GS','F','F1','PB_GS','Goldman Sachs (PB)','PB',0)")
        conn.execute("INSERT INTO commands(id, request_id, kind, actor, payload_json, payload_sha256, result_json, http_status, started_ts, finished_ts, duration_ms,"
                     " app_version, state_version) VALUES (1,'test:otc-chart-0001','test.chart','cli','{}','0','{}',200,0,0,0,'1.0.3.1',1)")
        conn.execute("INSERT INTO journal_entries(entry_id, command_id, ts, business_date, kind, memo, idem_key) VALUES (1,1,0,'2026-10-21','TEST','otc chart (test)','test:otc-chart:1')")

    @staticmethod
    def _line(conn, line_no: int, gl: str) -> None:
        conn.execute("INSERT INTO journal_lines(entry_id, line_no, account_id, gl, currency, amount_c, base_c, fx_rate, fx_source, instrument_id)"
                     " VALUES (1,?,'F1:PB_GS',?,'USD',1,1,'1','base','SWP-000001')", (line_no, gl))

    def test_ensure_chart_rows_on_the_raw_1031_file(self):
        raw = sqlite3.connect(self.path)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys=ON")
        have = {r["gl"] for r in raw.execute("SELECT gl FROM chart").fetchall()}
        self.assertEqual(52, len(have))
        self.assertEqual(set(), set(NEW_GLS) & have)                                  # 1.0.3.1 knows none of the new OTC codes
        self._parents(raw)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "FOREIGN KEY constraint failed"):
            self._line(raw, 1, "4451")
        added = ledger.ensure_chart_rows(raw, NEW_GLS)                               # the five codes only
        self.assertEqual(5, added)
        for i, gl in enumerate(NEW_GLS, start=1):
            self._line(raw, i, gl)
        self.assertEqual([], raw.execute("PRAGMA foreign_key_check").fetchall())
        row = raw.execute("SELECT name, kind, per_instrument, bucket FROM chart WHERE gl='2345'").fetchone()
        self.assertEqual((CHART["2345"].name, "LIABILITY", 1, "otc_cost"), tuple(row))
        self.assertEqual(0, ledger.ensure_chart_rows(raw, NEW_GLS))                 # idempotent: INSERT OR IGNORE
        self.assertEqual(62 - 52 - 5, ledger.ensure_chart_rows(raw))                # the lending codes 1.0.3.1 never seeded come with a full sync
        raw.commit()
        raw.close()

    def test_repo_code_with_swaps_bound_accepts_a_line_on_each_new_gl(self):
        from finsim.terminal.products import swaps
        db = Database(self.path, FakeClock("2026-10-27T18:00:00-04:00"), None)
        db.open(books=list(config.BOOKS))
        try:
            swaps.bind(types.SimpleNamespace(db=db, log=None))
            with db.writer() as tx:
                self.assertEqual(1, tx.one("PRAGMA foreign_keys")[0])
                self._parents(tx.conn)
                for i, gl in enumerate(NEW_GLS, start=1):
                    self._line(tx.conn, i, gl)
                self.assertEqual([], tx.q("PRAGMA foreign_key_check"))
            with db.reader() as conn:
                self.assertEqual(sorted(NEW_GLS), [r[0] for r in conn.execute("SELECT DISTINCT gl FROM journal_lines ORDER BY gl").fetchall()])
                cols = {r[1] for r in conn.execute("PRAGMA table_info(otc_contracts)").fetchall()}
                self.assertTrue(set(swaps.C10_COLUMNS) <= cols, cols)                # C10: the as-of columns on the old file
                self.assertTrue({"ref_type", "ref_id"} <= {r[1] for r in conn.execute("PRAGMA table_info(settlements)").fetchall()})
            swaps.bind(types.SimpleNamespace(db=db, log=None))                       # a second bind adds nothing and raises nothing
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
