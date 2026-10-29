"""Unit-change consumers (2026-10-21 regression fixes): the code that READS the display-units fix's new shapes.

The display-units fix made API money fields major-unit decimal strings (the codec renames ``equity_min_c`` 100 to
``equity_min`` "1.00") and made rate units explicit (``quotes/rates.py`` docstring UNITS: FRED values are PERCENTS,
spreads / fees / accrual rates FRACTIONS).  Four consumers broke; a checker reproduced each on an offline server:

1. GET /api/portfolio/performance answered 400 "SOFR must be a decimal fraction ... got 3.87" whenever SOFR was loaded:
   ``performance.sofr_input`` passed the store's percent where ``statistics()`` takes a fraction.  It now reads
   ``RateStore.sofr_fraction()`` (3.87 / 100 = 0.0387, divided exactly once).
2. The Cash page toasted "SOFR 387.00 % + 0.75 %" after a loan draw: the published percent went through T.fmt.rate
   (fractions).  It now goes through T.fmt.ratePct (checked statically: the JS cannot run here).
3. DATA-CORRUPTING: the PB fee-schedule editor read ``equity_min_c`` (no longer served), showed 0.00 and sent
   ``equity_min_c`` 0 / ``monthly_minimum_c`` 0 with any other change, erasing the minimum commission.  The editor now
   reads and sends the served names (``equity_min`` / ``monthly_minimum``, decimal USD) and only the edited fields; the
   server accepts those names (converted to cents with ``units.to_c``) and refuses a missing / unparsable money value
   with 400 naming the field instead of storing 0.
4. The AVAILABLE sentence, the Cash page and the ticket preview's hint now state available = max(0, ...).

The world: the three real books built by ``server.build_runtime`` offline (FakeProvider, FakeClock Tue 2026-10-20 16:30
ET, a temp home, no network); SOFR 3.87 % observed 2026-10-19 in the rate store AS A PERCENT, exactly as FRED publishes
it.  Every expected number is worked by hand in the comment next to it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import _Log
from finsim.terminal import config, prime, server as _server, tables
from finsim.terminal.clock import FakeClock
from finsim.terminal.commands import RejectError
from finsim.terminal.performance import sofr_input
from finsim.terminal.quotes.providers import FakeProvider
from finsim.terminal.quotes.rates import RateStore

D = Decimal
AT = "2026-10-20T16:30:00-04:00"
SOFR_DATE = date(2026, 10, 19)
HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(os.path.dirname(HERE), "finsim", "terminal", "static")
PB = "F1:PB_GS"

# The §19.20 performance fixture under P1: NAV in cents, Mon 2026-10-12 .. Fri 10-16; the inception row's flows_day IS the
# starting NAV; Thu 10-15 carries a 10,000.00 deposit.  Daily returns r = [0.01, -0.01, 0.02, 0.00]:
#   1,010,000 / 1,000,000 - 1 = 0.01;  999,900 / 1,010,000 - 1 = -0.01;  (1,029,898 - 10,000) / 999,900 - 1 = 0.02;  0.00
SNAPS = [("2026-10-12", 100_000_000, 100_000_000), ("2026-10-13", 101_000_000, 0), ("2026-10-14", 99_990_000, 0),
         ("2026-10-15", 102_989_800, 1_000_000), ("2026-10-16", 102_989_800, 0)]
SPY = {"2026-10-12": "100.00", "2026-10-13": "102.00", "2026-10-14": "100.98", "2026-10-15": "100.98", "2026-10-16": "101.9898"}


def build(tmp: str):
    os.environ[config.HOME_ENV] = tmp
    os.environ[config.DB_ENV] = os.path.join(tmp, "terminal.db")
    clock = FakeClock(AT)
    return _server.build_runtime(tmp, os.path.join(tmp, "terminal.db"), clock=clock, offline=True, fake_provider=FakeProvider(clock), log=_Log(),
                                 books=None, fetch_threads=0, news_enabled=False)


def snap_row(key: str, d: str, nav_c: int, flows_c: int) -> dict:
    """A complete ``nav_snapshots`` row; only the fields the statistics read carry values."""
    return {"account_id": key, "snap_date": d, "ts": 0, "nav_c": nav_c, "settled_c": nav_c, "pending_c": 0, "mv_long_c": 0, "mv_short_c": 0,
            "unrealised_c": 0, "fx_result_c": 0, "realised_cum_c": 0, "income_cum_c": 0, "financing_cum_c": 0, "fees_cum_c": 0, "capital_cum_c": nav_c,
            "transfers_cum_c": 0, "flows_day_c": flows_c, "pnl_day_c": 0, "quality": "close", "partial": 0, "detail_json": "{}"}


class Base(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV)}
        self.tmp = tempfile.mkdtemp(prefix="fst-unitcons-")
        self.assertNotIn(".finsim", self.tmp.lower())
        self.rt = build(self.tmp)
        self.rt.rates.put("SOFR", [(SOFR_DATE, D("3.87"))], persist=False)        # a PERCENT, as FRED publishes it

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def call(self, method: str, path: str, body=None, query=None):
        status, out, _headers = self.rt.router.handle(method, path, query, body, "127.0.0.1", None)
        return status, out

    def ok(self, method: str, path: str, body=None, query=None):
        status, out = self.call(method, path, body, query)
        self.assertEqual(200, status, f"{method} {path} -> {status} {out}")
        return out


# ===== 1. performance reads SOFR as a fraction ==========================================================================================
class PerformanceSofrFraction(Base):
    def seed_p1(self):
        from finsim.terminal.accounting import Accounting
        with self.rt.db.writer() as tx:
            for d, nav, flows in SNAPS:
                Accounting.insert_snapshot(tx.conn, snap_row("P1:ALL", d, nav, flows))
            for d, px in SPY.items():
                tx.x("INSERT INTO marks(instrument_id, mark_date, kind, price, currency, quality, source, method, quote_time, fetched_at, note)"
                     " VALUES ('SPY', ?, 'CLOSE', ?, 'USD', 'close', 'test', 'daily_close', NULL, 0, NULL)", (d, px))

    def test_performance_is_200_with_sofr_loaded_and_uses_0_0387(self):
        self.assertEqual((SOFR_DATE, D("3.87")), self.rt.rates.sofr())                  # the store keeps the published percent
        self.assertEqual((D("0.0387"), "2026-10-19"), sofr_input(self.rt))              # 3.87 / 100 = 0.0387, divided once
        self.assertEqual((SOFR_DATE, D("3.87")), self.rt.rates.sofr())                  # ... and the store is not changed
        self.seed_p1()
        status, v = self.call("GET", "/api/portfolio/performance", query={"book": "P"})
        self.assertEqual(200, status, v)                                                # was 400 "SOFR must be a decimal fraction ... got 3.87"
        # PORTFOLIO-15 (1.0.6): trading-day returns from inception - Mon 10-12 .. Fri 10-16, the inception return 1,000,000 / 1,000,000 - 1 = 0
        # first: [0, 0.01, -0.01, 0.02, 0.00] -> 5 returns; the inception day has no previous SPY close, so 4 pairs
        self.assertEqual(("ok", "P1", 5, 5, 4, 0), (v["status"], v["lifetime_id"], v["n_snapshots"], v["n_returns"], v["pairs_used"], v["pairs_skipped"]))
        self.assertEqual(("0.0387", "2026-10-19"), (v["sofr"], v["sofr_as_of"]))
        # rf = SOFR / 360 per day (ACT/360, sentence RATE_LAG): 0.0387 / 360 = 0.0001075 (with the percent it would be 3.87 / 360 = 0.01075)
        self.assertAlmostEqual(0.0001075, v["rf_daily"], places=12)
        # 5 < 20 trading-day returns: Sharpe, the annualised return and beta wait for the 20th (PORTFOLIO-15) - none is shown on a guess
        self.assertEqual(("insufficient history", None, None, None), (v["ratios_status"], v["sharpe"], v["ann_ret"], v["beta"]))
        # the cumulative time-weighted return is always shown: 1 x 1.01 x 0.99 x 1.02 x 1 - 1 = 0.019898
        self.assertAlmostEqual(0.019898, v["cum_twr"], places=6)
        self.assertNotIn("SOFR unavailable: Sharpe, Sortino and alpha are n/a", v["notes"])

    def test_no_sofr_is_said_not_assumed(self):
        self.rt.rates = RateStore()                                                     # FRED not loaded yet
        self.assertEqual((None, None), sofr_input(self.rt))
        self.seed_p1()
        v = self.ok("GET", "/api/portfolio/performance", query={"book": "P"})
        self.assertEqual((None, None, None), (v["sofr"], v["rf_daily"], v["sharpe"]))
        self.assertIn("SOFR unavailable: Sharpe, Sortino and alpha are n/a", v["notes"])
        # PORTFOLIO-15: no annualised return before 20 trading days; the cumulative return needs no rate (0.019898, as above)
        self.assertIsNone(v["ann_ret"])
        self.assertAlmostEqual(0.019898, v["cum_twr"], places=6)


# ===== 3. the PB schedule: the served names, only the changed fields, no silent zero ==================================================
class ScheduleMoneyFields(Base):
    def setUp(self):
        super().setUp()
        # F (Fund Book, USD 25,000,000.00) reaches the default PB / OTC threshold of 25,000,000.00 -> onboarding GS is allowed
        self.ok("POST", "/api/books/F/onboard", {"broker": "GS", "request_id": "unitcons:onboard-gs-0001"})

    def schedule(self) -> dict:
        return self.ok("GET", f"/api/accounts/{PB}/schedule")["schedule"]

    def stored(self) -> dict:
        with self.rt.db.reader() as conn:
            return json.loads(conn.execute("SELECT schedule_json FROM pb_schedules WHERE account_id=?", (PB,)).fetchone()["schedule_json"])

    def events(self) -> int:
        with self.rt.db.reader() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM events WHERE type='SETTINGS_CHANGED' AND ref_id=?", (PB,)).fetchone()[0])

    def patch(self, body: dict, rid: str):
        return self.call("PATCH", f"/api/accounts/{PB}/schedule", dict(body, request_id=rid))

    def test_the_api_serves_major_units_and_a_spread_change_leaves_the_minimums(self):
        s = self.schedule()
        # the GS seed: equity_min_c 100 cents -> "1.00"; monthly_minimum_c 0 -> "0.00"; financing_spread 0.0125 (a fraction)
        self.assertEqual(("1.00", "0.00", "0.0125"), (s["equity_min"], s["monthly_minimum"], s["financing_spread"]))
        self.assertNotIn("equity_min_c", s)
        self.assertNotIn("monthly_minimum_c", s)
        # what the fixed editor sends when only the financing spread is edited: that field and nothing else
        status, out = self.patch({"financing_spread": "0.0150"}, "unitcons:sched-0001")
        self.assertEqual(200, status, out)
        s = out["schedule"]
        self.assertEqual(("0.0150", "1.00", "0.00"), (s["financing_spread"], s["equity_min"], s["monthly_minimum"]))
        st = self.stored()
        self.assertEqual((100, 0, "0.0150"), (st["equity_min_c"], st["monthly_minimum_c"], st["financing_spread"]))   # 1.00 USD = 100 cents, kept
        with self.rt.db.reader() as conn:
            pl = json.loads(conn.execute("SELECT payload_json FROM events WHERE type='SETTINGS_CHANGED' AND ref_id=?", (PB,)).fetchone()["payload_json"])
        self.assertEqual(({"financing_spread": "0.0125"}, {"financing_spread": "0.0150"}, ["financing_spread"]), (pl["before"], pl["after"], pl["fields"]))
        self.assertEqual(D("1.00"), D(self.schedule()["equity_min"]))                 # read back: still 1.00
        self.assertEqual(100, self.rt.prime.schedule(PB).equity_min_c)                  # what fee_for reads

    def test_the_money_fields_under_their_served_names(self):
        # equity_min "1.50" = 150 cents; monthly_minimum "500.00" = 50,000 cents
        status, out = self.patch({"equity_min": "1.50", "monthly_minimum": "500.00"}, "unitcons:sched-0002")
        self.assertEqual(200, status, out)
        self.assertEqual(("1.50", "500.00"), (out["schedule"]["equity_min"], out["schedule"]["monthly_minimum"]))
        self.assertEqual((150, 50000), (self.stored()["equity_min_c"], self.stored()["monthly_minimum_c"]))
        with self.rt.db.reader() as conn:
            pl = json.loads(conn.execute("SELECT payload_json FROM events WHERE type='SETTINGS_CHANGED' AND ref_id=?", (PB,)).fetchone()["payload_json"])
        # before / after name the stored field: 100 -> 150, 0 -> 50,000
        self.assertEqual(({"equity_min_c": 100, "monthly_minimum_c": 0}, {"equity_min_c": 150, "monthly_minimum_c": 50000}), (pl["before"], pl["after"]))
        s = self.rt.prime.schedule(PB)
        self.assertEqual((150, 50000), (s.equity_min_c, s.monthly_minimum_c))
        # the stored names keep working (integer cents): 175 cents = 1.75
        status, out = self.patch({"equity_min_c": 175}, "unitcons:sched-0003")
        self.assertEqual((200, "1.75"), (status, out["schedule"]["equity_min"]))
        # an explicit zero is a value (no minimum), sent only when typed: "0.00" -> 0 cents
        status, out = self.patch({"monthly_minimum": "0.00"}, "unitcons:sched-0004")
        self.assertEqual((200, "0.00", "1.75"), (status, out["schedule"]["monthly_minimum"], out["schedule"]["equity_min"]))
        # the same value again under either name changes nothing
        status, out = self.patch({"equity_min": "1.75"}, "unitcons:sched-0005")
        self.assertEqual((400, "NOTHING_TO_UPDATE"), (status, out["error"]["code"]))

    def test_a_missing_or_unparsable_money_value_is_refused_naming_the_field(self):
        self.assertEqual(200, self.patch({"equity_min": "1.50"}, "unitcons:sched-0010")[0])
        events, before = self.events(), self.stored()
        bad = [({"equity_min": ""}, "equity_min"), ({"equity_min": None}, "equity_min"), ({"equity_min": "   "}, "equity_min"),
               ({"equity_min": "abc"}, "equity_min"), ({"equity_min": "1.005"}, "equity_min"), ({"equity_min": "-1.00"}, "equity_min"),
               ({"equity_min": 1.5}, "equity_min"), ({"equity_min": "1,000.00"}, "equity_min"), ({"equity_min": "NaN"}, "equity_min"),
               ({"monthly_minimum": ""}, "monthly_minimum"), ({"monthly_minimum": "1e3"}, "monthly_minimum"), ({"monthly_minimum": True}, "monthly_minimum"),
               ({"equity_min_c": ""}, "equity_min_c"), ({"equity_min_c": None}, "equity_min_c"), ({"equity_min_c": "1.50"}, "equity_min_c"),
               ({"equity_min_c": "²"}, "equity_min_c"), ({"monthly_minimum_c": "abc"}, "monthly_minimum_c"),
               # a good field next to a bad one: the whole command is refused, the good one is not written either
               ({"financing_spread": "0.0150", "equity_min": "abc"}, "equity_min")]
        for i, (body, field) in enumerate(bad):
            status, out = self.patch(body, f"unitcons:bad-{i:04d}")
            self.assertEqual((400, "SCHEDULE_VALUE", field), (status, out["error"]["code"], out["error"]["details"].get("field")), body)
            self.assertIn(field, out["error"]["message"], body)
        # both names of one field in one request: refused, the field named
        status, out = self.patch({"equity_min": "2.00", "equity_min_c": 200}, "unitcons:bad-both")
        self.assertEqual((400, "SCHEDULE_FIELD", "equity_min"), (status, out["error"]["code"], out["error"]["details"].get("field")))
        # every refusal left the schedule and the event log as they were: equity_min still 1.50 (150 cents), spread 0.0125
        self.assertEqual((events, before), (self.events(), self.stored()))
        self.assertEqual(("1.50", "0.0125"), (self.schedule()["equity_min"], self.schedule()["financing_spread"]))

    def test_parse_helpers(self):
        # hand: "1.50" USD = 150 cents; 2 (whole USD) = 200 cents; "0" = 0 cents
        self.assertEqual((150, 200, 0), (prime.parse_money("equity_min", "1.50"), prime.parse_money("equity_min", 2), prime.parse_money("equity_min", "0")))
        self.assertEqual(50000, prime.parse_field("monthly_minimum", "500.00"))
        self.assertEqual({"equity_min": "equity_min_c", "monthly_minimum": "monthly_minimum_c"}, prime.MONEY_ALIASES)
        with self.assertRaises(RejectError) as cm:
            prime.parse_field("equity_min_c", "")
        self.assertEqual(("SCHEDULE_VALUE", {"field": "equity_min_c"}), (cm.exception.code, cm.exception.details))


# ===== 2 + 3 + 4. the consumers in the UI (static: the JS cannot run here) ===============================================================
class StaticConsumers(unittest.TestCase):
    def read(self, *rel: str) -> str:
        with open(os.path.join(STATIC, *rel), "r", encoding="utf-8") as f:
            return f.read()

    def test_cash_page_formats_the_loan_index_as_a_percent(self):
        cash = self.read("pages", "cash.js")
        # the loan toast: the index observation (rate.value, a PERCENT) through T.fmt.ratePct, the spread / all-in (FRACTIONS) through T.fmt.rate
        self.assertIn("' drawn at ' + loanRateText(r.rate)", cash)
        fn = cash[cash.index("function loanRateText"):cash.index("function loanRowRate")]
        self.assertIn("T.fmt.ratePct(rt.value)", fn)
        self.assertIn("T.fmt.rate(rt.spread)", fn)
        self.assertIn("T.fmt.rate(rt.all_in)", fn)
        # no published percent anywhere in the page goes through the fraction formatter ("SOFR 387.00 %")
        self.assertIsNone(re.search(r"T\.fmt\.rate\([^)]*(\.value|index_pct|_pct)\b", cash), "a percent formatted as a fraction")
        rows = cash[cash.index("function loanRowRate"):cash.index("function liquidity")]
        self.assertIn("T.fmt.ratePct(pct)", rows)
        self.assertIn("T.fmt.rate(r.spread)", rows)
        # the fractions of the borrows table stay with T.fmt.rate
        self.assertIn("T.fmt.rate(r.fee_rate)", cash)
        self.assertIn("T.fmt.rate(r.rebate_rate)", cash)

    def test_cash_page_shows_the_floored_available_and_what_lies_beyond_it(self):
        cash = self.read("pages", "cash.js")
        self.assertIn("T.fmt.liquidity(r, '')", cash)                                   # reserved (Treasury sweep covers N) / UNFUNDED / overdrawn
        self.assertIn("'beyond own cash'", cash)
        self.assertIn("liquidity(r).available", cash)
        self.assertIn("liquidity(r).title", cash)                                       # the definition on hover

    def test_schedule_editor_reads_and_sends_the_served_names_and_only_edits(self):
        js = self.read("components", "pbschedule.js")
        self.assertIn("['equity_min', 'equities: minimum per fill (USD)', 'money'", js)
        self.assertIn("['monthly_minimum', 'monthly minimum commission (USD)', 'money'", js)
        self.assertNotIn("['equity_min_c'", js)
        self.assertNotIn("['monthly_minimum_c'", js)
        ch = js[js.index("function changes()"):js.index("function onSave()")]
        self.assertIn("if (raw === inp.initial) { return; }", ch)                       # an unedited field is never sent
        self.assertIn("if (inp.current == null && raw.trim() === '') { return; }", ch)   # a field not served is not sent empty
        fc = js[js.index("function fromCents"):js.index("function valueOf")]
        self.assertIn("return null;", fc)                                               # an unreadable value is null, never "0.00"

    def test_available_sentence_and_preview_hint_say_max_0(self):
        s = tables.CONVENTIONS["AVAILABLE"]
        self.assertTrue(s.startswith("Available = max(0, settled cash + receivables due by the order's settlement date"), s)
        self.assertIn("never shown below zero", s)
        self.assertIn("reserved (Treasury sweep covers N)", s)
        pv = self.read("components", "preview.js")
        self.assertIn("'max(0, ' + f.formula + ')'", pv)

    def test_edited_files_are_ascii(self):
        for rel in (("pages", "cash.js"), ("components", "pbschedule.js"), ("components", "preview.js"), ("components", "performance.js")):
            self.read(*rel).encode("ascii")


if __name__ == "__main__":
    unittest.main()
