"""Phase 8 — careers and game modes: missions, investors (hedge fund), the client franchise (bank / derivatives
trader), the lending desk (lend side), the corporate treasury, and the scripted crisis scenario."""
import unittest
from datetime import date

from helpers import make_world, assert_ledger_invariants
from finsim.world import World, CommandError
from finsim.store import EventStore
from finsim.money import D, ZERO
from finsim.domain.events import E


def job_world(job, seed=42, start="2026-01-05", capital=None, scenario="NONE"):
    from finsim.careers import JOBS
    store = EventStore(":memory:")
    w = World.create("t", "test", seed, date.fromisoformat(start), store=store, scenario=scenario)
    pf = w.create_portfolio("Desk", "INSTITUTIONAL", D(capital) if capital else JOBS[job].capital, job=job)
    return w, pf, store


def check_all(tc, w):
    for p in w.portfolios.values():
        assert_ledger_invariants(tc, w, p)


class MissionTest(unittest.TestCase):
    def test_missions_start_progress_complete_with_bonus_and_replay(self):
        w, pf, store = job_world("PORTFOLIO_MANAGER")
        ids = [m["id"] for m in pf.missions]
        self.assertEqual(ids, ["PM_DIVERSIFY", "PM_VAR"])
        self.assertTrue(all(m["status"] == "ACTIVE" and m["progress"] == 0.0 for m in pf.missions))
        # build a diversified book: 8 names across 4+ sectors, small sizes
        by_sector = {}
        for s in w.securities.values():
            if s.asset_class == "EQUITY" and s.liquidity_tier == "LARGE" and not s.delisted:
                by_sector.setdefault(s.sector, []).append(s.id)
        picks = []
        for sec, names in sorted(by_sector.items()):
            picks.extend(names[:2])
        picks = picks[:8]
        self.assertGreaterEqual(len({w.securities[p].sector for p in picks}), 4)
        cap0 = pf.contributed_capital
        for sid in picks:
            w.place_order(pf.id, sid, "BUY", 1000)
        w.advance(1)
        m = next(x for x in pf.missions if x["id"] == "PM_DIVERSIFY")
        self.assertEqual(m["status"], "COMPLETED")
        self.assertEqual(m["progress"], 1.0)
        self.assertGreater(m["bonus"], 0, "a completed mission earns a capital allocation")
        self.assertEqual(pf.contributed_capital, cap0 + m["bonus"])
        self.assertTrue(any(x["kind"] == "MISSION" for x in pf.career_log))
        assert_ledger_invariants(self, w, pf)
        # the VaR mission counts consecutive clean sessions
        w.advance(3)
        v = next(x for x in pf.missions if x["id"] == "PM_VAR")
        self.assertEqual(v["status"], "ACTIVE")
        self.assertGreater(v["progress"], 0.0)
        w2 = World.load(store, "t")
        self.assertEqual(w2.portfolios[pf.id].missions, pf.missions)
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())

    def test_mission_fails_at_the_deadline(self):
        w, pf, store = job_world("REPO_TRADER")
        m = next(x for x in pf.missions if x["id"] == "RP_MATCHED")
        deadline = date.fromisoformat(m["deadline"])
        while w.current_date <= deadline:
            w.advance(1)
        self.assertEqual(m["status"], "FAILED")
        self.assertTrue(any("Mission failed" in x["text"] for x in pf.career_log))


