"""WP-24 strategy templates: the catalogue is the old playbook verbatim (60 templates, 12 groups, every leg rule parses, no
simulation import), the §19.22 resolution pins (715 / 770 / 840 / 730 / 800, expiry_for_tenor 2027-02-19 and 2027-01-22,
sizing 1,000 + 10 / 5 / 10 + 20 / 2 ES, fill_mode AON vs SEQUENTIAL), the iron-condor package priced from a fixture chain
(credit +4,200.00, margin 28,100.00, max loss 25,800.00, breakevens 725.80 and 804.20), a sequential protective put and a
zero-cost collar, a beta-hedged long against the front ES, TRS availability per account, the 202 "fetching" answer and the
catalogue reasons.  Every expected number is worked by hand in a comment; no network; nothing touches ~/.finsim."""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Dict, List

from tests.terminal_helpers import T1, TREASURY, US_EXCHANGE, make_runtime
from finsim.terminal import books, strategies as st
from finsim.terminal.commands import RejectError
from finsim.terminal.db import set_setting
from finsim.terminal.instruments import occ_symbol
from finsim.terminal.quotes import QuoteRecord
from finsim.terminal.quotes.cache import ChainCache, QuoteCache
from finsim.terminal.quotes.providers import ChainRow, ChainSnapshot, ChainUnderlying
from finsim.terminal.units import qprice

_HERE = os.path.dirname(os.path.abspath(__file__))
OLD_PLAYBOOK = os.path.join(os.path.dirname(_HERE), "finsim", "engines", "playbook.py")
TODAY = date(2026, 10, 20)
SPOT = Decimal("769.32")
STEP5 = [Decimal(k) for k in range(700, 845, 5)]        # 700 … 840 step 5 (29 strikes)


def D(x) -> Decimal:
    return Decimal(str(x))


def occ(root: str, expiry: str, right: str, strike) -> str:
    return occ_symbol(root, date.fromisoformat(expiry), right, D(strike))


def row(root, expiry, right, strike, bid, ask, bid_size=50, ask_size=50, delta=None, vega=None, theta=None, iv=0.20) -> ChainRow:
    return ChainRow(occ(root, expiry, right, strike), expiry, right, qprice(strike), None if bid is None else qprice(bid), None if ask is None else qprice(ask),
                    bid_size, ask_size, None, None, iv, delta, None, theta, vega, 100, 10)


def chain(sym, spot, rows, now, bid=None, ask=None, age=30) -> ChainSnapshot:
    under = ChainUnderlying(sym, qprice(spot), None if bid is None else qprice(bid), None if ask is None else qprice(ask), 10, 10, now - 100, None, 0.18)
    return ChainSnapshot(sym, sym, under, rows, now - age, now - 100, len(rows))


def rec(iid, last, now, group="US_EQUITY") -> QuoteRecord:
    return QuoteRecord(iid, "yahoo", f"Yahoo Finance chart ({iid})", "direct", last=qprice(last), quote_time=now - 120, fetched_at=now - 10, session_group=group)


# ===== the catalogue ==============================================================================================================
class CatalogueTests(unittest.TestCase):
    def test_catalogue_is_the_old_playbook_verbatim(self):
        with open(OLD_PLAYBOOK, "r", encoding="utf-8") as fh:
            src = fh.read()
        start, end = src.index("GROUPS = ["), src.index("\nBY_KEY = {")
        ns: dict = {"List": List, "Dict": Dict}
        exec(compile(src[start:end], "playbook-data", "exec"), ns)       # only the data lines, never the module (it imports the World)
        self.assertEqual(ns["GROUPS"], st.GROUPS)
        self.assertEqual(ns["STRATEGIES"], st.STRATEGIES)                  # entry for entry: keys, groups, legs, every sentence
        self.assertEqual(60, len(st.STRATEGIES))
        self.assertEqual(12, len(st.GROUPS))
        self.assertEqual(60, len(st.BY_KEY))
        self.assertEqual(60, len({s["key"] for s in st.STRATEGIES}))
        codes = {c for c, _ in st.GROUPS}
        self.assertTrue(all(s["group"] in codes for s in st.STRATEGIES))
        self.assertEqual(set(codes), {s["group"] for s in st.STRATEGIES})   # every group is used

    def test_every_leg_rule_parses(self):
        n_legs = 0
        for s in st.STRATEGIES:
            for leg in s["legs"]:
                n_legs += 1
                self.assertIn(leg["kind"], ("STOCK", "OPTION", "FUTURE", "TRS"))
                self.assertIn(leg["side"], ("BUY", "SELL"))
                self.assertGreater(Decimal(str(leg.get("size", 1))), 0)
                self.assertIn(st.order_side(leg["kind"], leg["side"]), ("BUY", "SELL", "SELL_SHORT", "BTO", "STO", "RECEIVE", "PAY"))
                if leg["kind"] == "OPTION":
                    self.assertIn(leg["type"], ("C", "P"))
                    mode, v = st.parse_rule(leg["strike"])
                    self.assertIn(mode, ("ATM", "PCT", "ZERO_COST"))
                    if mode == "PCT":
                        self.assertTrue(-60 < v < 60)
                    if "tenor" in leg:
                        self.assertIsInstance(leg["tenor"], int)
                        self.assertGreater(leg["tenor"], 0)
                else:
                    self.assertNotIn("strike", leg)
        self.assertEqual(134, n_legs)   # the per-group hand count of test_leg_count_by_group sums to 134

    def test_leg_count_by_group(self):
        # per group, by hand from the catalogue text (each template once, in its own group field):
        # long_hedge: PROTECTIVE_PUT 2, _TIGHT 2, _ATM 2, TAIL_HEDGE 2, COLLAR 3, ZERO_COST_COLLAR 3, PUT_SPREAD_HEDGE 3 = 17
        # short_hedge: PROTECTED_SHORT 2, _ATM 2, SHORT_TAIL_HEDGE 2, SHORT_COLLAR 3, CALL_SPREAD_HEDGE 3 = 12
        # directional: LONG_PLUS_CALL 2, SHORT_PLUS_PUT 2, LONG_CALL 1, LONG_PUT 1, SYNTHETIC_LONG 2, SYNTHETIC_SHORT 2, RISK_REVERSAL 2 = 12
        # income: COVERED_CALL 2, PARTIAL 2, CASH_SECURED_PUT 1, COVERED_STRANGLE 3, JADE_LIZARD 3 = 11
        # bull: BULL_CALL 2, BULL_PUT 2 = 4;  bear: BEAR_PUT 2, BEAR_CALL 2 = 4
        # vol: STRADDLE 2, STRANGLE 2, SHORT_STRADDLE 2, SHORT_STRANGLE 2, IRON_CONDOR 4, REVERSE 4, IRON_BUTTERFLY 4, CALL_BUTTERFLY 3, BROKEN_WING 3 = 26
        # calendar: 2 + 2 + 2 = 6;  trs: 1 + 1 + 2 + 2 + 3 + 2 = 11;  futures: LONG_FUTURES_HEDGE 2, LONG 1, SHORT 1, LONG_PROTECTED 2, SHORT_PROTECTED 2, BETA 2 = 10
        # rv: 2 + 2 + 3 = 7;  adv: PUT_SPREAD_COLLAR 4, CALL_RATIO_SPREAD 2, STOCK_REPAIR 3, SEAGULL 3, STRAP 2 = 14
        # total 17+12+12+11+4+4+26+6+11+10+7+14 = 134 … minus nothing: the sum over all 60 entries must equal this
        expected = {"long_hedge": 17, "short_hedge": 12, "directional": 12, "income": 11, "bull": 4, "bear": 4, "vol": 26, "calendar": 6, "trs": 11,
                    "futures": 10, "rv": 7, "adv": 14}
        got: Dict[str, int] = {}
        for s in st.STRATEGIES:
            got[s["group"]] = got.get(s["group"], 0) + len(s["legs"])
        self.assertEqual(expected, got)
        self.assertEqual(134, sum(expected.values()))
        self.assertEqual(134, sum(len(s["legs"]) for s in st.STRATEGIES))

    def test_module_never_imports_the_simulation(self):
        self.assertNotIn("finsim.engines.playbook", sys.modules)
        self.assertNotIn("finsim.world", sys.modules)
        from finsim.terminal import strategies_catalogue
        for path in (st.__file__, strategies_catalogue.__file__):
            with open(path, "r", encoding="utf-8") as fh:
                src = fh.read()
            self.assertIsNone(re.search(r"^\s*(from|import)\s+\S*(engines|world|domain)", src, re.M), path)   # no import of the simulation anywhere
            self.assertNotIn("import playbook", src)


