"""Phase 3 — listed options: pricing, vol surface, chains, trading, margin, exercise, assignment, expiration,
strategies, splits, P&L, replay and the deterministic definition-of-done scenario."""
import math
import unittest
from datetime import date

from helpers import make_world, assert_ledger_invariants
from finsim.engines import options_pricing as op
from finsim.engines.options import MULTIPLIER, contract_id, third_friday
from finsim.engines.vol import VolSurfaceModel
from finsim.money import D
from finsim.store import EventStore
from finsim.world import CommandError, World


def atm_contract(w, under, otype="C", expiry_index=0, offset=0):
    ch = w.options.chain(under)
    exp = ch["expiries"][expiry_index]
    ch = w.options.chain(under, exp)
    rows = ch["rows"]
    i = min(range(len(rows)), key=lambda j: abs(rows[j]["strike"] - ch["level"]))
    return rows[max(0, min(len(rows) - 1, i + offset))][otype]["id"]


def strikes_of(w, under, expiry=None):
    ch = w.options.chain(under, expiry)
    return ch, [r["strike"] for r in ch["rows"]]


class PricingTest(unittest.TestCase):
    def test_put_call_parity_and_bounds(self):
        S, K, T, r, q, s = 100.0, 95.0, 0.5, 0.04, 0.01, 0.3
        c, p = op.bsm("C", S, K, T, r, q, s), op.bsm("P", S, K, T, r, q, s)
        self.assertAlmostEqual(c - p, S * math.exp(-q * T) - K * math.exp(-r * T), places=9)
        self.assertTrue(op.bounds_ok("C", "EUROPEAN", S, K, T, r, q, c))
        self.assertTrue(op.bounds_ok("P", "EUROPEAN", S, K, T, r, q, p))
        self.assertEqual(op.bsm("C", S, K, 0.0, r, q, s), 5.0)

    def test_american_vs_european(self):
        S, K, T, r, q, s = 100.0, 110.0, 1.0, 0.05, 0.0, 0.25
        self.assertGreater(op.crr_american("P", S, K, T, r, q, s), op.bsm("P", S, K, T, r, q, s))
        self.assertAlmostEqual(op.crr_american("C", S, K, T, r, 0.0, s), op.bsm("C", S, K, T, r, 0.0, s), places=2)
        self.assertGreaterEqual(op.crr_american("P", 100.0, 150.0, 0.5, 0.05, 0.0, 0.2), 50.0 - 1e-9)

    def test_greeks_match_finite_differences(self):
        S, K, T, r, q, s = 100.0, 100.0, 0.25, 0.03, 0.02, 0.35
        g = op.greeks("C", "EUROPEAN", S, K, T, r, q, s)
        h = 0.01
        fd_delta = (op.bsm("C", S + h, K, T, r, q, s) - op.bsm("C", S - h, K, T, r, q, s)) / (2 * h)
        fd_gamma = (op.bsm("C", S + h, K, T, r, q, s) - 2 * op.bsm("C", S, K, T, r, q, s) + op.bsm("C", S - h, K, T, r, q, s)) / (h * h)
        fd_vega = (op.bsm("C", S, K, T, r, q, s + 0.005) - op.bsm("C", S, K, T, r, q, s - 0.005)) / (2 * 0.005) / 100
        self.assertAlmostEqual(g["delta"], fd_delta, places=5)
        self.assertAlmostEqual(g["gamma"], fd_gamma, places=4)
        self.assertAlmostEqual(g["vega"], fd_vega, places=5)
        self.assertLess(g["theta"], 0)
        ga = op.greeks("P", "AMERICAN", S, K, T, r, q, s)
        self.assertAlmostEqual(ga["delta"], g["delta"] - math.exp(-q * T), places=1)
        self.assertGreater(ga["gamma"], 0)
        self.assertGreater(ga["vega"], 0)

    def test_implied_vol_roundtrip(self):
        px = op.bsm("P", 50.0, 55.0, 0.4, 0.04, 0.0, 0.42)
        self.assertAlmostEqual(op.implied_vol("P", "EUROPEAN", 50.0, 55.0, 0.4, 0.04, 0.0, px), 0.42, places=4)


class VolSurfaceTest(unittest.TestCase):
    def test_skew_term_and_stress(self):
        m = VolSurfaceModel(7)
        m.init_underlying("X", 0.30)
        F, T = 100.0, 30 / 365
        self.assertGreater(m.iv("X", 85, F, T), m.iv("X", 100, F, T))       # puts richer
        self.assertGreater(m.iv("X", 100, F, T), m.iv("X", 115, F, T))
        self.assertGreater(m.atm_for_tenor("X", 1.0), m.atm_for_tenor("X", T))  # upward term structure at rest
        calm = [m.step(date(2026, 1, 1 + i), "X", 0.30, 0.28, 18.0, 0.0, "NORMAL_GROWTH")["atm"] for i in range(10)]
        for a, b in zip(calm, calm[1:]):
            self.assertLess(abs(b / a - 1), 0.06, "IV is persistent day to day")
        stress = m.step(date(2026, 2, 1), "X", 0.30, 0.60, 45.0, 0.6, "LIQUIDITY_STRESS")
        self.assertGreater(stress["atm"], calm[-1] * 1.15)
        for i in range(15):
            stress = m.step(date(2026, 2, 2 + i), "X", 0.30, 0.60, 45.0, 0.0, "LIQUIDITY_STRESS")
        self.assertLess(stress["skew"], -0.30, "skew steepens in stress")
        self.assertLess(stress["term"], 0, "term structure inverts in stress")
        self.assertLess(m.atm_for_tenor("X", 1.0), m.atm_for_tenor("X", T))
        m2 = VolSurfaceModel(7)
        m2.init_underlying("X", 0.30)
        self.assertEqual(m2.step(date(2026, 1, 1), "X", 0.30, 0.28, 18.0, 0.0, "NORMAL_GROWTH")["atm"], calm[0], "deterministic")