class HedgeFundTest(unittest.TestCase):
    def test_investors_fees_redemptions_and_hwm(self):
        w, pf, store = job_world("HEDGE_FUND", capital=100_000_000)
        led = w.ledgers[pf.id]
        w.advance(1)
        inv = pf.investors
        self.assertEqual(sum(inv["holders"].values(), ZERO), D(100_000_000), "the seed holders own the fund")
        self.assertEqual(inv["hwm"], D(100_000_000))
        self.assertGreater(led.balance("5900"), 0, "management fee accrues daily")
        self.assertEqual(led.balance("2360"), D(inv["mgmt_accrued"]))
        assert_ledger_invariants(self, w, pf)
        # a concentrated loss and a recession: investors redeem at the next dealing day
        sid = "NVDA"
        w.place_order(pf.id, sid, "BUY", 400_000)
        w.advance(2)
        w.force_regime("RECESSION")
        w.market.force_return(sid, -0.35)
        w.advance(1)
        dd = float((pf.peak_nav - w.ledgers[pf.id].nav()) / pf.peak_nav)
        self.assertGreater(dd, 0.05)
        # advance through the 15th (notice) and month end (dealing)
        while not (w.current_date.day >= 15 and inv.get("pending")):
            w.advance(1)
            if w.current_date.month == 3:
                self.fail("no redemption notice after a drawdown in a recession")
        self.assertTrue(all(p["kind"] == "REDEMPTION" for p in inv["pending"]))
        self.assertTrue(any(f["kind"] == "NOTICE" for f in inv["flows"]))
        cap_before = pf.contributed_capital
        cash_before = pf.cash["USD"].balance
        while not w.calendar.is_month_end(w.current_date):
            w.advance(1)
        w.advance(1)   # the month-end close deals and pays the management fee
        self.assertLess(pf.contributed_capital, cap_before, "redemptions leave the fund")
        self.assertTrue(any(m.kind == "CAPITAL" and m.amount < 0 for m in pf.cash_movements))
        self.assertTrue(any(m.kind == "FEE" for m in pf.cash_movements), "management fee paid at month end")
        self.assertEqual(led.balance("2360"), D(inv.get("mgmt_accrued", 0)) + D(inv.get("perf_accrued", 0)))
        self.assertGreater(inv["fees_paid"], 0)
        self.assertTrue(any(s.capital_flows < 0 for s in pf.nav_history[-3:]), "NAV bridge carries the redemption as a capital flow")
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        self.assertEqual(w2.portfolios[pf.id].investors["holders"], inv["holders"])
        self.assertEqual(w2.ledgers[pf.id].nav(), led.nav())

    def test_performance_fee_crystallises_in_december_above_hwm(self):
        w, pf, store = job_world("HEDGE_FUND", seed=7, start="2026-12-01", capital=50_000_000)
        w.advance(1)
        inv = pf.investors
        # manufacture a profit above the high-water mark, then close the year
        w.place_order(pf.id, "SPY", "BUY", 20_000)
        w.advance(2)
        w.market.force_return("SPY", 0.5)
        while w.current_date.month == 12:
            w.advance(1)
        self.assertGreater(inv["fees_paid"], 0)
        self.assertTrue(any(f["kind"] == "FEE" and "performance" in f["detail"] for f in inv["flows"]), "performance fee crystallised")
        self.assertGreater(inv["hwm"], D(50_000_000), "the high-water mark ratchets up net of the fee")
        assert_ledger_invariants(self, w, pf)


class ClientFranchiseTest(unittest.TestCase):
    def test_bank_trader_quotes_win_and_book_at_the_desk_level(self):
        w, pf, store = job_world("BANK_TRADER")
        w.advance(1)
        open_reqs = [r for r in pf.client_rfqs.values() if r["status"] == "OPEN"]
        self.assertTrue(open_reqs, "client requests arrive every session")
        with self.assertRaises(CommandError):
            w.quote_client(pf.id, open_reqs[0]["id"])            # a level is required unless passing
        won_total = 0
        for _ in range(8):
            for r in pf.client_rfqs.values():
                if r["status"] == "OPEN":
                    # quote at mid: the client hits with high probability and the desk books the other side
                    w.quote_client(pf.id, r["id"], float(r["mid"]))
            w.advance(1)
            assert_ledger_invariants(self, w, pf)
        st = pf.client_stats
        self.assertGreater(st.get("won", 0), 0)
        self.assertEqual(st["requests"], st.get("won", 0) + st.get("lost", 0) + st.get("passed", 0) + st.get("expired", 0))
        won = [r for r in pf.client_rfqs.values() if r["outcome"] == "WON"]
        # securities blocks sit in the position at the quoted price; OTC requests become client-facing trades
        for r in won:
            if r["product"] in ("UST", "CORP", "EQUITY"):
                trades = [t for t in pf.trades.values() if t.security_id == r["security_id"] and t.price == D(repr(float(r["mid"]))).quantize(D("0.0001"))]
                self.assertTrue(trades, f"block {r['id']} booked at the desk's level")
                self.assertEqual(trades[0].commission, ZERO, "client blocks carry no commission")
            else:
                ts = [t for t in pf.otc_trades.values() if t.counterparty == f"CLIENT:{r['client']}"]
                self.assertTrue(ts, f"OTC request {r['id']} booked against the client")
        client_cps = {t.counterparty for t in pf.otc_trades.values() if t.counterparty.startswith("CLIENT:")}
        ex = w.otc.exposure(pf)
        for cp in client_cps:
            row = next(x for x in ex["rows"] if x["dealer"] == cp)
            self.assertTrue(row["is_client"])
        # passing keeps the request out of the book
        nxt = next((r for r in pf.client_rfqs.values() if r["status"] == "OPEN"), None)
        if nxt:
            w.quote_client(pf.id, nxt["id"], None, pass_=True)
            w.advance(1)
            self.assertEqual(pf.client_rfqs[nxt["id"]]["outcome"], "PASSED")
        w2 = World.load(store, "t")
        self.assertEqual(w2.portfolios[pf.id].client_stats, pf.client_stats)
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())

    def test_off_market_levels_lose_the_flow(self):
        w, pf, store = job_world("DERIVATIVES_TRADER", seed=3)
        w.advance(1)
        for _ in range(6):
            for r in pf.client_rfqs.values():
                if r["status"] == "OPEN":
                    hw = float(r["street_half_width"])
                    mid = float(r["mid"])
                    # 3× the street width away from mid, on the side that earns the desk the most
                    lvl = mid + 3 * hw if r["client_side"] == "BUY" else mid - 3 * hw
                    w.quote_client(pf.id, r["id"], lvl)
            w.advance(1)
        st = pf.client_stats
        self.assertGreater(st.get("lost", 0), st.get("won", 0), "uncompetitive quotes lose most requests")


