"""WP-01 marks tests: discount factors (§19.15 to 8 dp), par-yield interpolation, the §19.13 bond and CIP pins, the option model's
inputs and labels, mark selection per kind, fx() orientation, snapshot immutability (TERMINAL_WORKPLAN WP-01).

Every expected number is worked by hand in a comment.  No network, no ~/.finsim.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import make_runtime
from finsim.terminal.clock import FakeClock, epoch_of
from finsim.terminal.instruments import Instrument
from finsim.terminal.marks import MarkProvider, accrued_interest, cip_forward, coupon_period, interpolate_iv, price_bond
from finsim.terminal.quotes import QuoteRecord
from finsim.terminal.quotes.cache import ChainCache, QuoteCache, record_from_chain_row, record_from_intraday
from finsim.terminal.quotes.providers import ChainRow, ChainSnapshot, ChainUnderlying, FakeProvider, FxRates, ParCurve, parse_chain
from finsim.terminal.quotes.rates import CurveStore, FxStore, RateStore, bootstrap_dfs
from finsim.terminal.sessions import SessionState
from finsim.terminal.units import qprice

_HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(_HERE, "fixtures", "terminal")
TODAY = date(2026, 10, 20)
BUILD2 = False


def D(x) -> Decimal:
    return Decimal(str(x))


def q8(x) -> Decimal:
    return Decimal(x).quantize(Decimal("0.00000001"))


def flat(pct, as_of=date(2026, 10, 19)) -> ParCurve:
    return ParCurve(as_of, {D("0.5"): D(pct), D(1): D(pct), D(2): D(pct), D(5): D(pct), D(10): D(pct), D(30): D(pct)}, source="test curve")


class _Master:
    """A tiny instrument master for pure mark tests (no database)."""

    def __init__(self, *insts):
        self._d = {i.instrument_id: i for i in insts}

    def get(self, iid, conn=None):
        return self._d.get(iid)

    def by_underlying(self, uid, expiry=None):
        return [i for i in self._d.values() if i.underlying_id == uid]

    def all(self):
        return list(self._d.values())


def inst(iid, kind, **kw):
    base = dict(instrument_id=iid, kind=kind, symbol=iid, name=iid, currency="USD", multiplier=D(1), tick=D("0.01"), lot=1, price_scale=D(1),
                session_group="US_EQUITY", settle_days=1, account_kinds=("US_EXCHANGE",), yahoo_symbol=iid, cboe_symbol=None, underlying_id=None, expiry=None,
                strike=None, right=None, style=None, exercise=None, spec={}, support="FULL", status="ACTIVE")
    base.update(kw)
    return Instrument(**base)


def rec(iid, last, now, age=120, **kw):
    return QuoteRecord(iid, "yahoo", "test", "direct", last=qprice(last), quote_time=now - age, fetched_at=now - 10, **kw)


class _OpenSessions:
    """A session table that says OPEN (09:30-16:00 today) for every group — the pure option tests need an open session."""

    @staticmethod
    def state(group, at=None, foreign=None):
        return SessionState(group, True, "OPEN", epoch_of(TODAY, "09:30"), epoch_of(TODAY, "16:00"), None, "2026-10-20", False, "")


# ===== curves and discount factors ========================================================================================================
class CurveTests(unittest.TestCase):
    def test_discount_factors_flat_4pct_reproduce_19_15(self):
        cs = CurveStore()
        cs.put(flat("4.00"), persist=False)
        dfs = cs.discount_factors(2)
        # §19.15: df(0.5) = 1/1.02 = 0.98039216; df(1) = 1/1.0404 = 0.96116878; df(1.5) = 1/1.061208 = 0.94232233; df(2) = 1/1.08243216 = 0.92384543
        self.assertEqual([D("0.5"), D("1"), D("1.5"), D("2")], sorted(dfs))
        self.assertEqual(D("0.98039216"), q8(dfs[D("0.5")]))
        self.assertEqual(D("0.96116878"), q8(dfs[D("1")]))
        self.assertEqual(D("0.94232233"), q8(dfs[D("1.5")]))
        self.assertEqual(D("0.92384543"), q8(dfs[D("2")]))
        self.assertEqual(D("3.80772870"), q8(sum(q8(v) for v in dfs.values())))          # Σ df
        self.assertEqual(D("1.90386435"), q8(cs.annuity(2)))                             # α = 0.5 -> 3.80772870 / 2
        self.assertEqual(D("0.04000000"), q8(cs.par_rate(2)))                            # 2 × (1 − 0.92384543) / 3.80772870 = 0.15230914 / 3.80772870
        # +10 bp (flat 4.10 %), exact powers: 1/1.0205 = 0.9799118 -> 0.97991181; 1/1.04142025 = 0.9602271549 -> 0.96022715;
        # 1/1.062769365 = 0.9409379244 -> 0.94093792; 1/1.084556137 = 0.9220361840 -> 0.92203618; Σ of the rounded four 3.80311306;
        # annuity = 3.8031130712 / 2 = 1.9015565356 -> 1.90155654.  (§19.15's text prints 0.96022716 / 0.94093744 / 0.92203474 and Σ 3.80311115
        # — inconsistent with its own 1.0205 powers from the 7th decimal; WP-17 feeds its dfs directly through set_curve_dfs, so its pins are unaffected.)
        cs2 = CurveStore()
        cs2.put(flat("4.10"), persist=False)
        d2 = cs2.discount_factors(2)
        self.assertEqual([D("0.97991181"), D("0.96022715"), D("0.94093792"), D("0.92203618")], [q8(d2[t]) for t in sorted(d2)])
        self.assertEqual(D("3.80311306"), q8(sum(q8(v) for v in d2.values())))
        # the unrounded Σ/2 = 1.9015565353… sits on the 8th-decimal boundary; 7 dp is what hand arithmetic settles
        self.assertEqual(D("1.9015565"), cs2.annuity(2).quantize(Decimal("0.0000001")))
        # the same four numbers as a FixedCurve fixture (set_curve_dfs) come back untouched
        fp = FakeProvider(FakeClock("2026-10-20T14:00:00-04:00"))
        fp.set_curve_dfs({"0.5": "0.98039216", "1": "0.96116878", "1.5": "0.94232233", "2": "0.92384543"}, "2026-10-20")
        dfs_fixed, as_of = fp.curve_dfs()
        cs3 = CurveStore()
        cs3.set_fixed_dfs(dfs_fixed, as_of)
        self.assertEqual({D("0.5000"): D("0.98039216"), D("1.0000"): D("0.96116878"), D("1.5000"): D("0.94232233"), D("2.0000"): D("0.92384543")}, cs3.discount_factors())
        self.assertEqual(date(2026, 10, 20), cs3.as_of())
        self.assertEqual(D("0.92384543"), bootstrap_dfs(lambda t: D("4.00"), D(2))[D("2.0000")].quantize(Decimal("0.00000001")))

    def test_par_yield_linear_in_maturity_flat_beyond_the_ends(self):
        cs = CurveStore()
        cs.put(ParCurve(date(2026, 10, 19), {D(2): D("4.00"), D(10): D("5.00")}), persist=False)
        self.assertEqual(D("4.500000"), cs.par_yield(6))                 # 4 + (5 − 4) × (6 − 2) / (10 − 2)
        self.assertEqual(D("4.000000"), cs.par_yield(1))                 # flat below the first tenor
        self.assertEqual(D("5.000000"), cs.par_yield(30))                # flat beyond the last
        self.assertEqual(D("5.000000"), cs.par_yield(10))
        self.assertEqual(D("4.125000"), cs.par_yield(3))                 # 4 + 1 × 1/8

    def test_intraday_shifts_from_the_yield_records(self):
        now = epoch_of(TODAY, "14:00")
        cache = QuoteCache(clock=FakeClock(now))
        cache.put(rec("^TNX", "5.117", now, prev_close=D("5.136")), now)     # shift −0.019 at 10 years
        cache.put(rec("^IRX", "4.05", now, prev_close=D("4.00")), now)       # +0.05 at 3 months
        cs = CurveStore()
        cs.put(ParCurve(date(2026, 10, 19), {D(2): D("4.00"), D(10): D("5.00")}), persist=False)
        shifts, inputs = cs.intraday_shifts(cache)
        self.assertEqual({D("0.25"): D("0.05"), D(10): D("-0.019")}, shifts)
        self.assertEqual(D("4.981000"), cs.par_yield(10, shifts))        # 5.00 − 0.019
        self.assertEqual(D("4.050000"), cs.par_yield("0.25", shifts))    # 4.00 (flat) + 0.05
        self.assertEqual({"^IRX", "^TNX"}, set(inputs))
        self.assertEqual("close", inputs["^TNX"]["quality"])              # no session table in this cache: close


# ===== §19.13 Build 2 pins (pure functions, asserted now) ======================================================================================
class Build2PinTests(unittest.TestCase):
    def test_bond_pin_19_13(self):
        # 4.500 % benchmark, exactly 10 years, on a coupon date at y = 5.00 %: 20 coupons of 2.25; v = 1/1.025;
        # annuity (1 − 1.025^−20) / 0.025 = (1 − 0.610271) / 0.025 = 15.58916; coupons 2.25 × 15.58916 = 35.0756; principal 61.0271; clean 96.1027
        clean, accrued, dirty, detail = price_bond("4.5", date(2036, 10, 20), TODAY, "5.00")
        self.assertEqual(D("96.1027"), clean)
        self.assertEqual(D("0.0000"), accrued)
        self.assertEqual(clean, dirty)
        self.assertEqual((20, "1.000000", "2026-10-20", "2027-04-20"), (detail["n_coupons"], detail["w"], detail["prev_coupon"], detail["next_coupon"]))
        # accrued after 45 days of a 184-day period (1 Mar → 1 Sep 2027): 2.25 × 45 / 184 = 0.5503
        self.assertEqual(D("0.5503"), accrued_interest("4.5", date(2027, 3, 1), date(2027, 9, 1), date(2027, 4, 15)))
        self.assertEqual((date(2027, 3, 1), date(2027, 9, 1), 21), coupon_period(date(2037, 9, 1), date(2027, 4, 15)))
        clean2, accrued2, dirty2, _ = price_bond("4.5", date(2037, 9, 1), date(2027, 4, 15), "5.00")
        self.assertEqual(D("0.5503"), accrued2)
        self.assertEqual(dirty2, clean2 + accrued2)

    def test_cip_pin_19_13(self):
        # F = 1.1238 × (1 + 0.0387 × 90/360) / (1 + 0.0200 × 90/360) = 1.1238 × 1.009675 / 1.005 = 1.134673 / 1.005 = 1.129028
        self.assertEqual(D("1.129028"), cip_forward("1.1238", "0.0200", "0.0387", 90))
        # GBP base: ACT/365 on the base leg — F = 1.3394 × (1 + 0.0387 × 90/360) / (1 + 0.0400 × 90/365) = 1.352359 / 1.009863 = 1.339151
        self.assertEqual(D("1.339151"), cip_forward("1.3394", "0.0400", "0.0387", 90, basis_base=365))

    # Build 2 (2026-10-21): bond and FX-forward EXECUTION are built - their money tests live in test_terminal_bonds (UST purchase,
    # nightly accrual) and test_terminal_fx (EURUSD 3M CIP forward booked / posted / settled); the placeholder that skipped here is gone.


# ===== the option model and mark selection (pure, with a tiny master) ============================================================================
class OptionModelTests(unittest.TestCase):
    def setUp(self):
        self.now = epoch_of(TODAY, "14:00")
        self.clock = FakeClock(self.now)
        self.xyz = inst("XYZ", "EQUITY", spec={"dividend_yield": 0})
        self.opt = inst("XYZ271020C00100000", "OPTION", session_group="US_EQUITY_OPTIONS", multiplier=D(100), cboe_symbol="XYZ271020C00100000", underlying_id="XYZ",
                        expiry="2027-10-20", strike=D(100), right="C", style="EUROPEAN", exercise="PHYSICAL")
        self.master = _Master(self.xyz, self.opt, inst("^IRX", "YIELD", session_group="US_BONDS"))
        self.sessions = _OpenSessions()
        self.cache = QuoteCache(clock=self.clock, instruments=self.master, sessions=self.sessions)
        self.chains = ChainCache(self.clock)
        self.curve, self.rates = CurveStore(), RateStore()
        self.mp = MarkProvider(self.cache, self.chains, self.master, self.curve, self.rates, self.sessions, self.clock)

    def _chain(self, rows):
        under = ChainUnderlying("XYZ", D(100), D("99.9"), D("100.1"), 10, 10, self.now - 100, D(99), 0.21)
        return ChainSnapshot("XYZ", "XYZ", under, rows, self.now - 30, self.now - 100, len(rows))

    @staticmethod
    def _row(strike, iv, right="C", expiry="2027-10-20", bid="1.00", ask="1.10"):
        # prices normalised like the live parser (dec -> qprice): "7.90" is stored as 7.9
        return ChainRow(f"XYZ271020{right}{int(strike) * 1000:08d}", expiry, right, qprice(strike), qprice(bid), qprice(ask), 5, 5, qprice(bid), None, iv, 0.5, 0.01,
                        -0.01, 0.3, 10, 1)

    def test_option_model_inputs_and_labels(self):
        self.cache.put(rec("XYZ", "100", self.now), self.now)
        self.cache.put(rec("^IRX", "0", self.now), self.now)                        # r = 0.00 %
        self.chains.put(self._chain([self._row(90, 0.18), self._row(110, 0.22)]))     # σ(100) = 0.18 + (0.22 − 0.18) × (100 − 90) / (110 − 90) = 0.20
        view = self.mp.mark_for(self.opt)
        # BSM call, S = K = 100, T = 365/365 = 1, r = q = 0, σ = 0.2: d1 = 0.1, d2 = −0.1; C = 100 × (N(0.1) − N(−0.1)) = 100 × (0.5398278 − 0.4601722) = 7.9656
        self.assertEqual(D("7.9656"), view.mark)
        self.assertEqual(("computed", "model", "option model", False), (view.quality, view.method, view.source, view.executable))
        i = view.inputs
        self.assertEqual("Black-Scholes-Merton (European)", i["model"])
        self.assertEqual(("100", "100", 365, "0.000000", "0", "0.200000"), (i["S"], i["K"], i["T_days"], i["r"], i["q"], i["sigma"]))
        self.assertIn("interpolated between strikes 90 and 110", i["sigma_source"])
        self.assertEqual("^IRX (13-week bill yield)", i["rate"]["source"])
        self.assertEqual(["delayed", "delayed", "delayed"], i["qualities"])          # underlying, rate, chain — all fresh in the open session
        self.assertEqual("XYZ", i["underlying"]["instrument_id"])
        self.assertIn("not executable", view.note)
        # the same contract through option_model() directly, with an American style: the CRR tree is labelled and prices at least intrinsic
        amer = inst("XYZ271020P00100000", "OPTION", underlying_id="XYZ", expiry="2027-10-20", strike=D(100), right="P", style="AMERICAN", exercise="PHYSICAL")
        px, inputs = self.mp.option_model(amer, self.cache.get("XYZ"), self.chains.get("XYZ"), now=self.now)
        self.assertEqual("Cboe iv30 of the underlying", inputs["sigma_source"])      # no put rows -> iv30 0.21
        self.assertEqual("CRR 60-step American", inputs["model"])
        self.assertGreater(px, D("7.9"))                                             # an American put with r = 0 is worth at least the European put 8.36 (σ 0.21)

    def test_option_without_a_rate_or_vol_is_unavailable(self):
        self.cache.put(rec("XYZ", "100", self.now), self.now)
        view = self.mp.mark_for(self.opt)                                            # no ^IRX, no curve
        self.assertEqual("unavailable", view.quality)
        self.assertIn("no risk-free rate", view.note)
        self.cache.put(rec("^IRX", "4.00", self.now), self.now)
        view = self.mp.mark_for(self.opt)                                            # rate ok, but no chain / iv30 and an EQUITY underlying: no VIX fallback
        self.assertEqual("unavailable", view.quality)
        self.assertIn("no implied volatility", view.note)
        self.assertIsNone(self.mp.mark_for(self.opt).mark)

    def test_nbbo_beats_the_model_and_the_snapshot_is_immutable(self):
        self.cache.put(rec("XYZ", "100", self.now), self.now)
        self.cache.put(rec("^IRX", "0", self.now), self.now)
        self.chains.put(self._chain([self._row(90, 0.18), self._row(100, 0.20, bid="7.90", ask="8.10"), self._row(110, 0.22)]))
        snap = self.mp.snapshot()
        view = snap.mark(self.opt.instrument_id)
        self.assertEqual((D("8"), "chain", "delayed"), (view.mark, view.method, view.quality))     # mid of 7.90 / 8.10 from the cached chain
        self.assertEqual(("7.9", "8.1", 5, 5, "Cboe mid"), (view.inputs["bid"], view.inputs["ask"], view.inputs["bid_size"], view.inputs["ask_size"], view.inputs["basis"]))
        self.assertTrue(view.executable)                                             # no session table here: the chain record is delayed and quoted
        self.assertEqual(self.now, snap.as_of())
        self.cache.put(rec("XYZ", "150", self.now), self.now)                        # a later print never reaches the frozen snapshot
        self.assertEqual(D("100"), snap.mark("XYZ").mark)
        self.assertIs(snap.mark("XYZ"), snap.mark("XYZ"))                             # memoised
        self.assertFalse(hasattr(snap, "set"))
        self.assertEqual(D("150"), self.mp.snapshot().mark("XYZ").mark)


# ===== mark selection per kind with the seeded master ==============================================================================================
class MarkSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-marks-")
        self.rt = make_runtime(self.tmp)                                              # 2026-10-20 14:00 ET
        rt = self.rt
        self.now = rt.clock.epoch()
        self.cache = QuoteCache(rt.db, rt.instruments, rt.sessions, rt.clock, rt.bus, rt.log)
        self.chains = ChainCache(rt.clock)
        self.curve, self.rates, self.fxs = CurveStore(), RateStore(), FxStore()
        self.mp = MarkProvider(self.cache, self.chains, rt.instruments, self.curve, self.rates, rt.sessions, rt.clock, services=rt, fxstore=self.fxs)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _put(self, iid, last, **kw):
        self.cache.put(rec(iid, last, self.now, **kw), self.now)

    def test_equity_index_fx_and_fallbacks(self):
        self._put("AAPL", "339.50")
        self._put("SPX", "7712.16")
        self._put("USDJPY", "158.10")
        self._put("EURUSD", "1.1238")
        self.fxs.put(FxRates(TODAY, {"CHF": D("0.82363"), "EUR": D("0.89166")}), persist=False)
        snap = self.mp.snapshot()
        v = snap.mark("AAPL")
        self.assertEqual((D("339.5"), "delayed", "direct", True, ""), (v.mark, v.quality, v.method, v.executable, v.note))
        v = snap.mark("SPX")
        self.assertEqual((D("7712.16"), "delayed", False, "REFERENCE_ONLY"), (v.mark, v.quality, v.executable, v.note))
        self.assertEqual(D(1), snap.fx("USD").mark)
        j = snap.fx("JPY")
        self.assertEqual(D("0.00632511"), j.mark)                                     # 1 / 158.10 = 0.0063251107 -> 8 dp
        self.assertEqual(("delayed", "158.1", "USDJPY"), (j.quality, j.inputs["inverted_from"], j.instrument_id))
        e = snap.fx("EUR")
        self.assertEqual((D("1.1238"), "delayed", "direct"), (e.mark, e.quality, e.method))     # the Yahoo pair wins over the ECB reference
        c = snap.fx("CHF")
        self.assertEqual(D("1.21413742"), c.mark)                                     # 1 / 0.82363 = 1.2141374 (long division in the comment of WP-01)
        self.assertEqual(("close", "daily_close", False), (c.quality, c.method, c.executable))
        self.assertIn("ECB reference rate 2026-10-20", c.note)
        self.assertEqual("unavailable", snap.fx("MXN").quality)
        self.assertEqual("unavailable", snap.mark("KO").quality)                     # seeded, never fetched
        self.assertEqual("no quote yet", snap.mark("KO").note)
        self.assertEqual("unknown instrument", snap.mark("NOPE").note)

    def test_option_from_a_chain_record_and_products_not_yet_built(self):
        rt = self.rt
        under = rt.instruments.get("AAPL")
        with open(os.path.join(FIX, "cboe_aapl_trimmed.json"), "rb") as fh:
            chain = parse_chain(json.load(fh), "AAPL", "AAPL", TODAY, self.now)
        row = chain.by_occ("AAPL261120C00340000")
        with rt.db.writer() as tx:
            opt = rt.instruments.ensure_option(tx, "AAPL261120C00340000", under, row)
        rt.instruments.load()
        self._put("AAPL", "339.485")
        self.cache.put(record_from_chain_row(opt, chain, row), self.now)
        v = self.mp.mark_for(opt)
        self.assertEqual((D("8.725"), "delayed", "chain", True), (v.mark, v.quality, v.method, v.executable))   # (8.60 + 8.85) / 2; 14:00 ET, NBBO present
        self.assertEqual("Cboe delayed quotes (AAPL)", v.source)
        # the 360 call has bid 0: no NBBO -> the model (needs a rate: ^IRX 4.00 -> r = 0.04) from the cached chain's IVs
        with rt.db.writer() as tx:
            opt360 = rt.instruments.ensure_option(tx, "AAPL261120C00360000", under, chain.by_occ("AAPL261120C00360000"))
        rt.instruments.load()
        self.chains.put(chain)
        self._put("^IRX", "4.00")
        v = self.mp.mark_for(opt360)
        self.assertEqual(("computed", "model", False), (v.quality, v.method, v.executable))
        # the 360 call's own iv is not used: bid 0 makes it unquoted, and no quoted strike lies above it -> the nearest quoted strike (350, iv 0.252)
        self.assertEqual("Cboe iv of the nearest quoted strike 350", v.inputs["sigma_source"])
        self.assertIsNotNone(v.mark)
        self.assertGreater(v.mark, D("0.5"))
        self.assertEqual("0.040000", v.inputs["r"])
        private = next(i for i in rt.instruments.all() if i.kind == "PRIVATE") if rt.instruments.count("PRIVATE") else None
        if private is not None:
            self.assertEqual(("unavailable", "not yet built"), (self.mp.mark_for(private).quality, self.mp.mark_for(private).note))

    def test_bond_mark_is_computed_from_the_curve_with_the_static_spread(self):
        rt = self.rt
        bond = rt.instruments.get("JPM-29")                                            # coupon 0.0485, years 3.0, rating A, spread 80 bp
        self.assertIsNotNone(bond)
        self.assertEqual("unavailable", self.mp.mark_for(bond).quality)               # no curve yet
        self.assertEqual("no par curve", self.mp.mark_for(bond).note)
        self.curve.put(ParCurve(date(2026, 10, 19), {D(2): D("4.00"), D(10): D("5.00")}), persist=False)
        v = self.mp.mark_for(bond)
        # maturity = 2026-09-18 + round(3 × 365.25) = 1096 days = 2029-09-18; T = (2029-09-18 − 2026-10-20) = 1064 days = 2.9151 years;
        # par yield 4 + 1 × (2.9151 − 2) / 8 = 4.1144; y = 4.1144 + 0.80 (static spread, no FRED OAS) = 4.9144 % on a 4.85 % coupon -> clean just below 100
        self.assertEqual(("computed", "curve", False), (v.quality, v.method, v.executable))
        self.assertEqual("2029-09-18", v.inputs["maturity"])
        self.assertEqual("static spread (universe)", v.inputs["oas"]["series"])
        self.assertEqual("80.0", v.inputs["oas"]["value_bps"])
        self.assertLess(v.mark, D(100))
        self.assertGreater(v.mark, D(98))
        self.assertEqual("2026-10-19", v.inputs["curve"]["as_of"])

    def test_fx_forward_through_the_provider(self):
        self._put("EURUSD", "1.1238")
        self.rates.put("SOFR", [(date(2026, 10, 19), D("3.87"))], persist=False)
        self.rates.put("ECBDFR", [(date(2026, 10, 17), D("2.00"))], persist=False)
        f, inputs = self.mp.fx_forward("EURUSD", TODAY.replace(month=1, day=18, year=2027))      # 2027-01-18 − 2026-10-20 = 90 days
        self.assertEqual(D("1.129028"), f)                                              # the §19.13 pin
        self.assertEqual(("ECBDFR", "SOFR", 90, 360, 360), (inputs["r_base"]["series"], inputs["r_quote"]["series"], inputs["days"], inputs["r_base"]["basis"],
                                                              inputs["r_quote"]["basis"]))
        self.assertEqual(["delayed", "close", "close"], inputs["qualities"])
        f2, inputs2 = self.mp.fx_forward("GBPUSD", date(2027, 1, 18))
        self.assertIsNone(f2)
        self.assertIn("no spot for GBPUSD", inputs2["note"])
        self._put("GBPUSD", "1.3394")
        f3, inputs3 = self.mp.fx_forward("GBPUSD", date(2027, 1, 18))
        self.assertIsNone(f3)
        self.assertIn("IUDSOIA unavailable", inputs3["note"])                            # no SONIA observation: nothing is defaulted

    def test_record_from_intraday_keeps_fx_market_convention(self):
        fp = FakeProvider(self.rt.clock)
        fp.set("JPY=X", last="158.10", quote_time=self.now - 60, bar=(self.now - 120, 0), currency="JPY", exchange_tz="Europe/London")
        r = record_from_intraday(self.rt.instruments.get("USDJPY"), fp.intraday("JPY=X"))
        self.assertEqual((D("158.1"), "JPY", "FX_SPOT", "yahoo", "direct"), (r.last, r.currency, r.session_group, r.provider, r.method))
        self.assertEqual("Yahoo Finance chart (JPY=X)", r.source)


if __name__ == "__main__":
    unittest.main()