class ChainTest(unittest.TestCase):
    def test_listings_are_bounded_and_deterministic(self):
        w, pf, store = make_world()
        exps = w.options.expiries(w.current_date)
        self.assertEqual(len(exps), 6)
        for e in exps:
            tf = third_friday(e.year, e.month)
            self.assertLessEqual((tf - e).days, 3)
            self.assertTrue(w.calendar.is_business_day(e))
        ch = w.options.chain("NVDA")
        self.assertEqual(len(ch["rows"]), 17)
        self.assertTrue(all("C" in r and "P" in r for r in ch["rows"]))
        self.assertEqual(ch["style"], "AMERICAN/PHYSICAL")
        idx = w.options.chain("SPX")
        self.assertEqual(idx["style"], "EUROPEAN/CASH")
        self.assertAlmostEqual(idx["level"], float(w.market.last_bar("SPY").close) * 10, places=6)
        n = sum(1 for s in w.securities.values() if s.is_option)
        self.assertEqual(w.options.ensure_listings(w.current_date), 0)
        self.assertEqual(n, sum(1 for s in w.securities.values() if s.is_option))
        # quotes: bid < mid < ask, IV smile, arbitrage bounds
        for r in ch["rows"]:
            for t in ("C", "P"):
                q = r[t]
                self.assertLess(q["bid"], q["ask"])
                sec = w.securities[q["id"]]
                self.assertTrue(w.options.quote(sec)["bounds_ok"], q["id"])
        low, high = ch["rows"][0], ch["rows"][-1]
        self.assertGreater(low["P"]["iv"], high["C"]["iv"])
        # same seed -> same chain and quotes
        w2, _, _ = make_world()
        ch2 = w2.options.chain("NVDA")
        self.assertEqual([r["C"]["bid"] for r in ch["rows"]], [r["C"]["bid"] for r in ch2["rows"]])
        self.assertNotIn("BAC-PL", w.options.optionable())   # preferreds and small caps have no listed options
        with self.assertRaises(CommandError):
            w.request_locate(pf.id, ch["rows"][0]["C"]["id"], 1)