class LendingDeskTest(unittest.TestCase):
    def test_lend_accrue_settle_recall_and_return(self):
        w, pf, store = job_world("SEC_LENDING", capital=50_000_000)
        led = w.ledgers[pf.id]
        # pick a name with a lending market and demand
        sid = next(s.id for s in w.securities.values() if s.asset_class == "EQUITY" and s.liquidity_tier == "LARGE"
                   and w.market.lending.state.get(s.id) and w.market.lending.state[s.id].utilization > 0.2)
        with self.assertRaises(CommandError):
            w.lend_out(pf.id, sid, 1000)                       # nothing in custody yet
        w.place_order(pf.id, sid, "BUY", 50_000)
        w.advance(2)
        cash0 = pf.cash["USD"].balance
        l = w.lend_out(pf.id, sid, 50_000)
        self.assertEqual(l["status"], "OPEN")
        self.assertGreater(D(l["quantity"]), 0)
        self.assertEqual(led.balance("2460"), D(l["collateral"]))
        self.assertEqual(pf.cash["USD"].balance, cash0 + D(l["collateral"]), "102% cash collateral received")
        self.assertEqual(w.collateral.available_quantity(pf, sid), D(50_000) - D(l["quantity"]), "lent shares are encumbered")
        with self.assertRaises(CommandError):
            w.place_order(pf.id, sid, "SELL", 50_000)          # cannot deliver what is on loan
        assert_ledger_invariants(self, w, pf)
        w.advance(1)
        self.assertGreater(D(l["accrued_fee"]), 0)
        self.assertEqual(led.balance("1430"), D(l["accrued_fee"]))
        self.assertEqual(led.balance("2370"), D(l["accrued_rebate"]))
        book = w.lenddesk.book(pf)
        self.assertGreater(book["utilisation_of_inventory"], 0.5)
        util = next(m for m in pf.missions if m["id"] == "SL_UTIL")
        self.assertEqual(util["status"], "COMPLETED")
        # month end settles fees net of rebate
        while w.current_date.month == 1 and l["status"] == "OPEN":
            w.advance(1)
            assert_ledger_invariants(self, w, pf)
        if l["status"] == "OPEN":
            self.assertGreater(pf.lend_fees_earned, 0)
            self.assertTrue(any(m.kind == "BORROW_FEE" and m.amount != 0 for m in pf.cash_movements))
            # recall: the shares come back on the due date and collateral is returned
            w.recall_lent(pf.id, l["id"])
            self.assertIsNotNone(l["recall_due"])
            while l["status"] == "OPEN":
                w.advance(1)
            self.assertEqual(l["status"], "RETURNED")
        self.assertEqual(led.balance("2460"), ZERO)
        self.assertEqual(pf.pledged_quantity(sid), ZERO)
        self.assertEqual(w.collateral.available_quantity(pf, sid), D(50_000))
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        self.assertEqual(w2.portfolios[pf.id].lends[l["id"]]["status"], "RETURNED")
        self.assertEqual(w2.ledgers[pf.id].nav(), led.nav())