# ===== pure resolution (§13.16 / §19.22) ==========================================================================================
class ResolutionTests(unittest.TestCase):
    def test_resolve_strike_pins_19_22(self):
        # spot 769.32, listed 700 … 840 step 5
        self.assertEqual(D(715), st.resolve_strike("pct:-7", SPOT, STEP5))       # target 769.32 × 0.93 = 715.4676 -> 715 (0.4676 away; 720 is 4.5324)
        self.assertEqual(D(770), st.resolve_strike("ATM", SPOT, STEP5))          # 769.32 -> 770 (0.68) over 765 (4.32)
        self.assertEqual(D(840), st.resolve_strike("pct:+10", SPOT, STEP5))      # 846.252 -> 840, the highest listed
        self.assertEqual(D(730), st.resolve_strike("-5", SPOT, STEP5))           # an override |−5| < 60 is a percent: 730.854 -> 730
        self.assertEqual(D(800), st.resolve_strike("800", SPOT, STEP5))          # |800| >= 60: absolute -> 800
        self.assertEqual(D(800), st.resolve_strike(800, SPOT, STEP5))            # a number works too
        self.assertEqual(D(770), st.resolve_strike("ATM", D("772.5"), STEP5))   # a tie (770 and 775 both 2.5 away) goes to the LOWER strike
        self.assertEqual(D(840), st.resolve_strike("59", SPOT, STEP5))           # 59 is still a percent: 769.32 × 1.59 = 1223.22 -> 840
        self.assertEqual(D(700), st.resolve_strike("60", SPOT, STEP5))           # 60 is an absolute strike -> nearest listed 700
        self.assertEqual(("PCT", D("-7")), st.parse_rule("pct:-7"))
        self.assertEqual(("ZERO_COST", None), st.parse_rule("zero_cost"))
        with self.assertRaises(ValueError):
            st.resolve_strike("ATM", SPOT, [])
        with self.assertRaises(ValueError):
            st.resolve_strike("foo", SPOT, STEP5)
        with self.assertRaises(ValueError):
            st.resolve_strike("zero_cost", SPOT, STEP5)

    def test_zero_cost_call(self):
        E = "2027-02-19"
        rows = [row("SPY", E, "C", 760, "14.00", "14.20"), row("SPY", E, "C", 780, "9.80", "10.00"), row("SPY", E, "C", 790, "7.20", "7.40"),
                row("SPY", E, "C", 800, "5.10", "5.30"), row("SPY", E, "C", 810, None, "3.00"), row("SPY", E, "P", 750, "6.00", "6.20")]
        # put ask 7.00: candidates above spot 769.32 with a bid: 780 (|9.80 − 7.00| = 2.80), 790 (0.20), 800 (1.90); 810 has no bid; 760 is below spot
        self.assertEqual(D(790), st.zero_cost_call(rows, D("7.00"), SPOT))
        self.assertEqual(D(800), st.zero_cost_call(rows, D("2.00"), SPOT))      # |5.10 − 2.00| = 3.10 beats 790's 5.20
        self.assertEqual(D(780), st.zero_cost_call(rows, D("8.50"), SPOT))      # 780 and 790 both 1.30 away: the lower strike
        self.assertIsNone(st.zero_cost_call(rows, D("7.00"), D("900")))        # nothing listed above spot
        self.assertIsNone(st.zero_cost_call([r for r in rows if r.right == "P"], D("7.00"), SPOT))

    def test_expiry_for_tenor(self):
        listed = ["2026-11-20", "2027-01-15", "2027-02-19"]
        # today 2026-10-20 + 3 months = 2027-01-20: 2027-01-15 is before it -> 2027-02-19
        self.assertEqual(date(2027, 2, 19), st.expiry_for_tenor(listed, TODAY, 3))
        self.assertEqual(date(2027, 1, 22), st.expiry_for_tenor(listed + ["2027-01-22"], TODAY, 3))     # the 2027-01-22 variant
        self.assertEqual(date(2026, 11, 20), st.expiry_for_tenor(listed, TODAY, 1))                     # 2026-11-20 >= 2026-11-20 exactly
        self.assertIsNone(st.expiry_for_tenor(listed, TODAY, 12))                                       # nothing on/after 2027-10-20
        self.assertEqual(date(2026, 2, 28), st.add_months(date(2026, 1, 31), 1))                        # day clamped to the month end
        self.assertEqual(date(2027, 1, 20), st.add_months(TODAY, 3))
        self.assertEqual(date(2026, 3, 20), st.expiry_for_tenor([date(2026, 2, 27), date(2026, 3, 20)], date(2026, 1, 31), 1))   # target 2026-02-28

    def test_size_legs_pins_19_22(self):
        u = 1000
        pp = st.size_legs(st.BY_KEY["PROTECTIVE_PUT"], u, SPOT)
        self.assertEqual([1000, 10], [l.qty for l in pp])                                  # 1,000 shares + round(1000 × 1 / 100) = 10 puts
        self.assertEqual(("STOCK", "OPTION"), (pp[0].kind, pp[1].kind))
        self.assertEqual([1000, 5], [l.qty for l in st.size_legs(st.BY_KEY["PARTIAL_COVERED_CALL"], u, SPOT)])   # size 0.5: round(1000 × 0.5 / 100) = 5
        self.assertEqual([10, 20], [l.qty for l in st.size_legs(st.BY_KEY["CALL_RATIO_SPREAD"], u, SPOT)])       # size 2.0 on the sold calls
        beta = st.size_legs(st.BY_KEY["BETA_HEDGED_LONG"], u, SPOT, D("7754.25"), D(50))
        # n = max(1, round(1,000 × 769.32 / (7,754.25 × 50))) = round(769,320 / 387,712.5) = round(1.98425) = 2
        self.assertEqual([1000, 2], [l.qty for l in beta])
        self.assertEqual(("FUTURE", "SELL"), (beta[1].kind, beta[1].side))
        self.assertIsNone(st.size_legs(st.BY_KEY["BETA_HEDGED_LONG"], u, SPOT)[1].qty)   # no futures price -> no size
        self.assertEqual([1000], [l.qty for l in st.size_legs(st.BY_KEY["SYNTHETIC_LONG_TRS"], u, SPOT)])
        self.assertEqual([1], [l.qty for l in st.size_legs(st.BY_KEY["LONG_CALL"], 50, SPOT)])         # round(0.5) = 0 -> at least 1 contract
        self.assertEqual([150, 2], [l.qty for l in st.size_legs(st.BY_KEY["PROTECTIVE_PUT"], 150, SPOT)])   # 150 / 100 = 1.5 contracts -> HALF_EVEN 2
        with self.assertRaises(ValueError):
            st.size_legs(st.BY_KEY["LONG_CALL"], 0, SPOT)

    def test_size_legs_half_even(self):
        # 250 units × 1 / 100 = 2.5 contracts -> HALF_EVEN 2; 350 -> 3.5 -> 4
        self.assertEqual(2, st.size_legs(st.BY_KEY["LONG_CALL"], 250, SPOT)[0].qty)
        self.assertEqual(4, st.size_legs(st.BY_KEY["LONG_CALL"], 350, SPOT)[0].qty)

    def test_fill_mode(self):
        self.assertEqual("AON", st.fill_mode_for(st.BY_KEY["IRON_CONDOR"]))
        self.assertEqual("SEQUENTIAL", st.fill_mode_for(st.BY_KEY["PROTECTIVE_PUT"]))
        self.assertEqual("AON", st.fill_mode_for(st.BY_KEY["CALENDAR_SPREAD"]))        # two expiries, one underlying, options only
        self.assertEqual("SEQUENTIAL", st.fill_mode_for(st.BY_KEY["RV_PROTECTED"]))     # a second name
        self.assertEqual("SEQUENTIAL", st.fill_mode_for(st.BY_KEY["LONG_FUTURES"]))
        self.assertEqual("SEQUENTIAL", st.fill_mode_for(st.BY_KEY["SYNTHETIC_LONG_TRS"]))
        modes = [st.fill_mode_for(s) for s in st.STRATEGIES]
        # option-only templates counted by hand: LONG_CALL, LONG_PUT, SYNTHETIC_LONG/SHORT_OPTIONS, RISK_REVERSAL, CASH_SECURED_PUT, JADE_LIZARD,
        # BULL_CALL/PUT_SPREAD, CALL_RATIO_SPREAD, BEAR_PUT/CALL_SPREAD, LONG/SHORT STRADDLE/STRANGLE (4), IRON_CONDOR, REVERSE_IRON_CONDOR,
        # IRON_BUTTERFLY, LONG_CALL_BUTTERFLY, BROKEN_WING_BUTTERFLY, CALENDAR_SPREAD, DIAGONAL_PMCC, PUT_CALENDAR, SEAGULL, STRAP = 26
        self.assertEqual(26, modes.count("AON"))
        self.assertEqual(34, modes.count("SEQUENTIAL"))

    def test_order_sides(self):
        self.assertEqual(("BUY", "SELL_SHORT"), (st.order_side("STOCK", "BUY"), st.order_side("STOCK", "SELL")))
        self.assertEqual(("BTO", "STO"), (st.order_side("OPTION", "BUY"), st.order_side("OPTION", "SELL")))
        self.assertEqual(("BUY", "SELL"), (st.order_side("FUTURE", "BUY"), st.order_side("FUTURE", "SELL")))
        self.assertEqual(("RECEIVE", "PAY"), (st.order_side("TRS", "BUY"), st.order_side("TRS", "SELL")))

    def test_package_margin_formula_13_3(self):
        E = "2026-11-20"
        condor = [{"index": 0, "side": "BUY", "right": "P", "strike": D(700), "expiry": E, "qty": 10, "est_price": D("2.10"), "mark": D("2.05")},
                  {"index": 1, "side": "SELL", "right": "P", "strike": D(730), "expiry": E, "qty": 10, "est_price": D("4.00"), "mark": D("4.05")},
                  {"index": 2, "side": "SELL", "right": "C", "strike": D(800), "expiry": E, "qty": 10, "est_price": D("3.50"), "mark": D("3.55")},
                  {"index": 3, "side": "BUY", "right": "C", "strike": D(830), "expiry": E, "qty": 10, "est_price": D("1.20"), "mark": D("1.15")}]
        req, detail = st.package_margin_c(condor, SPOT)
        # put spread 30 × 100 × 10 − (4.00 − 2.10) × 100 × 10 = 30,000 − 1,900 = 28,100; call spread 30,000 − (3.50 − 1.20) × 1,000 = 27,700; max = 28,100
        self.assertEqual(28_100_00, req)
        # the detail lists the short legs in (expiry, right, strike) order: the call spread first, then the put spread
        self.assertEqual([("vertical spread", 27_700_00), ("vertical spread", 28_100_00)], [(d["rule"], d["requirement_c"]) for d in detail])
        self.assertEqual(("700", "30", 1_900_00), (detail[1]["with_strike"], detail[1]["width"], detail[1]["credit_c"]))
        self.assertEqual(("830", "30", 2_300_00), (detail[0]["with_strike"], detail[0]["width"], detail[0]["credit_c"]))
        # naked put K 715, 10 contracts, mark 12.30 (premium 1,230.00 per contract), S 769.32:
        #   a = 1,230 + 0.20 × 769.32 × 100 − (769.32 − 715) × 100 = 1,230 + 15,386.40 − 5,432.00 = 11,184.40
        #   b = 1,230 + 0.10 × 715 × 100 = 1,230 + 7,150 = 8,380.00  -> max 11,184.40 × 10 = 111,844.00
        naked = [{"index": 0, "side": "SELL", "right": "P", "strike": D(715), "expiry": E, "qty": 10, "est_price": D("12.20"), "mark": D("12.30")}]
        req, detail = st.package_margin_c(naked, SPOT)
        self.assertEqual(111_844_00, req)
        self.assertEqual(("naked put", 10, "11184.40"), (detail[0]["rule"], detail[0]["contracts"], detail[0]["per_contract"]))
        # covered call: 1,000 shares bought in the package cover 10 short calls -> 0
        cc = [{"index": 1, "side": "SELL", "right": "C", "strike": D(845), "expiry": E, "qty": 10, "est_price": D("2.00"), "mark": D("2.05")}]
        self.assertEqual((0, "covered call"), (st.package_margin_c(cc, SPOT, long_shares=1000)[0], st.package_margin_c(cc, SPOT, long_shares=1000)[1][0]["rule"]))
        # 1×2 call ratio spread: BUY 10 C800 ask 8.00, SELL 20 C850 bid 3.00 mark 3.10. The long 800 call sits BELOW the short 850 calls, so the
        #   10 shorts it covers form a debit spread (width 0; the credit 3.00 − 8.00 is a debit, floored at 0) -> 0; the other 10 are naked:
        #   a = 310 + 0.20 × 769.32 × 100 − (850 − 769.32) × 100 = 310 + 15,386.40 − 8,068.00 = 7,628.40; b = 310 + 0.10 × 769.32 × 100 = 8,003.20
        #   -> 8,003.20 × 10 = 80,032.00; total 80,032.00
        ratio = [{"index": 0, "side": "BUY", "right": "C", "strike": D(800), "expiry": E, "qty": 10, "est_price": D("8.00"), "mark": D("7.90")},
                 {"index": 1, "side": "SELL", "right": "C", "strike": D(850), "expiry": E, "qty": 20, "est_price": D("3.00"), "mark": D("3.10")}]
        req, detail = st.package_margin_c(ratio, SPOT)
        self.assertEqual(80_032_00, req)
        self.assertEqual([("vertical spread", 10, 0), ("naked call", 10, 80_032_00)], [(d["rule"], d["contracts"], d["requirement_c"]) for d in detail])
        self.assertIn("debit spread", detail[0]["note"])
        # the same two strikes the other way round (SELL 10 C800 bid 8.00, BUY 10 C850 ask 3.00): a credit spread, width 50 × 100 × 10 = 50,000 net of
        #   the credit (8.00 − 3.00) × 1,000 = 5,000 -> 45,000.00
        credit = [{"index": 0, "side": "SELL", "right": "C", "strike": D(800), "expiry": E, "qty": 10, "est_price": D("8.00"), "mark": D("8.10")},
                  {"index": 1, "side": "BUY", "right": "C", "strike": D(850), "expiry": E, "qty": 10, "est_price": D("3.00"), "mark": D("2.90")}]
        self.assertEqual(45_000_00, st.package_margin_c(credit, SPOT)[0])
        # index option (15 % / 10 %): naked SPX call K 7800, mark 50 (premium 5,000), S 7712.16, 1 contract:
        #   a = 5,000 + 0.15 × 7712.16 × 100 − (7800 − 7712.16) × 100 = 5,000 + 115,682.40 − 8,784.00 = 111,898.40; b = 5,000 + 77,121.60 = 82,121.60
        idx = [{"index": 0, "side": "SELL", "right": "C", "strike": D(7800), "expiry": E, "qty": 1, "est_price": D("49.90"), "mark": D("50")}]
        self.assertEqual(111_898_40, st.package_margin_c(idx, D("7712.16"), index=True)[0])
        self.assertEqual((0, []), st.package_margin_c([], SPOT))