class TradingTest(unittest.TestCase):
    def test_long_call_premium_settlement_mark_and_close(self):
        w, pf, store = make_world()
        cid = atm_contract(w, "NVDA", "C", 1)
        sec = w.securities[cid]
        w.place_order(pf.id, cid, "BUY", 10)
        w.advance(1)
        pos = pf.positions[cid]
        led = w.ledgers[pf.id]
        t = pf.trades[pos.trade_ids[0]]
        self.assertEqual(pos.quantity, D(10))
        self.assertTrue(pos.is_option)
        self.assertEqual(t.gross_amount, (t.price * 10 * 100).quantize(D("0.01")))
        self.assertEqual(t.commission, D("6.50"))
        self.assertEqual(led.security_balance(cid, "1700"), pos.cost_basis)
        self.assertEqual(led.security_balance(cid, "2100"), t.net_amount, "premium payable until T+1")
        self.assertEqual(pos.settled_quantity, D(0))
        self.assertIn("delta", pos.greeks)
        self.assertEqual(pos.margin_requirement, D(0))
        self.assertEqual(pf.options_margin, D(0))
        assert_ledger_invariants(self, w, pf)
        w.advance(1)
        self.assertEqual(pos.settled_quantity, D(10), "premium settled T+1, contracts in the account")
        self.assertEqual(led.security_balance(cid, "2100"), D(0))
        self.assertEqual(led.security_balance(cid, "1750"), pos.valuation_adjustment)
        assert_ledger_invariants(self, w, pf)
        w.place_order(pf.id, cid, "SELL", 10)
        w.advance(1)
        self.assertEqual(pos.quantity, D(0))
        self.assertEqual(led.security_balance(cid, "1700"), D(0))
        self.assertEqual(led.security_balance(cid, "1750"), D(0))
        self.assertEqual(pos.realized_pnl, led.security_balance(cid, "4000"))
        w.advance(1)
        assert_ledger_invariants(self, w, pf)
        self.assertEqual(led.security_balance(cid, "5000"), D("13.00"))

    def test_short_put_margin_close_and_release(self):
        w, pf, store = make_world(capital=2_000_000)
        pid_ = atm_contract(w, "NVDA", "P", 1)
        w.place_order(pf.id, pid_, "SELL", 5)
        w.advance(1)
        pos = pf.positions[pid_]
        led = w.ledgers[pf.id]
        t = pf.trades[pos.trade_ids[0]]
        self.assertEqual(pos.quantity, D(-5))
        self.assertEqual(led.security_balance(pid_, "2900"), t.gross_amount, "premium received carried as a liability")
        self.assertEqual(led.security_balance(pid_, "1200"), t.net_amount)
        self.assertGreater(pf.options_margin, D(0))
        self.assertEqual([d["kind"] for d in pf.options_margin_detail], ["NAKED_PUT"])
        self.assertEqual(led.balance("1300"), pf.options_margin, "margin swept to the clearing member")
        self.assertEqual(pos.margin_requirement, pf.options_margin)
        # formula: max(prem + 20% S - OTM, prem + 10% K) x contracts x 100 x regime
        q = w.options.quote(w.securities[pid_])
        S, K = D(repr(q["underlying"])), D(repr(w.securities[pid_].strike))
        prem = D(repr(q["mid"]))
        expect = max(prem + S * D("0.2") - max(D(0), S - K), prem + K * D("0.1")) * 5 * 100
        self.assertEqual(pf.options_margin, expect.quantize(D("0.01")))
        assert_ledger_invariants(self, w, pf)
        w.advance(1)
        self.assertEqual(pos.settled_quantity, D(-5))
        self.assertEqual(led.security_balance(pid_, "2910"), -pos.valuation_adjustment)
        assert_ledger_invariants(self, w, pf)
        w.place_order(pf.id, pid_, "BUY", 5)
        w.advance(1)
        self.assertEqual(pos.quantity, D(0))
        self.assertEqual(pf.options_margin, D(0))
        self.assertEqual(led.balance("1300"), D(0), "margin released")
        self.assertEqual(led.security_balance(pid_, "2900"), D(0))
        w.advance(1)
        assert_ledger_invariants(self, w, pf)

    def test_covered_call_reserves_shares_and_has_no_margin(self):
        w, pf, store = make_world()
        cid = atm_contract(w, "JPM", "C", 1, offset=2)
        w.place_order(pf.id, "JPM", "BUY", 1000)
        w.advance(1)
        w.place_order(pf.id, cid, "SELL", 10)
        w.advance(1)
        self.assertEqual(pf.positions[cid].quantity, D(-10))
        self.assertEqual(pf.options_margin, D(0))
        self.assertEqual(pf.options_margin_detail[0]["kind"], "COVERED_CALL")
        self.assertEqual(pf.positions[cid].covered_by_shares, D(10))
        self.assertEqual(w.options.covered_shares_needed(pf, "JPM"), D(1000))
        with self.assertRaises(CommandError) as cm:
            w.place_order(pf.id, "JPM", "SELL", 1000)
        self.assertIn("reserved to cover written calls", str(cm.exception))
        assert_ledger_invariants(self, w, pf)
        # buying the calls back frees the shares
        w.place_order(pf.id, cid, "BUY", 10)
        w.advance(1)
        self.assertEqual(pf.positions[cid].quantity, D(0))
        self.assertEqual(w.options.covered_shares_needed(pf, "JPM"), D(0))
        w.place_order(pf.id, "JPM", "SELL", 1000)
        w.advance(2)
        self.assertEqual(pf.positions["JPM"].quantity, D(0))
        assert_ledger_invariants(self, w, pf)

    def test_vertical_spread_margin_is_the_width(self):
        w, pf, store = make_world(capital=1_000_000)
        ch, ks = strikes_of(w, "NVDA")
        i = min(range(len(ks)), key=lambda j: abs(ks[j] - ch["level"]))
        short_call, long_call = ch["rows"][i + 3]["C"]["id"], ch["rows"][i + 4]["C"]["id"]   # far enough out of the money not to be assigned in the first days
        w.place_order(pf.id, long_call, "BUY", 5)
        w.advance(1)
        w.place_order(pf.id, short_call, "SELL", 5)
        w.advance(1)
        width = D(repr(ks[i + 4] - ks[i + 3]))
        self.assertEqual([d["kind"] for d in pf.options_margin_detail], ["CALL_SPREAD"])
        self.assertEqual(pf.options_margin, (width * 5 * 100).quantize(D("0.01")))
        assert_ledger_invariants(self, w, pf)
        # naked component appears when short exceeds long
        w.place_order(pf.id, short_call, "SELL", 2)
        w.advance(1)
        kinds = sorted(d["kind"] for d in pf.options_margin_detail)
        self.assertEqual(kinds, ["CALL_SPREAD", "NAKED_CALL"])
        self.assertGreater(pf.options_margin, width * 5 * 100)
        assert_ledger_invariants(self, w, pf)

    def test_validation_rules(self):
        w, pf, store = make_world(capital=50_000)
        cid = atm_contract(w, "NVDA", "C", 1)
        with self.assertRaises(CommandError):
            w.place_order(pf.id, cid, "BUY", 500)            # premium not affordable
        with self.assertRaises(CommandError):
            w.place_order(pf.id, cid, "SELL", 200)           # margin not financeable
        fi = w.create_portfolio("FI", "PERSONAL", D(1_000_000), job="FIXED_INCOME_PM")
        with self.assertRaises(CommandError) as cm:
            w.place_order(fi.id, cid, "BUY", 1)
        self.assertIn("mandate", str(cm.exception))
        # an order on a contract that expires today is refused; a GTC order on a contract that expires is cancelled
        near = atm_contract(w, "NVDA", "C", 0)
        exp = date.fromisoformat(w.securities[near].expiry)
        w.place_order(pf.id, near, "SELL", 1, "LIMIT", 99999, None, "GTC")
        while w.current_date < exp:
            w.advance(1)
        with self.assertRaises(CommandError):
            w.place_order(pf.id, near, "BUY", 1)
        w.advance(1)
        o = [o for o in pf.orders.values() if o.security_id == near][0]
        self.assertEqual(o.status, "CANCELLED")
        self.assertTrue(w.securities[near].expired)

    def test_liquidity_caps_fills(self):
        w, pf, store = make_world(capital=500_000_000)
        ch = w.options.chain("NKE")
        far = ch["rows"][-1]["C"]        # far OTM on a mid cap: thin
        w.place_order(pf.id, far["id"], "BUY", 100_000, time_in_force="GTC")
        w.advance(1)
        o = list(pf.orders.values())[-1]
        self.assertEqual(o.status, "PARTIALLY_FILLED")
        t = pf.trades[o.trade_ids[0]]
        self.assertTrue(t.execution_detail["partial"])
        self.assertLessEqual(t.quantity, D(round(t.execution_detail["session_volume"] * 0.2)))
        self.assertGreater(t.execution_detail["impact_bps"], 0)