class TreasuryTest(unittest.TestCase):
    def test_operating_flows_debt_service_and_hedging_missions(self):
        w, pf, store = job_world("TREASURY_MANAGER")
        led = w.ledgers[pf.id]
        t = pf.treasury
        self.assertEqual(len(t["debt"]), 3)
        self.assertEqual(led.balance("2380"), D(600_000_000))
        self.assertEqual(led.balance("1900"), D(600_000_000), "debt funded operating assets; equity is the job's capital")
        self.assertEqual(led.nav(), D(120_000_000))
        # the 20th brings receipts in EUR/GBP/JPY and base-currency payments
        while not t.get("flows"):
            w.advance(1)
        self.assertEqual(t["flows"][-1]["kind"], "OPERATING")
        self.assertGreater(pf.cash["EUR"].balance, 0)
        self.assertGreater(led.balance("4980"), 0)
        assert_ledger_invariants(self, w, pf)
        # hedge half the EUR receipts with a 12-month forward: mission TM_FX completes at the close
        d = w.treasury.dashboard(pf)
        eur_usd = d["fx_exposure"]["EUR"]["next_12m_receipts_base"]
        usd_to_buy = (eur_usd * D("0.55")).quantize(D("0.01"))
        w.fx_forward(pf.id, "USD", "EUR", usd_to_buy, w.calendar.add_business_days(w.current_date, 250).isoformat())
        # fix the floating debt with a 150MM pay-fixed swap: TM_RATES completes
        r = w.request_quote(pf.id, "IRS", {"notional": 150_000_000, "tenor_years": 4, "pay_fixed": True})
        best = min((q for q in r.quotes if not q.get("declined")), key=lambda q: q["cost_vs_mid"])
        w.execute_rfq(pf.id, r.id, best["dealer"])
        w.advance(1)
        d = w.treasury.dashboard(pf)
        self.assertGreaterEqual(d["fx_exposure"]["EUR"]["hedge_ratio"], 0.5)
        self.assertLessEqual(d["floating_share_after_swaps"], 0.2)
        ms = {m["id"]: m["status"] for m in pf.missions}
        self.assertEqual(ms["TM_FX"], "COMPLETED")
        self.assertEqual(ms["TM_RATES"], "COMPLETED")
        # the term loan pays a floating coupon every quarter; the fixed notes semi-annually
        target = w.calendar.add_business_days(w.current_date, 70)
        while w.current_date < target:
            w.advance(1)
        self.assertGreater(led.balance("5950"), 0, "debt interest paid")
        self.assertTrue(any(f["kind"] == "DEBT_SERVICE" and f["debt_id"] == "TERM-LOAN" for f in t["flows"]))
        self.assertEqual(led.balance("2380"), D(600_000_000), "no maturity yet")
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        self.assertEqual(w2.portfolios[pf.id].treasury["flows"], t["flows"])
        self.assertEqual(w2.ledgers[pf.id].nav(), led.nav())


class CrisisScenarioTest(unittest.TestCase):
    def test_script_fires_by_day_index_and_replays(self):
        w, pf, store = job_world("GLOBAL_MACRO", scenario="CRISIS")
        self.assertEqual(w.scenario, "CRISIS")
        self.assertEqual(w.scenario_log, [], "the creation day is calm")
        w.advance(1)
        self.assertEqual(len(w.scenario_log), 1)
        self.assertEqual(w.market.state.regime, "LIQUIDITY_STRESS")
        self.assertTrue(any(e.type == E.RATES_SHOCK_FORCED for e in w.events))
        w.advance(4)
        self.assertTrue(w.securities["AAL-28"].defaulted, "the weakest issuer fails on day 5")
        w.advance(5)
        self.assertTrue(w.market.dealers.state["DEUTSCHE"].defaulted, "a dealer fails on day 10")
        self.assertEqual([x["day"] for x in w.scenario_log], [1, 5, 10])
        check_all(self, w)
        w2 = World.load(store, "t")
        self.assertEqual(w2.scenario_log, w.scenario_log)
        self.assertEqual(w2.market.state.regime, w.market.state.regime)
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())
        # the script continues deterministically from a loaded save (the loaded world owns the store from here)
        w2.advance(1)
        self.assertEqual(len(w2.scenario_log), 3)
        self.assertEqual(w2.day_count, w.day_count + 1)

    def test_scenario_is_a_creation_option(self):
        from finsim.api.service import Service
        s = Service(EventStore(":memory:"))
        with self.assertRaises(CommandError):
            s.create_world("x", 1, "2026-01-05", scenario="APOCALYPSE")
        r = s.create_world("x", 1, "2026-01-05", scenario="crisis", job="PORTFOLIO_MANAGER")
        info = s.world_info(r["world_id"])
        self.assertEqual(info["scenario"], "CRISIS")


if __name__ == "__main__":
    unittest.main()
