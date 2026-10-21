"""WP-20 Portfolio Performance statistics (TERMINAL_DESIGN.md §6.9, §19.20): the pure ``statistics()`` on the §19.20
fixture with every figure worked by hand, insufficient history, a missing SPY close, no SOFR, the monthly table across
months, the loader ``perf_view`` on a real database with per-lifetime isolation, and the static shape of the tab component.

Every expected number below is derived from the inputs in a comment; none is pasted from the code's output.
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

from finsim.terminal.accounting import Accounting, ScopeError
from finsim.terminal.performance import (NEEDED, STATUS_INSUFFICIENT, STATUS_OK, Snap, max_drawdown, monthly_table, perf_view, sofr_input,
                                         statistics)
from finsim.terminal.tables import CONVENTIONS
from tests.terminal_helpers import deposit, make_runtime, snapshot

D = Decimal
SOFR = D("0.0387")                      # 3.87 % as a decimal fraction -> rf = 0.0387 / 360 = 0.0001075 per day
RF = 0.0387 / 360
SQRT252 = 252 ** 0.5                    # 15.874508

# §19.20 fixture: T1:ALL snapshots Mon 2026-10-12 .. Fri 10-16, NAV in cents (1,000,000.00 = 100_000_000 cents).
# The inception row's flows_day IS the starting NAV (the SNAPSHOT job writes capital_cum there when no snapshot precedes it);
# Thu 10-15 carries a 10,000.00 deposit.
SNAPS = [Snap("2026-10-12", 100_000_000, 100_000_000),
         Snap("2026-10-13", 101_000_000, 0),
         Snap("2026-10-14", 99_990_000, 0),
         Snap("2026-10-15", 102_989_800, 1_000_000),
         Snap("2026-10-16", 102_989_800, 0)]
# SPY CLOSE marks on the same dates: 100.98 x 1.01 = 101.9898
SPY = {"2026-10-12": D("100.00"), "2026-10-13": D("102.00"), "2026-10-14": D("100.98"), "2026-10-15": D("100.98"), "2026-10-16": D("101.9898")}


class StatisticsTests(unittest.TestCase):
    """The pure function on the §19.20 fixture."""

    def test_19_20_exact(self):
        v = statistics(SNAPS, SPY, SOFR, lifetime_id="T1", sofr_as_of="2026-10-19")
        self.assertEqual(STATUS_OK, v.status)
        self.assertEqual(("T1", 5, "2026-10-12", "2026-10-16", 4, 0, 4, 0), (v.lifetime_id, v.n_snapshots, v.first_date, v.last_date, v.n_returns,
                                                                           v.returns_skipped, v.pairs_used, v.pairs_skipped))
        # r = [1,010,000/1,000,000 - 1, 999,900/1,010,000 - 1, (1,029,898 - 10,000)/999,900 - 1, 1,029,898/1,029,898 - 1]
        #   = [0.01, -0.01, 0.02 (999,900 x 1.02 = 1,019,898), 0.00]
        for want, got in zip([0.01, -0.01, 0.02, 0.0], v.returns):
            self.assertAlmostEqual(want, got, places=9)
        # mean(r) = 0.02 / 4 = 0.005; ann_ret = 0.005 x 252 = 1.26
        self.assertAlmostEqual(1.26, v.ann_ret, places=9)
        # var(r, ddof=1) = (0.005^2 + 0.015^2 + 0.015^2 + 0.005^2) / 3 = 0.0005 / 3; stdev = 0.01290994
        # vol = 0.01290994 x 15.874508 = sqrt(0.0005 x 252 / 3) = sqrt(0.042) = 0.204939 -> 0.2049
        self.assertAlmostEqual(0.2049, v.vol, places=4)
        # rf = 0.0387 / 360 = 0.0001075; sharpe = (0.005 - 0.0001075) / 0.01290994 x 15.874508 = 0.378971 x 15.874508 = 6.0160
        self.assertAlmostEqual(RF, v.rf_daily, places=12)
        self.assertAlmostEqual(6.0160, v.sharpe, places=4)
        # r - rf = [0.0098925, -0.0101075, 0.0198925, -0.0001075]; min(., 0)^2 = [0, 0.00010216155625, 0, 0.00000001155625]
        # (0.0101075^2 = 101075^2 x 1e-14 = 10,216,155,625 x 1e-14); mean over ALL 4 days = 0.0001021731125 / 4 = 0.000025543278125;
        # sqrt: 5.054^2 = 25.542916, (25.543278125 - 25.542916) / (2 x 5.054) = 0.0000358 -> downside deviation 0.0050540358
        # ratio = 0.0048925 / 0.0050540358 = 0.9680382508 (0.0050540358 x 0.968 = 0.0048923067, remainder 0.0000001933 / 0.0050540358 = 0.0000382508)
        # sortino = 0.9680382508 x 15.8745078664 = 15.8745078664 - 15.8745078664 x 0.0319617492 = 15.8745078664 - 0.5073770387 = 15.36713 -> 15.3671
        # (TERMINAL_DESIGN.md s19.20 prints 15.3672 because it divides by the deviation rounded to 0.00505403: 0.9680396 x 15.874508 = 15.36716;
        #  unrounded the figure is 15.36713, so the exact hand-worked value is pinned here and the difference is recorded as a deviation)
        self.assertAlmostEqual(15.36713, v.sortino, places=5)
        self.assertEqual(15.3671, round(v.sortino, 4))
        # m = [102/100 - 1, 100.98/102 - 1, 100.98/100.98 - 1, 101.9898/100.98 - 1] = [0.02, -0.01, 0.00, 0.01]; mean(m) = 0.005
        # cov(r, m) = (0.005 x 0.015 + (-0.015)(-0.015) + 0.015 x (-0.005) + (-0.005)(0.005)) / 3 = 0.0002 / 3
        # var(m)    = (0.015^2 + 0.015^2 + 0.005^2 + 0.005^2) / 3 = 0.0005 / 3;  beta = 0.0002 / 0.0005 = 0.4
        self.assertAlmostEqual(0.4, v.beta, places=9)
        # alpha = (0.0048925 - 0.4 x (0.005 - 0.0001075)) x 252 = 0.6 x 0.0048925 x 252 = 0.0029355 x 252 = 0.739746 -> 0.7397
        self.assertAlmostEqual(0.7397, v.alpha, places=4)
        self.assertAlmostEqual(0.0029355 * 252, v.alpha, places=9)
        # flow-adjusted index 1, 1.01, 0.9999, 1.019898, 1.019898: max_dd = (1.01 - 0.9999) / 1.01 = 0.0101 / 1.01 = 0.01
        self.assertAlmostEqual(0.01, v.max_dd, places=9)
        # hit rate = count(r > 0) / 4 = 2 / 4
        self.assertEqual(0.5, v.hit_rate)
        # monthly October: nav_start = the first snapshot 1,000,000.00 (its own flows_day is not a flow of the month);
        # flows after it = 0 + 0 + 10,000.00 + 0; (1,029,898 - 1,000,000 - 10,000) / 1,000,000 = 19,898 / 1,000,000 = 0.019898
        self.assertEqual(1, len(v.monthly))
        m = v.monthly[0]
        self.assertEqual(("2026-10", "2026-10-12", "2026-10-16", 100_000_000, 102_989_800, 1_000_000, 5),
                         (m.month, m.start_date, m.end_date, m.nav_start_c, m.nav_end_c, m.flows_c, m.n_snapshots))
        self.assertAlmostEqual(0.019898, m.ret, places=9)
        self.assertEqual(("0.0387", "2026-10-19", "SPY", 0, [], []), (v.sofr, v.sofr_as_of, v.benchmark, v.partial_snapshots, v.skipped_dates, v.notes))
        self.assertEqual(CONVENTIONS["PERF"], v.sentence)

    def test_to_dict_rounds_ratios_to_4dp_and_is_json(self):
        d = statistics(SNAPS, SPY, SOFR, lifetime_id="T1").to_dict()
        # the same hand-worked figures, rounded to 4 dp (ratios) / 6 dp (returns) exactly as the JSON carries them
        self.assertEqual({"ann_ret": 1.26, "vol": 0.2049, "sharpe": 6.016, "sortino": 15.3671, "beta": 0.4, "alpha": 0.7397, "max_dd": 0.01, "hit_rate": 0.5},
                         {k: d[k] for k in ("ann_ret", "vol", "sharpe", "sortino", "beta", "alpha", "max_dd", "hit_rate")})
        self.assertEqual([0.01, -0.01, 0.02, 0.0], d["returns"])
        self.assertEqual(0.019898, d["monthly"][0]["ret"])
        self.assertEqual((100_000_000, 102_989_800, 1_000_000), (d["monthly"][0]["nav_start_c"], d["monthly"][0]["nav_end_c"], d["monthly"][0]["flows_c"]))
        # rf_daily 0.0387 / 360 = 0.000107500 at 8 dp
        self.assertEqual(0.0001075, d["rf_daily"])
        self.assertEqual(("ok", "T1", 2, 5, 4, 0, "0.0387", CONVENTIONS["PERF"]),
                         (d["status"], d["lifetime_id"], d["needed"], d["n_snapshots"], d["pairs_used"], d["pairs_skipped"], d["sofr"], d["sentence"]))
        json.dumps(d)                                      # plain JSON: no Decimal, no dataclass left inside

    def test_insufficient_history(self):
        v0 = statistics([], SPY, SOFR, lifetime_id="T1")
        self.assertEqual((STATUS_INSUFFICIENT, NEEDED, 0, None, None, [], [], None, None), (v0.status, v0.needed, v0.n_snapshots, v0.first_date, v0.last_date,
                                                                                            v0.returns, v0.monthly, v0.ann_ret, v0.sharpe))
        self.assertEqual(["insufficient history (0 of 2 snapshots)"], v0.notes)
        v1 = statistics(SNAPS[:1], SPY, SOFR, lifetime_id="T1")
        self.assertEqual((STATUS_INSUFFICIENT, 1, "2026-10-12", "2026-10-12", 0), (v1.status, v1.n_snapshots, v1.first_date, v1.last_date, v1.n_returns))
        self.assertEqual(["insufficient history (1 of 2 snapshots)"], v1.notes)
        for name in ("ann_ret", "vol", "sharpe", "sortino", "beta", "alpha", "max_dd", "hit_rate"):
            self.assertIsNone(getattr(v1, name), name)
        d = v1.to_dict()
        self.assertEqual(("insufficient history", 2, 1, [], "0.0387"), (d["status"], d["needed"], d["n_snapshots"], d["monthly"], d["sofr"]))
        # the snapshot order does not matter: the function sorts by date
        v = statistics(list(reversed(SNAPS)), SPY, SOFR)
        self.assertEqual(STATUS_OK, v.status)
        self.assertAlmostEqual(0.4, v.beta, places=9)

    def test_missing_spy_close_skips_the_pair(self):
        closes = dict(SPY)
        del closes["2026-10-16"]                             # the last date: exactly one pair (10-15 -> 10-16) has no benchmark
        v = statistics(SNAPS, closes, SOFR, lifetime_id="T1")
        self.assertEqual((STATUS_OK, 4, 3, 1, ["2026-10-16"]), (v.status, v.n_returns, v.pairs_used, v.pairs_skipped, v.skipped_dates))
        self.assertEqual(["1 benchmark pair(s) skipped: no SPY close on 2026-10-16"], v.notes)
        # the return statistics still use all four returns: unchanged from §19.20
        self.assertAlmostEqual(1.26, v.ann_ret, places=9)
        self.assertAlmostEqual(6.0160, v.sharpe, places=4)
        self.assertAlmostEqual(0.01, v.max_dd, places=9)
        self.assertEqual(0.5, v.hit_rate)
        # beta over the three paired days r = [0.01, -0.01, 0.02], m = [0.02, -0.01, 0.00]:
        # mean(r) = 0.02/3, mean(m) = 0.01/3; r - mean = [1/300, -1/60, 1/75]; m - mean = [1/60, -1/75, -1/300]
        # cov = (1/18000 + 1/4500 - 1/22500) / 2 = (21/90000) / 2;  var(m) = (1/3600 + 1/5625 + 1/90000) / 2 = (42/90000) / 2
        # beta = 21 / 42 = 0.5
        self.assertAlmostEqual(0.5, v.beta, places=9)
        # alpha = (0.02/3 - 0.0001075 - 0.5 x (0.01/3 - 0.0001075)) x 252 = (0.00655917 - 0.00161292) x 252 = 0.00494625 x 252 = 1.246455
        self.assertAlmostEqual(0.00494625 * 252, v.alpha, places=9)
        self.assertEqual(1.2465, v.to_dict()["alpha"])
        # an interior missing close (10-14) removes both pairs that touch it: (10-13 -> 10-14) and (10-14 -> 10-15)
        closes = dict(SPY)
        del closes["2026-10-14"]
        v = statistics(SNAPS, closes, SOFR)
        self.assertEqual((4, 2, 2, ["2026-10-14"]), (v.n_returns, v.pairs_used, v.pairs_skipped, v.skipped_dates))
        # paired days: (0.01, 0.02) and (0.00, 0.01): means 0.005 / 0.015; cov = (0.005 x 0.005 + (-0.005)(-0.005)) / 1 = 0.00005;
        # var(m) = (0.005^2 + 0.005^2) / 1 = 0.00005; beta = 1.0
        self.assertAlmostEqual(1.0, v.beta, places=9)
        # no close at all -> no pair, beta/alpha n/a, the return statistics still there
        v = statistics(SNAPS, {}, SOFR)
        self.assertEqual((0, 4, None, None), (v.pairs_used, v.pairs_skipped, v.beta, v.alpha))
        self.assertAlmostEqual(1.26, v.ann_ret, places=9)
        self.assertIn("beta and alpha need two SPY pairs", v.notes)

    def test_no_sofr_and_unit_error(self):
        v = statistics(SNAPS, SPY, None, lifetime_id="T1")
        self.assertEqual((STATUS_OK, None, None, None, None, None), (v.status, v.sofr, v.rf_daily, v.sharpe, v.sortino, v.alpha))
        self.assertAlmostEqual(0.4, v.beta, places=9)        # beta needs no rate
        self.assertAlmostEqual(1.26, v.ann_ret, places=9)
        self.assertEqual(["SOFR unavailable: Sharpe, Sortino and alpha are n/a"], v.notes)
        self.assertIsNone(v.to_dict()["sharpe"])
        # 3.87 passed as a percent is a unit error, never silently a 387 % rate
        with self.assertRaises(ValueError):
            statistics(SNAPS, SPY, D("3.87"))

    def test_degenerate_series(self):
        # one return only (two snapshots): r = 1,010,000/1,000,000 - 1 = 0.01; ann_ret = 2.52; vol needs two returns;
        # the single day is above rf, so the downside deviation is 0 -> Sortino n/a; beta needs two pairs
        v = statistics(SNAPS[:2], SPY, SOFR)
        self.assertEqual((STATUS_OK, 1, 1), (v.status, v.n_returns, v.pairs_used))
        self.assertAlmostEqual(2.52, v.ann_ret, places=9)
        self.assertEqual((None, None, None, None, None, 0.0, 1.0), (v.vol, v.sharpe, v.sortino, v.beta, v.alpha, v.max_dd, v.hit_rate))
        self.assertEqual(["volatility needs two daily returns", "no day below the policy rate: Sortino is n/a", "beta and alpha need two SPY pairs"], v.notes)
        # constant +1 % days: 100.00 -> 101.00 -> 102.01: r = [0.01, 0.01], stdev 0 -> vol 0.0, Sharpe n/a; both days above rf -> Sortino n/a
        flat = [Snap("2026-10-12", 10_000, 10_000), Snap("2026-10-13", 10_100, 0), Snap("2026-10-14", 10_201, 0)]
        v = statistics(flat, {}, SOFR)
        self.assertEqual(0.0, v.vol)
        self.assertEqual((None, None), (v.sharpe, v.sortino))
        self.assertIn("zero volatility: Sharpe is n/a", v.notes)
        self.assertIn("no day below the policy rate: Sortino is n/a", v.notes)
        # a wiped-out book: previous NAV 0 defines no return; the only pair is skipped -> no computable return
        wiped = [Snap("2026-10-12", 0, 0), Snap("2026-10-13", 10_000, 10_000)]
        v = statistics(wiped, {}, SOFR)
        self.assertEqual((STATUS_INSUFFICIENT, 1, 0), (v.status, v.returns_skipped, v.n_returns))
        self.assertEqual(["1 pair(s) skipped: previous NAV not positive", "no computable daily return"], v.notes)
        # partial snapshots are counted (disclosed), still used
        part = [Snap("2026-10-12", 10_000, 10_000, partial=1), Snap("2026-10-13", 10_100, 0)]
        self.assertEqual((1, 1), (statistics(part, {}, SOFR).partial_snapshots, statistics(part, {}, SOFR).n_returns))

    def test_max_drawdown(self):
        # index 1.1, 0.55, 0.66; peak 1.1: drawdowns 0, 0.5, 0.4 -> 0.5
        self.assertAlmostEqual(0.5, max_drawdown([0.1, -0.5, 0.2]), places=12)
        self.assertEqual(0.0, max_drawdown([]))
        self.assertEqual(0.0, max_drawdown([0.01, 0.02]))
        # a loss then a partial recovery: 0.9, 0.99 -> peak 1: max 0.1 (not 0.01)
        self.assertAlmostEqual(0.1, max_drawdown([-0.1, 0.1]), places=12)

    def test_monthly_table_across_months(self):
        snaps = [Snap("2026-10-30", 100_000_000, 100_000_000),   # inception: flows_day = the starting NAV
                 Snap("2026-10-31", 101_000_000, 0),
                 Snap("2026-11-01", 102_000_000, 500_000),       # +5,000.00 deposit
                 Snap("2026-11-02", 101_500_000, -1_000_000)]    # -10,000.00 withdrawal
        rows = monthly_table(snaps)
        self.assertEqual(2, len(rows))
        oct_, nov = rows
        # October: start 10-30 1,000,000.00, end 10-31 1,010,000.00, flows after the start 0: 10,000 / 1,000,000 = 0.01
        self.assertEqual(("2026-10", "2026-10-30", "2026-10-31", 100_000_000, 101_000_000, 0, 2), (oct_.month, oct_.start_date, oct_.end_date, oct_.nav_start_c, oct_.nav_end_c, oct_.flows_c, oct_.n_snapshots))
        self.assertAlmostEqual(0.01, oct_.ret, places=12)
        # November: start = October's last (10-31, 1,010,000.00), end 11-02 1,015,000.00, flows 5,000 - 10,000 = -5,000.00
        # (1,015,000 - 1,010,000 + 5,000) / 1,010,000 = 10,000 / 1,010,000 = 0.00990099
        self.assertEqual(("2026-11", "2026-10-31", "2026-11-02", 101_000_000, 101_500_000, -500_000, 2), (nov.month, nov.start_date, nov.end_date, nov.nav_start_c, nov.nav_end_c, nov.flows_c, nov.n_snapshots))
        self.assertAlmostEqual(10_000 / 1_010_000, nov.ret, places=12)
        self.assertEqual(0.009901, nov.to_dict()["ret"])
        # a month whose start NAV is 0 has no return
        self.assertIsNone(monthly_table([Snap("2026-10-30", 0, 0), Snap("2026-10-31", 5_000, 5_000)])[0].ret)


def _row(key: str, d: str, nav_c: int, flows_c: int, partial: int = 0) -> dict:
    """A complete ``nav_snapshots`` row with only the fields the statistics read carrying values."""
    return {"account_id": key, "snap_date": d, "ts": 0, "nav_c": nav_c, "settled_c": nav_c, "pending_c": 0, "mv_long_c": 0, "mv_short_c": 0,
            "unrealised_c": 0, "fx_result_c": 0, "realised_cum_c": 0, "income_cum_c": 0, "financing_cum_c": 0, "fees_cum_c": 0, "capital_cum_c": nav_c,
            "transfers_cum_c": 0, "flows_day_c": flows_c, "pnl_day_c": 0, "quality": "close", "partial": partial, "detail_json": "{}"}


def _mark(tx, d: str, price: str) -> None:
    tx.x("INSERT INTO marks(instrument_id, mark_date, kind, price, currency, quality, source, method, quote_time, fetched_at, note)"
         " VALUES ('SPY', ?, 'CLOSE', ?, 'USD', 'close', 'test', 'daily_close', NULL, 0, NULL)", (d, price))


class LoaderTests(unittest.TestCase):
    """``perf_view`` on a real database: the lifetime's own ``{lifetime}:ALL`` rows only, SPY closes from ``marks``, SOFR from the rate store."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-perf-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_per_lifetime_isolation_from_the_database(self):
        rt = self.rt = make_runtime(self.tmp, books="real")      # P1, F1, I1
        with rt.db.writer() as tx:
            for s in SNAPS:                                     # the §19.20 path under P1
                Accounting.insert_snapshot(tx.conn, _row("P1:ALL", s.snap_date, s.nav_c, s.flows_day_c))
            # F1: 25,000,000.00 then 25,250,000.00 (+1 %) on two of the same dates
            Accounting.insert_snapshot(tx.conn, _row("F1:ALL", "2026-10-12", 2_500_000_000, 2_500_000_000))
            Accounting.insert_snapshot(tx.conn, _row("F1:ALL", "2026-10-13", 2_525_000_000, 0))
            for d, px in SPY.items():
                _mark(tx, d, str(px))
            _mark(tx, "2026-10-09", "99.00")                    # outside the window: never read
        rt.rates = types.SimpleNamespace(sofr=lambda: (date(2026, 10, 19), SOFR))
        rt.accounting.selected_book = "P"
        v = perf_view(rt)                                       # scope None -> "ALL" -> the selected book's open lifetime
        self.assertEqual(("P1", STATUS_OK, 5, 4, 0, "0.0387", "2026-10-19"), (v.lifetime_id, v.status, v.n_snapshots, v.pairs_used, v.pairs_skipped, v.sofr, v.sofr_as_of))
        self.assertAlmostEqual(6.0160, v.sharpe, places=4)
        self.assertAlmostEqual(0.4, v.beta, places=9)
        self.assertAlmostEqual(0.019898, v.monthly[0].ret, places=9)
        self.assertEqual(v.to_dict(), perf_view(rt, "P1").to_dict())
        self.assertEqual(v.to_dict(), perf_view(rt, "P1:ALL").to_dict())
        self.assertEqual(v.to_dict(), perf_view(rt, "P1:US_EXCHANGE").to_dict())   # an account id -> its lifetime
        f = perf_view(rt, "F1")
        # F1 sees only its two rows: r = 25,250,000/25,000,000 - 1 = 0.01; one pair (10-12 -> 10-13) with m = 0.02
        self.assertEqual(("F1", STATUS_OK, 2, 1, 1, 0), (f.lifetime_id, f.status, f.n_snapshots, f.n_returns, f.pairs_used, f.pairs_skipped))
        self.assertAlmostEqual(0.01, f.returns[0], places=9)
        self.assertEqual((2_500_000_000, 2_525_000_000, 0), (f.monthly[0].nav_start_c, f.monthly[0].nav_end_c, f.monthly[0].flows_c))
        i = perf_view(rt, "I1")                                 # no snapshots yet
        self.assertEqual(("I1", STATUS_INSUFFICIENT, 0, ["insufficient history (0 of 2 snapshots)"], "0.0387"), (i.lifetime_id, i.status, i.n_snapshots, i.notes, i.sofr))
        with self.assertRaises(ScopeError):
            perf_view(rt, "ALL_BOOKS")                          # per lifetime only
        with self.assertRaises(ScopeError):
            perf_view(rt, "Z9:ALL")
        # a percent-unit SOFR from the rate store surfaces as an error, never as a 387 % rate
        rt.rates = types.SimpleNamespace(sofr=lambda: (date(2026, 10, 19), D("3.87")))
        with self.assertRaises(ValueError):
            perf_view(rt, "P1")

    def test_real_snapshots_and_flows(self):
        rt = self.rt = make_runtime(self.tmp)                   # T1 with USD 10,000,000.00; no rate store bound
        self.assertEqual((None, None), sofr_input(rt))
        v = perf_view(rt)
        self.assertEqual(("T1", STATUS_INSUFFICIENT, 0, None), (v.lifetime_id, v.status, v.n_snapshots, v.sofr))
        rt.clock.set("2026-10-19T17:45:00-04:00")
        snapshot(rt, "ALL", date(2026, 10, 19))                 # the real snapshot writer: NAV 10,000,000.00, flows_day = the capital
        v = perf_view(rt)
        self.assertEqual((STATUS_INSUFFICIENT, 1, "2026-10-19", ["insufficient history (1 of 2 snapshots)"]), (v.status, v.n_snapshots, v.first_date, v.notes))
        rt.clock.set("2026-10-20T10:00:00-04:00")
        self.assertTrue(deposit(rt, 10_000_000).ok)             # +100,000.00 capital on 10-20
        rt.clock.set("2026-10-20T17:45:00-04:00")
        row = snapshot(rt, "ALL", date(2026, 10, 20))
        self.assertEqual((1_010_000_000, 10_000_000), (row["nav_c"], row["flows_day_c"]))
        v = perf_view(rt)
        # r = (10,100,000 - 100,000) / 10,000,000 - 1 = 0: the deposit is not a gain
        self.assertEqual((STATUS_OK, 2, 1), (v.status, v.n_snapshots, v.n_returns))
        self.assertEqual(0.0, v.returns[0])
        self.assertEqual((0.0, 0.0, 0.0), (v.ann_ret, v.max_dd, v.hit_rate))
        # no SPY marks recorded and no SOFR: both disclosed, nothing invented
        self.assertEqual((0, 1, ["2026-10-19", "2026-10-20"], None, None), (v.pairs_used, v.pairs_skipped, v.skipped_dates, v.sharpe, v.sofr))
        self.assertIn("SOFR unavailable: Sharpe, Sortino and alpha are n/a", v.notes)
        # monthly October: start 10-19 10,000,000.00, end 10-20 10,100,000.00, flows 100,000.00 -> 0
        m = v.monthly[0]
        self.assertEqual(("2026-10", 1_000_000_000, 1_010_000_000, 10_000_000, 0.0), (m.month, m.nav_start_c, m.nav_end_c, m.flows_c, m.ret))
        json.dumps(v.to_dict())


class ComponentSourceTests(unittest.TestCase):
    """The tab component is static: plain ASCII, no CDN, computes nothing, shows the insufficient-history count."""

    def test_component_source(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(here, "finsim", "terminal", "static", "components", "performance.js")
        with open(path, "rb") as fh:
            raw = fh.read()
        src = raw.decode("ascii")                               # pure ASCII (non-ASCII glyphs are \\u escapes)
        self.assertIn("T.components.performance", src)
        self.assertIn("/api/portfolio/performance", src)
        self.assertIn(" of ' + v.needed + ' snapshots", src)    # "insufficient history (n of 2 snapshots)" from the JSON's own status/count
        self.assertNotIn("http://", src)
        self.assertNotIn("https://", src)
        for banned in ("Math.sqrt", "Math.log", "Math.pow", "Math.exp", ".reduce("):
            self.assertNotIn(banned, src, f"the page must not compute statistics ({banned})")
        self.assertTrue(src.replace("\r\n", "\n").endswith("})();\n"))   # EOL-agnostic (CRLF checkouts)


if __name__ == "__main__":
    unittest.main()