class ExerciseAssignmentExpiryTest(unittest.TestCase):
    def test_exercise_long_call_delivers_shares_at_strike(self):
        w, pf, store = make_world()
        ch, ks = strikes_of(w, "NVDA")
        cid = ch["rows"][0]["C"]["id"]          # deep ITM
        sec = w.securities[cid]
        w.place_order(pf.id, cid, "BUY", 4)
        w.advance(2)
        cost = pf.positions[cid].cost_basis
        r = w.exercise_option(pf.id, cid, 4)
        ev = w.events_by_id[r["event_id"]]
        self.assertEqual(ev.type, "OPTION_EXERCISED")
        kids = [w.events_by_id[k] for k in w.children[ev.id]]
        self.assertEqual([k.type for k in kids].count("ORDER_ENTERED"), 2, "delivery order + contract close-out derive from the exercise")
        stock = pf.positions["NVDA"]
        self.assertEqual(stock.quantity, D(400))
        t = pf.trades[stock.trade_ids[0]]
        self.assertEqual(t.price, D(repr(sec.strike)).quantize(D("0.0001")))
        self.assertEqual(t.gross_amount, D(repr(sec.strike)) * 400)
        self.assertTrue(t.execution_detail["contractual"])
        self.assertEqual(pf.positions[cid].quantity, D(0))
        self.assertEqual(pf.positions[cid].realized_pnl, -cost, "premium is the realized loss on the contract; the stock carries the intrinsic")
        assert_ledger_invariants(self, w, pf)
        w.advance(2)
        self.assertEqual(stock.settled_quantity, D(400))
        assert_ledger_invariants(self, w, pf)
        with self.assertRaises(CommandError):
            w.exercise_option(pf.id, cid, 1)
        w2 = World.load(store, "t")
        self.assertEqual(len(w2.events), len(w.events))
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())
        self.assertEqual(w2.portfolios[pf.id].positions["NVDA"].quantity, D(400))
        assert_ledger_invariants(self, w2, w2.portfolios[pf.id])

    def test_european_index_option_cannot_be_exercised_early(self):
        w, pf, store = make_world()
        cid = atm_contract(w, "SPX", "C", 1)
        w.place_order(pf.id, cid, "BUY", 2)
        w.advance(1)
        with self.assertRaises(CommandError) as cm:
            w.exercise_option(pf.id, cid)
        self.assertIn("European", str(cm.exception))

    def test_dividend_capture_assignment(self):
        w, pf, store = make_world()
        # a high-yield optionable stock with an ex-date coming up in a few sessions
        today = w.current_date
        cands = []
        for under in w.options.optionable():
            if under == "SPX":
                continue
            sec = w.securities[under]
            if sec.dividend_yield < 0.02:
                continue
            for ex in w.market.dividend_ex_dates(sec, today.year) + w.market.dividend_ex_dates(sec, today.year + 1):
                bd = w.calendar.business_days_between(today, ex)
                if 4 <= bd <= 25:
                    cands.append((bd, under, ex))
        bd, under, ex = sorted(cands)[0]
        ch = w.options.chain(under)
        exp = next(e for e in ch["expiries"] if date.fromisoformat(e) > ex)
        ch = w.options.chain(under, exp)
        deep = [r["C"]["id"] for r in ch["rows"][:3]]     # three deep ITM calls (independent assignment draws)
        w.place_order(pf.id, under, "BUY", 300)
        w.advance(1)
        for cid in deep:
            w.place_order(pf.id, cid, "SELL", 1)
        w.advance(1)
        self.assertEqual(sum(pf.positions[c].quantity for c in deep), D(-3))
        self.assertEqual(pf.options_margin, D(0), "covered")
        prev = w.calendar.prev_business_day(ex)
        while w.current_date < prev:
            w.advance(1)
        assigned = [e for e in w.events if e.type == "OPTION_ASSIGNED" and e.sim_date == prev.isoformat()]
        self.assertGreaterEqual(len(assigned), 1, "deep ITM covered calls are assigned before the ex-date when the dividend exceeds the extrinsic")
        self.assertTrue(any("dividend capture" in e.payload["reason"] for e in assigned))
        n_assigned = sum(D(e.payload["quantity"]) for e in w.events if e.type == "OPTION_ASSIGNED")
        stock = pf.positions[under]
        self.assertEqual(stock.quantity, 300 - n_assigned * 100)
        for e in assigned:
            cid = e.payload["security_id"]
            self.assertEqual(pf.positions[cid].quantity, D(0))
            kids = [w.events_by_id[k] for k in w.children[e.id] if w.events_by_id[k].type == "ORDER_ENTERED"]
            delivery = [k for k in kids if k.payload["security_id"] == under]
            self.assertEqual(len(delivery), 1, "one delivery order per assignment")
            t = pf.trades[pf.orders[delivery[0].payload["order_id"]].trade_ids[0]]
            self.assertEqual(t.side, "SELL")
            self.assertEqual(t.price, D(repr(w.securities[cid].strike)).quantize(D("0.0001")))
            self.assertTrue(t.execution_detail["contractual"])
        assert_ledger_invariants(self, w, pf)
        w.advance(2)
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        self.assertEqual(w2.portfolios[pf.id].positions[under].quantity, stock.quantity)
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())

    def test_expiration_worthless_itm_and_index_cash(self):
        w, pf, store = make_world(capital=50_000_000)
        ch, ks = strikes_of(w, "NVDA")
        otm_call, itm_call = ch["rows"][-1]["C"]["id"], ch["rows"][0]["C"]["id"]
        idx = w.options.chain("SPX")
        idx_call = idx["rows"][0]["C"]["id"]
        exp = date.fromisoformat(w.securities[otm_call].expiry)
        w.place_order(pf.id, otm_call, "BUY", 3)
        w.place_order(pf.id, itm_call, "BUY", 2)
        w.place_order(pf.id, idx_call, "BUY", 1)
        w.advance(1)
        prem_otm = pf.positions[otm_call].cost_basis
        prem_idx = pf.positions[idx_call].cost_basis
        # warning appears before expiry
        while w.calendar.business_days_between(w.current_date, exp) > 3:
            w.advance(1)
        w.advance(1)
        att = " ".join(a["text"] for a in pf.briefings[-1]["attention"])
        self.assertIn("EXPIRATION", att)
        while w.current_date < exp:
            w.advance(1)
        expired = {e.payload["security_id"]: e.payload for e in w.events if e.type == "OPTION_EXPIRED" and e.sim_date == exp.isoformat()}
        S = float(w.market.last_bar("NVDA").close)
        if S < w.securities[otm_call].strike:
            self.assertEqual(expired[otm_call]["outcome"], "WORTHLESS")
            self.assertEqual(pf.positions[otm_call].realized_pnl, -prem_otm)
        if S > w.securities[itm_call].strike:
            self.assertEqual(expired[itm_call]["outcome"], "AUTO_EXERCISED")
            self.assertEqual(pf.positions["NVDA"].quantity, D(200))
        self.assertEqual(pf.positions[itm_call].quantity, D(0))
        self.assertEqual(pf.positions[otm_call].quantity, D(0))
        level = float(w.market.last_bar("SPY").close) * 10
        if level > w.securities[idx_call].strike:
            self.assertEqual(expired[idx_call]["outcome"], "CASH_SETTLED")
            amount = D(expired[idx_call]["amount"])
            self.assertEqual(amount, (D(repr(level - w.securities[idx_call].strike)) * 100).quantize(D("0.01")))
            self.assertEqual(pf.positions[idx_call].realized_pnl, amount - prem_idx)
            self.assertNotIn("SPY", [s for s, p in pf.positions.items() if p.quantity])
        assert_ledger_invariants(self, w, pf)
        w.advance(2)
        assert_ledger_invariants(self, w, pf)
        self.assertEqual(w.ledgers[pf.id].security_balance(idx_call, "1700"), D(0))
        w2 = World.load(store, "t")
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())
        self.assertEqual(len(w2.events), len(w.events))

    def test_short_put_assigned_at_expiry_buys_stock(self):
        w, pf, store = make_world(capital=5_000_000)
        ch, ks = strikes_of(w, "JPM")
        deep_put = ch["rows"][-1]["P"]["id"]
        exp = date.fromisoformat(w.securities[deep_put].expiry)
        w.place_order(pf.id, deep_put, "SELL", 3)
        w.advance(1)
        prem = -pf.positions[deep_put].cost_basis
        while w.current_date < exp:
            w.advance(1)
        S = float(w.market.last_bar("JPM").close)
        if S < w.securities[deep_put].strike:
            self.assertEqual(pf.positions["JPM"].quantity, D(300))
            self.assertEqual(pf.positions[deep_put].realized_pnl, prem, "premium kept; stock bought at the strike")
            self.assertEqual(pf.options_margin, D(0))
        assert_ledger_invariants(self, w, pf)
        w.advance(2)
        assert_ledger_invariants(self, w, pf)