# ===== packages against a runtime with a fixture chain ================================================================================
class _RuntimeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-strategies-")
        self.rt = make_runtime(self.tmp)                                       # 2026-10-20 14:00 ET, T1 with USD 10,000,000
        rt = self.rt
        self.now = rt.clock.epoch()
        rt.cache = QuoteCache(rt.db, rt.instruments, rt.sessions, rt.clock, rt.bus, rt.log)
        rt.chains = ChainCache(rt.clock)
        st.register(rt.bus)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _spy_record(self, last="769.32"):
        self.rt.cache.put(rec("SPY", last, self.now), self.now)

    def _condor_chain(self):
        E = "2026-11-20"
        rows = [row("SPY", E, "P", 700, "2.00", "2.10", 50, 50, delta=-0.08, vega=0.40, theta=-0.05),
                row("SPY", E, "P", 730, "4.00", "4.10", 50, 50, delta=-0.15, vega=0.55, theta=-0.07),
                row("SPY", E, "P", 800, "33.00", "34.00"), row("SPY", E, "P", 830, "62.00", "63.00"),
                row("SPY", E, "C", 700, "72.00", "73.00"), row("SPY", E, "C", 730, "43.00", "44.00"),
                row("SPY", E, "C", 800, "3.50", "3.60", 5, 50, delta=0.18, vega=0.50, theta=-0.06),
                row("SPY", E, "C", 830, "1.10", "1.20", 50, 50, delta=0.07, vega=0.30, theta=-0.03)]
        return chain("SPY", SPOT, rows, self.now)

    def _hedge_chain(self):
        E = "2027-02-19"
        rows = [row("SPY", "2026-11-20", "C", 770, "20.00", "20.20"), row("SPY", "2027-01-15", "C", 770, "23.00", "23.20")]
        for k in STEP5:
            if k == 715:
                rows.append(row("SPY", E, "P", k, "12.20", "12.40", delta=-0.25, vega=0.95, theta=-0.08))
            else:
                rows.append(row("SPY", E, "P", k, "1.00", "1.10"))
        rows += [row("SPY", E, "C", 770, "25.00", "25.20"), row("SPY", E, "C", 800, "12.30", "12.50", delta=0.35, vega=1.10, theta=-0.09),
                 row("SPY", E, "C", 820, "9.00", "9.20")]
        return chain("SPY", SPOT, rows, self.now, bid="769.30", ask="769.35")

    # ---- §19.22: the AON iron condor --------------------------------------------------------------------------------------------
    def test_iron_condor_package_19_22(self):
        rt = self.rt
        rt.chains.put(self._condor_chain())
        overrides = {"0": "700", "1": "730", "2": "800", "3": "830"}      # the four §19.22 strikes (see the module note on pct rules)
        spec = st.package_legs(rt, US_EXCHANGE, "iron_condor", {"underlying": "SPY", "units": 1000, "tenor_months": 1, "strikes": overrides})
        self.assertEqual(("IRON_CONDOR", "AON", None, "2026-11-20", "delayed"), (spec.strategy_key, spec.fill_mode, spec.primary_index, spec.expiry, spec.chain_quality))
        self.assertEqual(SPOT, spec.spot)
        legs = spec.legs
        self.assertEqual(["SPY261120P00700000", "SPY261120P00730000", "SPY261120C00800000", "SPY261120C00830000"], [l.instrument_id for l in legs])
        self.assertEqual(["BTO", "STO", "STO", "BTO"], [l.side for l in legs])
        self.assertEqual([10, 10, 10, 10], [l.qty for l in legs])
        self.assertEqual([D("2.1"), D("4"), D("3.5"), D("1.2")], [l.est_price for l in legs])            # ask for buys, bid for sells
        self.assertEqual(["Cboe ask", "Cboe bid", "Cboe bid", "Cboe ask"], [l.price_basis for l in legs])
        # premiums −2.10 + 4.00 + 3.50 − 1.20 per share × 100 × 10 = −2,100 + 4,000 + 3,500 − 1,200 = +4,200.00 credit; fees 4 × 10 × 0.65 = 26.00
        self.assertEqual([-2_100_00, 4_000_00, 3_500_00, -1_200_00], [l.cash_c for l in legs])
        self.assertEqual([6_50] * 4, [l.fee_c for l in legs])
        self.assertEqual(("4200.00", 4_200_00, "26.00", "4174.00", "4200.00"), (spec.totals["net_cash"], spec.totals["net_cash_c"], spec.totals["fees"],
                                                                                 spec.totals["net_cash_after_fees"], spec.totals["net_option_premium"]))
        # pct from spot: 700/769.32 − 1 = −9.0109 %, 730 -> −5.1109 %, 800 -> +3.9878 %, 830 -> +7.8878 %
        self.assertEqual([D("-9.01"), D("-5.11"), D("3.99"), D("7.89")], [l.pct_from_spot for l in legs])
        # greeks × contracts × 100, signed: delta −0.08 × 1,000 = −80; −(−0.15 × 1,000) = +150; −(0.18 × 1,000) = −180; +70 -> −40
        self.assertEqual([D("-80.00"), D("150.00"), D("-180.00"), D("70.00")], [l.delta_shares for l in legs])
        self.assertEqual(("-40.00", "-30772.80"), (spec.totals["delta_shares"], spec.totals["dollar_delta"]))     # −40 × 769.32 = −30,772.80
        self.assertEqual(("-350.00", "50.00"), (spec.totals["vega"], spec.totals["theta"]))   # 400 − 550 − 500 + 300; −50 + 70 + 60 − 30
        # margin once for the package: put spread 30,000 − 1,900 = 28,100; call spread 30,000 − 2,300 = 27,700 -> 28,100.00
        self.assertEqual((28_100_00, "28100.00"), (spec.margin["requirement_c"], spec.margin["requirement"]))
        self.assertIsNone(spec.margin["incremental_c"])                     # WP-03 not bound: the package formula is what is shown
        self.assertEqual("28100.00", spec.totals["option_margin"])
        # payoff at the near expiry: max gain = the credit 4,200.00; max loss = 30,000 − 4,200 = 25,800.00; breakevens 730 − 4.20 and 800 + 4.20
        po = spec.payoff
        self.assertEqual(("4200.00", "-25800.00", ["725.80", "804.20"], False, False), (po["max_gain"], po["max_loss"], po["breakevens"], po["unlimited_up"], po["unlimited_down"]))
        self.assertEqual(("500.06", "1038.58"), (po["grid"]["low"], po["grid"]["high"]))       # 769.32 × 0.65 = 500.058; × 1.35 = 1038.582
        self.assertEqual(75, po["grid"]["points"])                                              # 71 grid points + the four strikes as knots
        pts = {Decimal(a): Decimal(b) for a, b in po["payoff"]}
        self.assertEqual(D("-25800.00"), pts[D(700)])                                           # −30,000 + 4,200 at the lower wing
        self.assertEqual(D("4200.00"), pts[D("769.32")])                                        # flat between 730 and 800
        self.assertEqual(D("-25800.00"), pts[D("1038.58")])                                     # upper wing: −30,000 + 4,200
        # the contract rows are not in the master yet: the preview materialises them so order.submit finds them
        self.assertEqual([l.instrument_id for l in legs], spec.missing_instruments)
        self.assertTrue(all(not l.materialised for l in legs))
        created = st.ensure_instruments(rt, spec)
        self.assertEqual(sorted(l.instrument_id for l in legs), sorted(created))
        self.assertEqual([], spec.missing_instruments)
        p700 = rt.instruments.get("SPY261120P00700000")
        self.assertEqual(("OPTION", "SPY", "2026-11-20", D(700), "P", "AMERICAN", D(100)), (p700.kind, p700.underlying_id, p700.expiry, p700.strike, p700.right, p700.style, p700.multiplier))
        self.assertEqual([], st.ensure_instruments(rt, spec))                                   # idempotent
        # what order.submit sees through _as_dict(spec)["legs"]
        d = spec.to_dict()
        self.assertEqual(("AON", True), (d["fill_mode"], d["aon"]))
        self.assertEqual([("SPY261120P00700000", "BTO", 10, "MARKET"), ("SPY261120P00730000", "STO", 10, "MARKET"), ("SPY261120C00800000", "STO", 10, "MARKET"),
                          ("SPY261120C00830000", "BTO", 10, "MARKET")], [(l["instrument_id"], l["side"], l["qty"], l["order_type"]) for l in d["legs"]])
        self.assertEqual("4200.00", d["totals"]["net_cash"])
        self.assertEqual(5, d["legs"][2]["quote"]["bid_size"])                                  # the §19.22 C800 bid_size 5 travels with the leg
        self.assertIn("AON", d["conventions"])
        # the view function answers 200 with the same shape
        status, body = st.preview_view(rt, {"account_id": US_EXCHANGE, "key": "IRON_CONDOR", "params": {"underlying": "SPY", "units": 1000, "tenor_months": 1, "strikes": overrides}})
        self.assertEqual((200, "28100.00", ["725.80", "804.20"]), (status, body["margin"]["requirement"], body["payoff"]["breakevens"]))

    # ---- a sequential protective put and a zero-cost collar off the same chain -----------------------------------------------------------
    def test_protective_put_sequential_package(self):
        rt = self.rt
        self._spy_record()
        rt.chains.put(self._hedge_chain())
        spec = st.package_legs(rt, US_EXCHANGE, "PROTECTIVE_PUT", {"underlying": "SPY", "units": 1000, "tenor_months": 3})
        self.assertEqual(("SEQUENTIAL", 0, "2027-02-19"), (spec.fill_mode, spec.primary_index, spec.expiry))
        stock, put = spec.legs
        # the chain is fresh (30 s) and shows the underlying 769.30 / 769.35: a buy pays the ask
        self.assertEqual(("SPY", "BUY", 1000, D("769.35"), "Cboe underlying ask", -769_350_00, 5_00), (stock.instrument_id, stock.side, stock.qty, stock.est_price,
                                                                                                   stock.price_basis, stock.cash_c, stock.fee_c))   # fee 1,000 × 0.005 = 5.00
        # pct:-7 -> 715.4676 -> 715 at the 2027-02-19 expiry (2026-10-20 + 3 months = 2027-01-20; 2027-01-15 is too early)
        self.assertEqual(("SPY270219P00715000", "BTO", 10, D("12.4"), -12_400_00, 6_50, D(715), "2027-02-19", 3), (put.instrument_id, put.side, put.qty, put.est_price,
                                                                                                                     put.cash_c, put.fee_c, put.strike, put.expiry, put.tenor_months))
        self.assertEqual(D("-7.06"), put.pct_from_spot)                                   # 715 / 769.32 − 1 = −7.0608 %
        self.assertEqual(("-781750.00", "11.50", "-12400.00", "750.00", "950.00", "-80.00"), (spec.totals["net_cash"], spec.totals["fees"], spec.totals["net_option_premium"],
                                                                                              spec.totals["delta_shares"], spec.totals["vega"], spec.totals["theta"]))
        # payoff: unlimited above (stock without a sold call); below 715 the loss is flat: −(769.35 − 715) × 1,000 − 12,400 = −54,350 − 12,400 = −66,750.00;
        # breakeven where (ST − 769.35) × 1,000 = 12,400 -> 781.75 (grid knots 776.91 -> −4,840 and 784.71 -> +2,960: 776.91 + 7.80 × 4,840 / 7,800 = 781.75)
        po = spec.payoff
        self.assertEqual((None, "-66750.00", ["781.75"], True, False), (po["max_gain"], po["max_loss"], po["breakevens"], po["unlimited_up"], po["unlimited_down"]))
        self.assertEqual((0, "0.00"), (spec.margin["requirement_c"], spec.margin["requirement"]))
        self.assertEqual("delayed", stock.quote["quality"])
        d = spec.to_dict()
        self.assertEqual([("SPY", "BUY", 1000), ("SPY270219P00715000", "BTO", 10)], [(l["instrument_id"], l["side"], l["qty"]) for l in d["legs"]])

    def test_zero_cost_collar_resolves_the_call_whose_bid_pays_for_the_put(self):
        rt = self.rt
        self._spy_record()
        rt.chains.put(self._hedge_chain())
        spec = st.package_legs(rt, US_EXCHANGE, "ZERO_COST_COLLAR", {"underlying": "SPY", "units": 1000})      # defaults: tenor 3
        stock, put, call = spec.legs
        # the put's ask is 12.40; calls above spot 769.32 at 2027-02-19: 770 bid 25.00 (12.60 away), 800 bid 12.30 (0.10), 820 bid 9.00 (3.40) -> 800
        self.assertEqual(("SPY270219C00800000", "STO", 10, D("12.3"), 12_300_00, D(800)), (call.instrument_id, call.side, call.qty, call.est_price, call.cash_c, call.strike))
        self.assertEqual(("-769450.00", "18.00", "-100.00"), (spec.totals["net_cash"], spec.totals["fees"], spec.totals["net_option_premium"]))   # −769,350 − 12,400 + 12,300; fees 5 + 6.50 + 6.50
        # capped above 800: (800 − 769.35) × 1,000 − 100 = 30,550.00; floored below 715: −54,350 − 100 = −54,450.00;
        # breakeven: (ST − 769.35) × 1,000 = 100 -> 769.45 (knots 769.32 -> −130 and 776.91 -> +7,460: 769.32 + 7.59 × 130 / 7,590 = 769.45)
        po = spec.payoff
        self.assertEqual(("30550.00", "-54450.00", ["769.45"], False, False), (po["max_gain"], po["max_loss"], po["breakevens"], po["unlimited_up"], po["unlimited_down"]))
        self.assertEqual((0, "covered call"), (spec.margin["requirement_c"], spec.margin["detail"][0]["rule"]))   # the 1,000 shares cover the 10 calls
        self.assertEqual([], spec.warnings)

    # ---- a beta-hedged long against the front ES -------------------------------------------------------------------------------------
    def test_beta_hedged_long_sizes_two_es_from_the_front_month(self):
        rt = self.rt
        self._spy_record()                                                        # no chain: the stock prices off the Yahoo last
        front = st.front_month(rt, "ES")
        self.assertEqual(("ESZ26", "2026-12-18"), (front.instrument_id, front.expiry))     # third Friday of December 2026 is the front month on 2026-10-20
        rt.cache.put(rec("ESZ26", "7754.25", self.now, group="CME_EQUITY_INDEX"), self.now)
        spec = st.package_legs(rt, US_EXCHANGE, "BETA_HEDGED_LONG", {"underlying": "SPY", "units": 1000})
        stock, fut = spec.legs
        self.assertEqual(("SPY", "BUY", 1000, D("769.32"), "last", -769_320_00, 5_00), (stock.instrument_id, stock.side, stock.qty, stock.est_price, stock.price_basis,
                                                                                         stock.cash_c, stock.fee_c))
        # n = round(1,000 × 769.32 / (7,754.25 × 50)) = round(1.98425) = 2; notional 2 × 50 × 7,754.25 = 775,425.00; IM 2 × 17,500 = 35,000.00; fee 2 × 2.25 = 4.50
        self.assertEqual(("ESZ26", "SELL", 2, D("7754.25"), 0, 4_50, 775_425_00, 35_000_00, D(50)), (fut.instrument_id, fut.side, fut.qty, fut.est_price, fut.cash_c, fut.fee_c,
                                                                                                      fut.notional_c, fut.margin_c, fut.multiplier))
        self.assertEqual(D("-1007.94"), fut.delta_shares)                              # −775,425 / 769.32 = −1,007.9395 shares of SPY
        self.assertEqual(("-7.94", "35000.00", "-769320.00"), (spec.totals["delta_shares"], spec.totals["futures_margin"], spec.totals["net_cash"]))
        # the old _payoff flags: a bought linear leg without a sold call is unlimited above, a sold linear leg (the futures) without a bought
        # call is unlimited below — the pair loses without limit when the stock lags the index
        self.assertEqual(("SEQUENTIAL", 0, True, True), (spec.fill_mode, spec.primary_index, spec.payoff["unlimited_up"], spec.payoff["unlimited_down"]))
        self.assertEqual("delayed", fut.quote["quality"])
        self.assertIsNone(spec.margin["incremental_c"])
        # the §19.22 pin again through the package with the chain's spot
        self.assertEqual(2, st.size_legs(st.BY_KEY["BETA_HEDGED_LONG"], 1000, SPOT, D("7754.25"), front.multiplier)[1].qty)

    # ---- TRS templates: PB account, eligibility, the swaps hook ---------------------------------------------------------------------------
    def test_trs_template_unavailable_outside_an_eligible_pb_account(self):
        rt = self.rt
        self._spy_record()
        cat = st.catalogue_view(rt, US_EXCHANGE)
        by_key = {s["key"]: s for s in cat["strategies"]}
        self.assertEqual((False, "NOT ELIGIBLE / not a PB account"), (by_key["SYNTHETIC_LONG_TRS"]["available"], by_key["SYNTHETIC_LONG_TRS"]["reason"]))
        self.assertEqual(54, sum(1 for s in cat["strategies"] if s["available"]))        # 60 − the 6 TRS templates
        self.assertTrue(by_key["PROTECTIVE_PUT"]["available"])
        with self.assertRaises(RejectError) as cm:
            st.package_legs(rt, US_EXCHANGE, "SYNTHETIC_LONG_TRS", {"underlying": "SPY"})
        self.assertEqual("NOT_AVAILABLE", cm.exception.code)
        self.assertIn("not a PB account", cm.exception.message)
        # a PB account of a lifetime below the PB/OTC threshold (T1 NAV 10,000,000 < 25,000,000)
        pb = f"{T1}:PB_GS"
        with rt.db.writer() as tx:
            books._insert_account(tx.conn, tx.ts, "T", T1, "PB_GS", "Goldman Sachs (PB)", "PB", 60, broker="GS")
        reason = {s["key"]: s["reason"] for s in st.catalogue_view(rt, pb)["strategies"]}["SYNTHETIC_LONG_TRS"]
        self.assertEqual("NOT ELIGIBLE / NAV 10,000,000.00 is below the PB/OTC threshold 25,000,000.00", reason)
        # eligible lifetime (threshold lowered to 5,000,000; ECP floor 10,000,000 met exactly) but no swaps module yet
        with rt.db.writer() as tx:
            set_setting(tx.conn, "pb_otc_threshold_c", 5_000_000_00)
        reason = {s["key"]: s["reason"] for s in st.catalogue_view(rt, pb)["strategies"]}["SYNTHETIC_LONG_TRS"]
        self.assertEqual("NOT AVAILABLE / TRS preview needs the swaps module (WP-17 / WP-26): not in this build", reason)
        # with a swaps module bound the leg is priced at the underlying's level with the PB's dealt spread from otc.preview
        seen = {}

        def fake_preview(services, account_id, kind, terms):
            seen.update({"account_id": account_id, "kind": kind, "terms": dict(terms)})
            return {"dealt_spread_bps": "60", "notional": "769320.00", "im": "76932.00", "margin_mode": "CTM"}
        rt.swaps = SimpleNamespace(preview=fake_preview)
        self.assertTrue({s["key"]: s["available"] for s in st.catalogue_view(rt, pb)["strategies"]}["SYNTHETIC_LONG_TRS"])
        spec = st.package_legs(rt, pb, "SYNTHETIC_LONG_TRS", {"underlying": "SPY", "units": 1000})
        leg = spec.legs[0]
        self.assertEqual(("TRS", None, "RECEIVE", 1000, D("769.32"), 769_320_00, 0, "otc.book"), (leg.kind, leg.instrument_id, leg.side, leg.qty, leg.est_price, leg.notional_c,
                                                                                                   leg.cash_c, leg.booking))
        self.assertEqual({"account_id": pb, "kind": "TRS", "terms": {"underlying_id": "SPY", "units": 1000, "receiver": True, "tenor_months": 3, "reset_months": 1}}, seen)
        self.assertEqual("60", leg.otc_preview["dealt_spread_bps"])
        self.assertEqual(("SEQUENTIAL", None), (spec.fill_mode, spec.primary_index))
        self.assertTrue(any("otc.book" in w for w in spec.warnings))
        # linear payoff from the reset price: at the lowest knot 500.06 the loss is (500.06 − 769.32) × 1,000 = −269,260.00; unlimited above
        self.assertEqual((None, "-269260.00", True, False), (spec.payoff["max_gain"], spec.payoff["max_loss"], spec.payoff["unlimited_up"], spec.payoff["unlimited_down"]))

    # ---- the chain is not cached: 202 while it loads; a stale chain is used and refreshed ----------------------------------------------------
    def test_preview_view_answers_202_while_the_chain_loads(self):
        rt = self.rt
        self._spy_record()
        status, body = st.preview_view(rt, {"account_id": US_EXCHANGE, "key": "IRON_CONDOR", "params": {"underlying": "SPY"}})
        self.assertEqual((202, "fetching", "SPY", 1500), (status, body["status"], body["underlying"], body["retry_ms"]))
        self.assertEqual(["SPY"], rt.chains.pending())                                   # the fetch is queued once (single flight)
        with self.assertRaises(st.ChainPending) as cm:
            st.package_legs(rt, US_EXCHANGE, "IRON_CONDOR", {"underlying": "SPY"})
        self.assertEqual(("CHAIN_PENDING", 409), (cm.exception.code, cm.exception.http))
        self.assertEqual(["SPY"], rt.chains.pending())
        # a stale snapshot (fetched 600 s ago) still prices the preview, labelled stale, and a refresh is requested
        self.assertEqual("SPY", rt.chains.take_pending())                                # the worker takes the request …
        snap = self._condor_chain()
        snap.fetched_at = self.now - 600
        rt.chains.put(snap)                                                              # … and answers it (here: an old snapshot)
        self.assertEqual([], rt.chains.pending())
        status, body = st.preview_view(rt, {"account_id": US_EXCHANGE, "key": "IRON_CONDOR",
                                            "params": {"underlying": "SPY", "units": 1000, "tenor_months": 1, "strikes": {"0": "700", "1": "730", "2": "800", "3": "830"}}})
        self.assertEqual((200, "stale", "4200.00"), (status, body["chain_quality"], body["totals"]["net_cash"]))
        self.assertTrue(any("stale" in w for w in body["warnings"]))
        self.assertEqual(["SPY"], rt.chains.pending())

    # ---- the catalogue per account / instrument, and the validation codes ----------------------------------------------------------------
    def test_catalogue_view_reasons_and_validation(self):
        rt = self.rt
        plain = st.catalogue_view()
        self.assertEqual((60, 12, 60), (plain["count"], len(plain["groups"]), sum(1 for s in plain["strategies"] if s["available"])))
        self.assertEqual(["long_hedge", "Long stock + hedge"], plain["groups"][0])
        self.assertEqual({"key", "group", "title", "simple", "thesis", "best_when", "risk", "legs", "group_label", "fill_mode", "available", "reason"},
                         set(plain["strategies"][1]))                                        # PROTECTIVE_PUT_TIGHT has no notes
        treasury = st.catalogue_view(rt, TREASURY)
        self.assertEqual(0, sum(1 for s in treasury["strategies"] if s["available"]))
        self.assertIn("Treasury never trades", treasury["strategies"][0]["reason"])
        spx = {s["key"]: s for s in st.catalogue_view(rt, US_EXCHANGE, "SPX")["strategies"]}
        self.assertFalse(spx["PROTECTIVE_PUT"]["available"])
        self.assertIn("is an index", spx["PROTECTIVE_PUT"]["reason"])
        self.assertTrue(spx["LONG_CALL"]["available"])                                   # index options are fine
        self.assertTrue(spx["LONG_FUTURES"]["available"])
        self.assertFalse(spx["BETA_HEDGED_LONG"]["available"])                           # its stock leg needs shares
        with self.assertRaises(RejectError) as cm:
            st.catalogue_view(rt, US_EXCHANGE, "NOPE")
        self.assertEqual("INSTRUMENT_UNKNOWN", cm.exception.code)
        with self.assertRaises(RejectError) as cm:
            st.catalogue_view(rt, "T9:US_EXCHANGE")
        self.assertEqual("ACCOUNT_UNKNOWN", cm.exception.code)
        with self.assertRaises(RejectError) as cm:
            st.package_legs(rt, US_EXCHANGE, "NOPE", {})
        self.assertEqual("UNKNOWN_STRATEGY", cm.exception.code)
        with self.assertRaises(RejectError) as cm:
            st.package_legs(rt, US_EXCHANGE, "LONG_CALL", {"underlying": "SPY", "units": 0})
        self.assertEqual("UNITS", cm.exception.code)
        with self.assertRaises(RejectError) as cm:
            st.package_legs(rt, US_EXCHANGE, "LONG_CALL", {"underlying": "SPY", "tenor_months": "x"})
        self.assertEqual("TENOR", cm.exception.code)
        with self.assertRaises(RejectError) as cm:
            st.package_legs(rt, US_EXCHANGE, "LONG_CALL", {"underlying": "NOPE"})
        self.assertEqual("INSTRUMENT_UNKNOWN", cm.exception.code)
        with self.assertRaises(RejectError) as cm:
            st.package_legs(rt, TREASURY, "LONG_CALL", {"underlying": "SPY"})
        self.assertEqual("NOT_AVAILABLE", cm.exception.code)
        # no listed expiry far enough out -> NOT_AVAILABLE with the reason, never a guessed expiry
        rt.chains.put(self._condor_chain())                                              # only 2026-11-20 listed
        with self.assertRaises(RejectError) as cm:
            st.package_legs(rt, US_EXCHANGE, "LONG_CALL", {"underlying": "SPY", "tenor_months": 3})
        self.assertEqual("NOT_AVAILABLE", cm.exception.code)
        self.assertIn("no listed expiry on or after 2027-01-20", cm.exception.message)


if __name__ == "__main__":
    unittest.main()
