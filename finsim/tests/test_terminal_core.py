"""WP-00 core tests: schema, seed counts, accounting day, the base-rounding plug, units, ids (TERMINAL_WORKPLAN WP-00)."""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from tests.terminal_helpers import DEBT, TREASURY, US_EXCHANGE, deposit, make_runtime
from finsim.terminal import SCHEMA_VERSION, config, units
from finsim.terminal.calendar import TradingCalendar
from finsim.terminal.clock import NY, accounting_day, epoch_of, ny_of, trade_date
from finsim.terminal.db import TABLES, Database, get_meta, get_setting, next_id
from finsim.terminal.ledger import CHART


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-core-")
        self.rt = make_runtime(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- schema ----------------------------------------------------------------------------------------------------
    def test_schema_has_every_table_and_amended_columns(self):
        with self.rt.db.reader() as conn:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()}
            for t in TABLES:
                self.assertIn(t, names, f"table {t} missing")
            self.assertIn("v_positions", names)
            cols = lambda t: {r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()}   # noqa: E731
            self.assertTrue({"trail_amt", "trail_pct", "trail_ref_px", "fill_mode", "package_id", "parent_order_id"} <= cols("orders"))
            self.assertIn("lock_until", cols("lots"))
            self.assertTrue({"base_c", "fx_rate", "fx_source"} <= cols("journal_lines"))
            self.assertTrue({"vm_balance_c", "margin_mode", "im_c", "reset_px"} <= cols("otc_contracts"))
            self.assertTrue({"flows_day_c", "pnl_day_c", "partial", "detail_json"} <= cols("nav_snapshots"))
            self.assertTrue({"linked_type", "linked_id", "shortfall_c"} <= cols("transfers"))
            sql = {r[0]: r[1] for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'").fetchall()}
            self.assertIn("'NEGOTIATED'", sql["fills"])
            self.assertIn("'PRIVATE'", sql["instruments"])
            self.assertIn("'SWAP'", sql["instruments"])
            self.assertIn("'TRAILING_STOP'", sql["orders"])
            self.assertEqual(str(SCHEMA_VERSION), get_meta(conn, "schema_version"))
            self.assertEqual(len(CHART), conn.execute("SELECT COUNT(*) FROM chart").fetchone()[0])
            # settings seed (§3.1)
            self.assertEqual(2500000000, get_setting(conn, "pb_otc_threshold_c"))
            self.assertEqual({"INDIVIDUAL": 500000000, "FUND": 2500000000, "INSTITUTION": 0}, get_setting(conn, "qp_threshold_c"))
            self.assertEqual({"fee_pct": "0.03", "settle_days": 30, "lockup_months": 6}, get_setting(conn, "private_defaults"))
            self.assertEqual({"T": {"name": "Test Book", "starting_capital_c": 1000000000}}, get_setting(conn, "book_defaults"))
            self.assertEqual(200000000, get_setting(conn, "sweep_confirm_threshold_c"))

    def test_seed_counts(self):
        m = self.rt.instruments
        # universe.json carries 827 equity rows of which 29 are CRYPTO (skipped): 550 EQUITY + 136 ETF + 79 ADR + 28 REIT + 5 PREFERRED = 798
        n_eq = sum(m.count(k) for k in ("EQUITY", "ETF", "ADR", "REIT", "PREFERRED"))
        self.assertGreaterEqual(n_eq, 790)
        self.assertEqual(0, m.count("CRYPTO"))
        self.assertEqual(25, m.count("FX"))                 # 25 currencies -> 25 pairs vs USD
        self.assertEqual(20 * 4, m.count("FUTURE"))        # 20 roots x the next 4 listed months
        self.assertEqual(4, m.count("YIELD"))
        self.assertEqual(6, m.count("INDEX"))
        self.assertGreaterEqual(m.count("BOND"), 100)
        aapl = m.get("AAPL")
        self.assertEqual(("US_EXCHANGE", "PB"), aapl.account_kinds)
        self.assertEqual("US_EQUITY", aapl.session_group)
        self.assertEqual(1, aapl.settle_days)
        jpy = m.get("USDJPY")
        self.assertEqual("JPY=X", jpy.yahoo_symbol)
        self.assertTrue(jpy.yahoo_invert)
        eur = m.get("EURUSD")
        self.assertEqual("EURUSD=X", eur.yahoo_symbol)
        self.assertFalse(eur.yahoo_invert)
        # the November 2026 crude contract: last trade = 3 business days before Sun 25 Oct 2026 -> Fri 23 Oct -> Tue 20 Oct 2026
        clx = m.get("CLX26")
        self.assertIsNotNone(clx)
        self.assertEqual("2026-10-20", clx.expiry)
        self.assertEqual("CLX26.NYM", clx.yahoo_symbol)
        # ESZ26: third Friday of December 2026 = 18 Dec
        self.assertEqual("2026-12-18", m.get("ESZ26").expiry)
        self.assertEqual("ESZ26.CME", m.get("ESZ26").yahoo_symbol)
        self.assertEqual(Decimal("50"), m.get("ESZ26").multiplier)
        bond = m.get("JPM-29")
        self.assertEqual("LIMITED", bond.support)
        self.assertEqual((), bond.account_kinds)
        # seeding again inserts nothing
        with self.rt.db.writer() as tx:
            self.assertEqual(0, m.seed(tx, self.rt.cal))

    # ---- clock -------------------------------------------------------------------------------------------------------
    def test_accounting_day_boundaries(self):
        # D covers (D-1 17:00 ET, D 17:00 ET]: 16:59:59 and 17:00:00 belong to 2026-10-20, 17:00:01 to 2026-10-21
        self.assertEqual(date(2026, 10, 20), accounting_day(datetime(2026, 10, 20, 16, 59, 59, tzinfo=NY)))
        self.assertEqual(date(2026, 10, 20), accounting_day(datetime(2026, 10, 20, 17, 0, 0, tzinfo=NY)))
        self.assertEqual(date(2026, 10, 21), accounting_day(datetime(2026, 10, 20, 17, 0, 1, tzinfo=NY)))
        # 21:30 UTC on 2026-10-20 is 17:30 EDT -> 2026-10-21
        self.assertEqual(date(2026, 10, 21), accounting_day(datetime(2026, 10, 20, 21, 30, tzinfo=timezone.utc)))
        # 13:00 UTC on 2026-10-21 is 09:00 EDT -> 2026-10-21
        self.assertEqual(date(2026, 10, 21), accounting_day(datetime(2026, 10, 21, 13, 0, tzinfo=timezone.utc)))

    def test_trade_date_rule(self):
        cal = self.rt.cal
        # CME groups after 18:00 ET belong to the next business day: Sun 25 Oct 19:00 -> Mon 26 Oct; Fri 23 Oct 19:00 -> Mon 26 Oct
        self.assertEqual(date(2026, 10, 26), trade_date(datetime(2026, 10, 25, 19, 0, tzinfo=NY), "CME_EQUITY_INDEX", cal))
        self.assertEqual(date(2026, 10, 26), trade_date(datetime(2026, 10, 23, 19, 0, tzinfo=NY), "CME_EQUITY_INDEX", cal))
        self.assertEqual(date(2026, 10, 20), trade_date(datetime(2026, 10, 20, 17, 30, tzinfo=NY), "CME_EQUITY_INDEX", cal))
        # equities: the New York date
        self.assertEqual(date(2026, 10, 20), trade_date(datetime(2026, 10, 20, 20, 0, tzinfo=NY), "US_EQUITY", cal))
        # epoch helpers round-trip: 09:30 ET on 2026-10-20 (EDT, UTC-4) = 13:30 UTC
        e = epoch_of(date(2026, 10, 20), "09:30")
        self.assertEqual(datetime(2026, 10, 20, 13, 30, tzinfo=timezone.utc), datetime.fromtimestamp(e, timezone.utc))
        self.assertEqual("2026-10-20 09:30", ny_of(e).strftime("%Y-%m-%d %H:%M"))

    # ---- units -----------------------------------------------------------------------------------------------------------
    def test_base_with_plug(self):
        # §19.10: EUR +33.33, +33.33, +33.34, -100.00 at 1.1238: 33.33 x 1.1238 = 37.456254 -> 37.46; 33.34 x 1.1238 = 37.467492 -> 37.47;
        # fx_c(-100.00) would be -112.38, but the plug on the largest line is -(37.46 + 37.46 + 37.47) = -112.39, so the sum is 0 exactly
        self.assertEqual([3746, 3746, 3747, -11239], units.base_with_plug([3333, 3333, 3334, -10000], Decimal("1.1238")))
        self.assertEqual(0, sum(units.base_with_plug([3333, 3333, 3334, -10000], Decimal("1.1238"))))
        # a two-line entry: 1,000,000.00 EUR at 1.1238 -> 1,123,800.00 both ways
        self.assertEqual([112380000, -112380000], units.base_with_plug([100000000, -100000000], "1.1238"))
        # ties on |amount| take the first line as the plug: [+5.00, -5.00] at 1.3333 -> 6.6665 -> 6.67 (HALF_EVEN on ...65 -> 6.66? no: 6.6665 -> 6.67 since the
        # discarded digit 5 follows an odd 6? HALF_EVEN rounds 6.6665 to 6.666 at 3 dp; at 2 dp the exact value 6.6665 is more than half -> 6.67)
        self.assertEqual([-(units.fx_c(-500, "1.3333")), units.fx_c(-500, "1.3333")], units.base_with_plug([500, -500], "1.3333"))
        with self.assertRaises(ValueError):
            units.base_with_plug([100, -99], "1.1")

    def test_units(self):
        # HALF_EVEN: 0.005 -> 0.00 (0 is even), 0.015 -> 0.02, 0.025 -> 0.02, 1.005 -> 1.00
        self.assertEqual(0, units.to_c("0.005"))
        self.assertEqual(2, units.to_c("0.015"))
        self.assertEqual(2, units.to_c("0.025"))
        self.assertEqual(100, units.to_c("1.005"))
        self.assertEqual(169752500, units.to_c("1697525"))
        self.assertEqual(Decimal("1697525.00"), units.from_c(169752500))
        self.assertEqual("1,697,525.00", units.fmt_money(169752500))
        self.assertEqual("-1.50", units.fmt_money(-150))
        self.assertEqual("0.05", units.fmt_money(5))
        self.assertEqual(Decimal("339.5"), units.qprice("339.50"))
        self.assertEqual("339.5", str(units.qprice("339.50")))
        self.assertEqual("100", str(units.qprice("100.00")))
        self.assertEqual("1.12345679", str(units.qprice("1.123456789")))     # 8 dp HALF_EVEN: ...6789 -> ...679
        self.assertEqual(units.qprice("0.00005"), units.qprice(str(units.qprice("0.00005"))))
        # 5,000 x 769.32 = 3,846,600.00
        self.assertEqual(384660000, units.mul_c(5000, "769.32", 1))
        # 10 contracts x 15.20 x 100 = 15,200.00
        self.assertEqual(1520000, units.mul_c(10, Decimal("15.20"), Decimal("100")))
        # 1,000,000.33 EUR x 1.1238 = 1,123,800.370854 -> 1,123,800.37
        self.assertEqual(112380037, units.fx_c(100000033, "1.1238"))
        with self.assertRaises(TypeError):
            units.to_c(1.5)
        with self.assertRaises(ValueError):
            units.to_c("NaN")
        with self.assertRaises(ValueError):
            units.to_c("abc")

    def test_account_id_re(self):
        rx = re.compile(config.ACCOUNT_ID_RE)
        for ok in ("P1:US_EXCHANGE", "I1:PB_GS", "T1:TREASURY", "F12:PRIVATE"):
            self.assertIsNotNone(rx.match(ok), ok)
        for bad in ("US_EXCHANGE", "p1:x", "P:TREASURY", "P1:pb_gs", "P1-TREASURY", ""):
            self.assertIsNone(rx.match(bad), bad)

    # ---- db plumbing ------------------------------------------------------------------------------------------------------
    def test_next_id_sequences(self):
        with self.rt.db.writer() as tx:
            self.assertEqual("O-000001", next_id(tx.conn, "order"))
            self.assertEqual("O-000002", next_id(tx.conn, "order"))
            self.assertEqual("SWP-000001", next_id(tx.conn, "swap"))
            self.assertEqual("PC-000001", next_id(tx.conn, "private_loan"))

    def test_reader_is_query_only(self):
        with self.rt.db.reader() as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO settings(key, value_json) VALUES ('x', '1')")

    def test_reopen_same_file_keeps_state_and_backs_up(self):
        # the init command is state_version 1; a deposit makes it 2
        self.assertEqual(1, self.rt.db.state_version())
        r = deposit(self.rt, 100)
        self.assertTrue(r.ok, r.body)
        self.assertEqual(2, self.rt.db.state_version())
        self.rt.db.close()
        db2 = Database(self.rt.db_path, self.rt.clock, self.rt.log)
        db2.open(books=[config.TEST_BOOK])
        try:
            self.assertEqual(2, db2.state_version())
            with db2.reader() as conn:
                self.assertEqual("1", get_meta(conn, "schema_version"))
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM lifetimes").fetchone()[0])
            backups = os.listdir(os.path.join(self.tmp, "backups"))
            self.assertEqual(1, len([b for b in backups if b.startswith("terminal-") and b.endswith(".db")]))
        finally:
            db2.close()

    def test_newer_schema_refused(self):
        self.rt.db.close()
        raw = sqlite3.connect(self.rt.db_path)
        raw.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
        raw.commit()
        raw.close()
        from finsim.terminal.db import SchemaTooNew
        db2 = Database(self.rt.db_path, self.rt.clock, self.rt.log)
        with self.assertRaises(SchemaTooNew):
            db2.open(books=[config.TEST_BOOK])

    def test_env_points_at_tmpdir_not_home(self):
        self.assertEqual(self.tmp, os.environ[config.HOME_ENV])
        self.assertTrue(config.db_path().startswith(self.tmp))
        self.assertNotIn(".finsim", config.db_path().replace(self.tmp, ""))

    def test_calendar_source_names_file(self):
        src = TradingCalendar(config.holidays_path()).source()
        self.assertTrue(src["file"].endswith("nyse-holidays.json"))
        self.assertIn(2026, src["years"])

    def test_quote_contract_freshness_and_executable(self):
        from decimal import Decimal as Dc
        from finsim.terminal.quotes import FixedMarks, QuoteRecord, freshness, is_executable
        from finsim.terminal.sessions import SessionState
        now = 1_792_526_401
        open_s = SessionState("US_EQUITY", True, "OPEN", now - 3600, now + 3600, None, "2026-10-20", False, "")
        closed_s = SessionState("US_EQUITY", False, "CLOSED", None, None, now + 60000, "2026-10-21", False, "")

        def rec(**kw):
            base = dict(instrument_id="AAPL", provider="yahoo", source="Yahoo Finance (up to 15 min delayed)", method="direct", last=Dc("339.50"),
                        quote_time=now - 120, fetched_at=now - 10, delay_s=900)
            base.update(kw)
            return QuoteRecord(**base)
        self.assertEqual("unavailable", freshness(None, open_s, now))
        self.assertEqual("unavailable", freshness(rec(last=None), open_s, now))
        self.assertEqual("private", freshness(rec(method="private_round"), open_s, now))
        self.assertEqual("computed", freshness(rec(method="model", inputs={"qualities": ["delayed"]}), open_s, now))
        self.assertEqual("stale", freshness(rec(method="model", inputs={"under": {"quality": "stale"}}), open_s, now))
        self.assertEqual("close", freshness(rec(), closed_s, now))
        self.assertEqual("stale", freshness(rec(quote_time=None), open_s, now))
        # the provider timestamp may be 900 + 300 s old: 1,100 s is delayed, 1,500 s is stale
        self.assertEqual("delayed", freshness(rec(quote_time=now - 1100), open_s, now))
        self.assertEqual("stale", freshness(rec(quote_time=now - 1500), open_s, now))
        self.assertEqual("stale", freshness(rec(fetched_at=now - 400), open_s, now))           # nothing fetched for 5 min
        self.assertEqual("stale", freshness(rec(error="HTTP 500", fetched_at=now - 100), open_s, now))
        self.assertEqual("delayed", freshness(rec(error="HTTP 500", fetched_at=now - 30), open_s, now))
        self.assertEqual("live", freshness(rec(delay_s=0), open_s, now))                        # reserved: no provider produces it
        self.assertEqual((True, "OK"), is_executable(rec(), open_s, now))
        self.assertEqual((False, "NO_QUOTE"), is_executable(None, open_s, now))
        self.assertEqual((False, "SESSION_CLOSED"), is_executable(rec(), closed_s, now))
        self.assertEqual((False, "STALE"), is_executable(rec(quote_time=now - 1500), open_s, now))
        self.assertEqual((False, "COMPUTED_NOT_EXECUTABLE"), is_executable(rec(method="model"), open_s, now))
        self.assertEqual((False, "COMPUTED_NOT_EXECUTABLE"), is_executable(rec(method="private_round"), open_s, now))
        self.assertEqual((False, "QUOTE_FROM_PREVIOUS_SESSION"), is_executable(rec(quote_time=now - 4000), open_s, now))
        self.assertEqual((False, "PROVIDER_SESSION_ENDED"), is_executable(rec(provider_session=(now - 7200, now - 120)), open_s, now))
        self.assertEqual((True, "OK"), is_executable(rec(provider_session=(now - 7200, now - 30)), open_s, now))
        chain = rec(method="chain", provider="cboe", bid=Dc("1.00"), ask=Dc("1.10"), mid=Dc("1.05"))
        self.assertEqual((True, "OK"), is_executable(chain, open_s, now))
        self.assertEqual((False, "NO_NBBO"), is_executable(rec(method="chain", bid=Dc("0"), ask=Dc("1.10"), mid=Dc("0.55")), open_s, now))
        self.assertEqual((False, "NO_NBBO"), is_executable(rec(method="chain", bid=Dc("1.20"), ask=Dc("1.10"), mid=Dc("1.15")), open_s, now))
        # the record round-trips through its dict form (what the quote cache persists)
        back = QuoteRecord.from_dict(chain.to_dict())
        self.assertEqual((chain.bid, chain.ask, chain.mid, chain.quote_time, chain.provider_session), (back.bid, back.ask, back.mid, back.quote_time, back.provider_session))
        # FixedMarks: a bare price is delayed at as_of; fx inverts USD-first pairs; a frozen snapshot refuses set()
        fm = FixedMarks({"AAPL": "339.50", "EURUSD": ("1.1238", "delayed", now - 60), "USDJPY": "160.00", "PRV-X": ("100", "private")}, as_of=now)
        self.assertEqual((Dc("339.5"), "delayed", now, True), (fm.mark("AAPL").mark, fm.mark("AAPL").quality, fm.mark("AAPL").quote_time, fm.mark("AAPL").executable))
        self.assertEqual(Dc("1.1238"), fm.fx("EUR").mark)
        self.assertEqual(Dc("0.00625"), fm.fx("JPY").mark)                  # 1 / 160.00
        self.assertEqual(Dc(1), fm.fx("USD").mark)
        self.assertEqual("unavailable", fm.fx("CHF").quality)
        self.assertEqual(("private", False, "private_round"), (fm.mark("PRV-X").quality, fm.mark("PRV-X").executable, fm.mark("PRV-X").method))
        self.assertEqual("unavailable", fm.mark("NOPE").quality)
        snap = fm.snapshot()
        fm.set("AAPL", "342.00")
        self.assertEqual(Dc("339.5"), snap.mark("AAPL").mark)
        self.assertEqual(Dc("342"), fm.mark("AAPL").mark)
        with self.assertRaises(RuntimeError):
            snap.set("AAPL", "1")


if __name__ == "__main__":
    unittest.main()