class StrategyTest(unittest.TestCase):
    def test_vertical_all_or_none_and_analytics(self):
        w, pf, store = make_world()
        ch, ks = strikes_of(w, "NVDA")
        i = min(range(len(ks)), key=lambda j: abs(ks[j] - ch["level"]))
        exp = ch["expiry"]
        st = w.place_strategy(pf.id, "BULL_CALL_SPREAD", "NVDA", exp, [ks[i], ks[i + 2]], 5, net_limit=D("0.01"))
        self.assertEqual(st.status, "WORKING")
        self.assertEqual(len(st.legs), 2)
        self.assertTrue(all(l["order_id"] for l in st.legs), "legs linked to orders")
        w.advance(1)
        self.assertEqual(st.status, "WORKING")
        self.assertTrue(all(pf.orders[l["order_id"]].status == "EXPIRED" or pf.orders[l["order_id"]].filled_quantity == 0 for l in st.legs))
        self.assertTrue(any("all-or-none" in h["note"] for h in pf.orders[st.legs[0]["order_id"]].history))
        w.advance(1)
        self.assertEqual(st.status, "EXPIRED")
        st2 = w.place_strategy(pf.id, "BULL_CALL_SPREAD", "NVDA", exp, [ks[i], ks[i + 2]], 5, net_limit=D(5000))
        w.advance(1)
        self.assertEqual(st2.status, "FILLED")
        self.assertGreater(st2.net_premium, 0)
        a = w.options.strategy_analytics(pf, st2)
        width = (ks[i + 2] - ks[i]) * 100
        self.assertAlmostEqual(a["max_loss"], -float(st2.net_premium) * 5, delta=0.01)
        self.assertAlmostEqual(a["max_gain"], (width - float(st2.net_premium)) * 5, delta=0.01)
        self.assertEqual(len(a["breakevens"]), 1)
        self.assertAlmostEqual(a["breakevens"][0], ks[i] + float(st2.net_premium) / 100, delta=0.5)
        self.assertGreater(a["greeks"]["delta"], 0)
        self.assertEqual(w.options.margin_requirement(pf)[0], D(0), "debit spread needs no margin")
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        s2 = w2.portfolios[pf.id].strategies[st2.id]
        self.assertEqual(s2.status, "FILLED")
        self.assertEqual(s2.net_premium, st2.net_premium)
        self.assertEqual([l["order_id"] for l in s2.legs], [l["order_id"] for l in st2.legs])

    def test_iron_condor_straddle_and_covered_call_tickets(self):
        w, pf, store = make_world()
        ch, ks = strikes_of(w, "SPX")
        i = min(range(len(ks)), key=lambda j: abs(ks[j] - ch["level"]))
        exp = ch["expiries"][1]
        ch, ks = strikes_of(w, "SPX", exp)
        ic = w.place_strategy(pf.id, "IRON_CONDOR", "SPX", exp, [ks[i - 4], ks[i - 2], ks[i + 2], ks[i + 4]], 2)
        sd = w.place_strategy(pf.id, "LONG_STRADDLE", "NVDA", w.options.chain("NVDA")["expiries"][1], [atm_strike(w, "NVDA")], 3)
        with self.assertRaises(CommandError):
            w.place_strategy(pf.id, "COVERED_CALL", "NVDA", w.options.chain("NVDA")["expiries"][1], [atm_strike(w, "NVDA")], 1)   # no shares
        with self.assertRaises(CommandError):
            w.place_strategy(pf.id, "NOT_A_STRATEGY", "NVDA", exp, [1], 1)
        w.advance(1)
        self.assertEqual(ic.status, "FILLED")
        self.assertEqual(sd.status, "FILLED")
        self.assertLess(ic.net_premium, 0, "condor is a credit")
        kinds = sorted(d["kind"] for d in pf.options_margin_detail)
        self.assertEqual(kinds, ["CALL_SPREAD", "PUT_SPREAD"])
        width = D(repr(ks[i + 4] - ks[i + 2]))
        self.assertEqual(pf.options_margin, (width * 2 * 100 * 2).quantize(D("0.01")))
        a = w.options.strategy_analytics(pf, ic)
        self.assertAlmostEqual(a["max_gain"], -float(ic.net_premium) * 2, delta=0.01)
        self.assertEqual(len(a["breakevens"]), 2)
        g = w.options.strategy_analytics(pf, sd)["greeks"]
        self.assertLess(abs(g["delta"]), 3 * 100 * 0.35)
        self.assertGreater(g["vega"], 0)
        self.assertLess(g["theta"], 0)
        assert_ledger_invariants(self, w, pf)
        w.advance(1)
        assert_ledger_invariants(self, w, pf)


def atm_strike(w, under):
    ch = w.options.chain(under, w.options.chain(under)["expiries"][1])
    return min((r["strike"] for r in ch["rows"]), key=lambda k: abs(k - ch["level"]))


class MarginCallTest(unittest.TestCase):
    def test_stress_margin_call_and_forced_liquidation(self):
        w, pf, store = make_world(capital=100_000)
        pid_ = atm_contract(w, "NVDA", "P", 1)
        w.place_order(pf.id, pid_, "SELL", 18)
        w.advance(2)
        self.assertEqual(pf.positions[pid_].quantity, D(-18))
        m0 = pf.options_margin
        w.force_regime("LIQUIDITY_STRESS")
        w.advance(1)
        self.assertGreater(pf.options_margin, m0, "requirement rises with the regime multiplier and the vol shock")
        days = 0
        calls = lambda: [c for c in pf.collateral_calls.values() if c.source == "PRIME"]
        while not any(c.status == "FORCED" for c in calls()) and days < 12:
            w.advance(1)
            days += 1
        self.assertTrue(calls(), "the margin swept to the clearing member overdrew cash, the prime broker financed it and called the shortfall")
        self.assertTrue(any(c.status == "FORCED" for c in calls()), "unmet call leads to forced liquidation")
        self.assertEqual(pf.positions[pid_].quantity, D(0), "short puts bought back by the broker")
        self.assertEqual(pf.options_margin, D(0))
        t = [pf.trades[t] for t in pf.positions[pid_].trade_ids if pf.trades[t].execution_detail.get("forced")]
        self.assertTrue(t)
        self.assertGreaterEqual(pf.cash_account("USD").balance, D(0))
        assert_ledger_invariants(self, w, pf)
        w.advance(2)
        assert_ledger_invariants(self, w, pf)


class SplitTest(unittest.TestCase):
    def test_split_adjusts_positions_and_contracts(self):
        w, pf, store = make_world()
        cid = atm_contract(w, "NVDA", "C", 2)
        w.place_order(pf.id, "NVDA", "BUY", 100)
        w.place_order(pf.id, cid, "BUY", 1)
        w.advance(2)
        k0, mv0 = w.securities[cid].strike, pf.positions[cid].market_value
        nav0 = w.ledgers[pf.id].nav()
        n_contracts = sum(1 for s in w.securities.values() if s.is_option and s.underlying == "NVDA")
        w.force_split("NVDA", 2.0)
        self.assertEqual(pf.positions["NVDA"].quantity, D(200))
        self.assertEqual(pf.positions["NVDA"].settled_quantity, D(200))
        self.assertEqual(pf.positions[cid].quantity, D(2))
        self.assertEqual(w.securities[cid].strike, k0 / 2)
        self.assertEqual(w.securities[cid].deliverable["quantity"], 100)
        self.assertEqual(w.ledgers[pf.id].nav(), nav0, "a split is NAV-neutral")
        self.assertEqual(sum(1 for e in w.events if e.type == "CONTRACT_ADJUSTED"), n_contracts)
        assert_ledger_invariants(self, w, pf)
        w.advance(1)
        self.assertAlmostEqual(float(pf.positions[cid].market_value), float(mv0), delta=float(mv0) * 0.5)
        assert_ledger_invariants(self, w, pf)
        # non-whole ratio: deliverable changes instead of the contract count
        cid2 = atm_contract(w, "JPM", "C", 2)
        w.place_order(pf.id, cid2, "BUY", 1)
        w.advance(2)
        w.force_split("JPM", 1.5)
        self.assertEqual(pf.positions[cid2].quantity, D(1))
        self.assertEqual(w.securities[cid2].deliverable["quantity"], 150)
        self.assertEqual(w.securities[cid2].multiplier, 150.0)
        w.advance(1)
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        self.assertEqual(w2.securities[cid].strike, k0 / 2)
        self.assertEqual(w2.portfolios[pf.id].positions["NVDA"].quantity, D(200))
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())
        self.assertEqual([b.close for b in w2.market.history["NVDA"][-5:]], [b.close for b in w.market.history["NVDA"][-5:]])


class OptionPnLTest(unittest.TestCase):
    def test_options_bucket_and_greek_attribution(self):
        w, pf, store = make_world()
        cid = atm_contract(w, "NVDA", "C", 2)
        pid_ = atm_contract(w, "SPX", "P", 2)
        w.place_order(pf.id, cid, "BUY", 20)
        w.place_order(pf.id, pid_, "SELL", 2)
        w.advance(3)
        snap = pf.nav_history[-1]
        ex = snap.explain
        self.assertIn("options", ex)
        self.assertEqual(sum((v for k, v in ex.items() if not k.startswith("_")), D(0)), snap.day_pnl)
        self.assertTrue(pf.briefings[-1]["pnl_buckets"].get("options") is not None)
        ga = snap.greek_attribution
        self.assertEqual(ga["actual"], ex["options"])
        self.assertEqual(len(ga["rows"]), 2)
        self.assertAlmostEqual(ga["explained"] + ga["residual"], float(ga["actual"]), places=1)
        self.assertLess(abs(ga["residual"]), max(200.0, 0.25 * abs(float(ga["actual"]))), "Taylor attribution explains most of a calm day")
        pos = pf.positions[cid]
        self.assertEqual(pos.greeks["date"], w.current_date.isoformat())
        self.assertLess(pos.prev_greeks["date"], pos.greeks["date"])
        # position rows carry greeks scaled by contracts x multiplier
        rows = {r["security_id"]: r for r in w.options.position_rows(pf)}
        self.assertAlmostEqual(rows[cid]["delta"], pos.greeks["delta"] * 20 * 100, places=6)
        agg = w.options.aggregate_greeks(pf)
        self.assertAlmostEqual(agg["total"]["delta"], rows[cid]["delta"] + rows[pid_]["delta"], places=6)
        d = pf.briefings[-1]["derivatives"]
        self.assertEqual(d["positions"], 2)


class OptionsDefinitionOfDoneTest(unittest.TestCase):
    """Deterministic scenario: run twice with the same seed and compare every material number."""

    def run_scenario(self):
        w, pf, store = make_world(seed=11, capital=20_000_000)
        log = []
        ch, ks = strikes_of(w, "NVDA")
        i = min(range(len(ks)), key=lambda j: abs(ks[j] - ch["level"]))
        exp1 = ch["expiries"][1]
        ch1, ks1 = strikes_of(w, "NVDA", exp1)
        i1 = min(range(len(ks1)), key=lambda j: abs(ks1[j] - ch1["level"]))
        # 1 shares + covered call, 2 long put protection, 3 index condor, 4 naked put
        w.place_order(pf.id, "NVDA", "BUY", 2000)
        w.advance(1)
        w.place_strategy(pf.id, "COVERED_CALL", "NVDA", exp1, [ks1[i1 + 2]], 10)
        w.place_strategy(pf.id, "PROTECTIVE_PUT", "NVDA", exp1, [ks1[i1 - 2]], 10)
        idx, iks = strikes_of(w, "SPX", w.options.chain("SPX")["expiries"][1])
        j = min(range(len(iks)), key=lambda k: abs(iks[k] - idx["level"]))
        w.place_strategy(pf.id, "IRON_CONDOR", "SPX", idx["expiry"], [iks[j - 5], iks[j - 3], iks[j + 3], iks[j + 5]], 3)
        w.place_order(pf.id, atm_contract(w, "JPM", "P", 1, offset=-1), "SELL", 5)
        w.advance(2)
        assert_ledger_invariants(self, w, pf)
        for st in pf.strategies.values():
            self.assertEqual(st.status, "FILLED", st.strategy_type)
        log.append(("margin", pf.options_margin))
        # 5 exercise part of the puts early, 6 split, 7 stress regime, 8 run to expiry
        put_id = [l["security_id"] for st in pf.strategies.values() if st.strategy_type == "PROTECTIVE_PUT" for l in st.legs][0]
        w.exercise_option(pf.id, put_id, 2)
        w.advance(1)
        assert_ledger_invariants(self, w, pf)
        w.force_split("NVDA", 2.0)
        assert_ledger_invariants(self, w, pf)
        w.force_regime("RECESSION")
        w.advance(3)
        assert_ledger_invariants(self, w, pf)
        exp = max(date.fromisoformat(s.expiry) for s in w.securities.values() if s.is_option and s.id in pf.positions and pf.positions[s.id].quantity)
        while w.current_date <= exp:
            w.advance(1)
            assert_ledger_invariants(self, w, pf)
        self.assertFalse(any(p.quantity for p in pf.positions.values() if p.is_option), "everything expired or was exercised/assigned")
        self.assertEqual(pf.options_margin, D(0))
        w.advance(2)
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        self.assertEqual(len(w2.events), len(w.events))
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())
        self.assertEqual({k: v.quantity for k, v in w2.portfolios[pf.id].positions.items()}, {k: v.quantity for k, v in pf.positions.items()})
        for s in pf.nav_history:
            self.assertTrue((s.nav - (pf.nav_history[pf.nav_history.index(s) - 1].nav if pf.nav_history.index(s) else s.nav - s.day_pnl - s.capital_flows) - s.capital_flows) == s.day_pnl)
        kinds = {e.type for e in w.events}
        for k in ("OPTION_EXERCISED", "OPTION_EXPIRED", "STRATEGY_ENTERED", "STOCK_SPLIT", "CONTRACT_ADJUSTED", "OPTIONS_MARGIN_COMPUTED", "VALUATION_MARKED"):
            self.assertIn(k, kinds)
        return w.ledgers[pf.id].nav(), len(w.events), log, w.current_date

    def test_definition_of_done_is_deterministic(self):
        a = self.run_scenario()
        b = self.run_scenario()
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
