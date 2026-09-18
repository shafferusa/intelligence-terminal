"""Private equity: deal flow, bids, the buyout, quarterly operating reports, covenants, value creation, recaps, exits, replay."""
import unittest
from datetime import date

try:
    from helpers import make_world, assert_ledger_invariants
except ImportError:
    from tests.helpers import make_world, assert_ledger_invariants

from finsim.api.service import Service
from finsim.engines.private_equity import SECTORS, irr
from finsim.money import D, ZERO
from finsim.store import EventStore
from finsim.world import World, CommandError


def affordable(pe, pipe, cap=1.2e9, extra=0.5):
    return max((d for d in pipe if d["status"] == "OPEN" and pe.structure(d, d["ask_multiple"] + extra, d["max_leverage"])["equity_cheque"] < cap), key=lambda d: d["ebitda"])


class PrivateEquityTest(unittest.TestCase):
    def test_buyout_lifecycle_and_replay(self):
        w, pf, _ = make_world()
        store = w.store
        w.contribute_capital(pf.id, "USD", D(3_000_000_000))
        pe = w.pequity
        book = pe.book(pf)
        self.assertEqual(set(book["market"]["public_multiples"]), set(SECTORS))
        pipe = book["pipeline"]
        self.assertGreaterEqual(len(pipe), 6)
        self.assertEqual(pe.pipeline(), pe.pipeline(), "deterministic within the month")
        for d in pipe:
            self.assertGreater(d["ask_multiple"], 3.0); self.assertGreater(d["ebitda"], 0); self.assertIsNone(d.get("qoe_adj"))
        deal = affordable(pe, pipe)
        st = pe.structure(deal, deal["ask_multiple"], deal["max_leverage"])
        self.assertAlmostEqual(st["ev"], deal["ask_multiple"] * deal["ebitda"], delta=1)
        self.assertAlmostEqual(st["senior"] + st["second_lien"], st["debt"], delta=1)
        self.assertAlmostEqual(st["equity_total"], st["ev"] + st["fees"] - st["debt"], delta=1)
        self.assertAlmostEqual(st["equity_cheque"], st["equity_total"] * st["ownership"], delta=1)
        # diligence reveals the quality-of-earnings adjustment; the bid is validated
        w.pe_diligence(pf.id, deal["id"])
        d2 = pe.deal(deal["id"], pf.id)
        self.assertTrue(d2["diligence"]); self.assertIsNotNone(d2["qoe_adj"])
        with self.assertRaises(CommandError):
            w.pe_bid(pf.id, deal["id"], 2.0, 3.0)                       # below 3x
        with self.assertRaises(CommandError):
            w.pe_bid(pf.id, deal["id"], deal["ask_multiple"], deal["max_leverage"] + 2)   # more debt than lenders give
        cash0 = pf.cash_account("USD").balance
        b = w.pe_bid(pf.id, deal["id"], deal["ask_multiple"] + 0.5, deal["max_leverage"])
        self.assertEqual(b["status"], "PENDING"); self.assertGreater(b["decides"], w.current_date.isoformat())
        low = next(d for d in pipe if d["id"] != deal["id"] and d["status"] == "OPEN")
        w.pe_bid(pf.id, low["id"], 3.5, 2.0)                             # a lowball: declined
        w.advance(6)
        comps = list(pf.pe_companies.values())
        self.assertEqual(len(comps), 1, "a bid at the ask plus half a turn wins; the lowball is declined")
        c = comps[0]
        self.assertEqual(c.deal_id, deal["id"]); self.assertEqual(c.status, "ACTIVE")
        self.assertTrue(any(low["id"] == n["deal_id"] for n in pf.pe_log))
        cheque = D(str(b["structure"]["equity_cheque"]))
        self.assertLess(pf.cash_account("USD").balance, cash0 - cheque + 1)
        self.assertLess(c.equity_invested, cheque, "fees are expensed, not capitalised")
        self.assertEqual(c.mark_value, c.equity_invested, "held at cost until the first report")
        self.assertTrue(c.qoe_revealed)
        assert_ledger_invariants(self, w, pf)
        # value creation, then quarters of operating
        w.pe_initiative(pf.id, c.id, "COST_PROGRAM")
        with self.assertRaises(CommandError):
            w.pe_initiative(pf.id, c.id, "COST_PROGRAM")                 # one at a time
        ebitda0 = c.ebitda
        w.pe_initiative(pf.id, c.id, "ADD_ON", 0.2)
        self.assertGreater(c.ebitda, ebitda0 * 1.15, "the add-on's EBITDA joins the platform")
        with self.assertRaises(CommandError):
            w.pe_recap(pf.id, c.id, c.leverage + 1.0)                    # lenders want two quarters first
        w.advance(140)
        self.assertGreaterEqual(len(c.reports), 2)
        r = c.reports[-1]
        for k in ("revenue", "margin", "ebitda_ltm", "fcf", "interest", "paydown", "leverage", "icr", "mark_multiple", "mark_value"):
            self.assertIn(k, r)
        self.assertNotEqual(c.mark_multiple, c.entry_multiple, "the mark drifts toward the public comps")
        self.assertIn(c.status, ("ACTIVE", "BREACH"))
        if c.status == "BREACH":
            self.assertIsNotNone(c.breach)
            w.pe_cure(pf.id, c.id)
            self.assertEqual(c.status, "ACTIVE")
        assert_ledger_invariants(self, w, pf)
        book = pe.book(pf)
        row = book["companies"][0]
        self.assertIsNotNone(row["moic"]); self.assertIn("irr", row); self.assertIsNotNone(book["totals"]["tvpi"])
        # a dividend recap pays a distribution and re-marks the equity down by the debt added
        cap = pe.max_leverage(c.sector)
        if c.leverage + 1.0 <= cap:
            mark0, cash1 = c.mark_value, pf.cash_account("USD").balance
            w.pe_recap(pf.id, c.id, c.leverage + 1.0)
            self.assertGreater(c.distributions, 0); self.assertGreater(pf.cash_account("USD").balance, cash1); self.assertLess(c.mark_value, mark0)
        assert_ledger_invariants(self, w, pf)
        # a sale process: thirty sessions, then sold (or pulled in a bad market and still ours)
        w.pe_exit(pf.id, c.id, "SALE")
        self.assertEqual(c.status, "EXIT_PROCESS")
        w.advance(35)
        self.assertIn(c.status, ("SOLD", "ACTIVE", "BREACH"))
        if c.status == "SOLD":
            self.assertGreater(c.distributions, 0); self.assertEqual(c.mark_value, ZERO); self.assertIsNotNone(c.exit.get("multiple"))
            self.assertEqual(pe.book(pf)["totals"]["value"], ZERO)
        assert_ledger_invariants(self, w, pf)
        # replay: companies, marks, distributions and the ledger come back exactly
        w2 = World.load(store, w.id)
        self.assertEqual(w2.replay_errors, [])
        p2 = w2.portfolio(pf.id)
        for cid, cc in pf.pe_companies.items():
            c2 = p2.pe_companies[cid]
            self.assertEqual((cc.status, cc.mark_value, cc.equity_invested, cc.distributions, cc.ebitda, cc.tlb, cc.quarters_held),
                             (c2.status, c2.mark_value, c2.equity_invested, c2.distributions, c2.ebitda, c2.tlb, c2.quarters_held))
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())
        self.assertEqual(w2.pequity.pipeline(None, pf.id), pe.pipeline(None, pf.id))

    def test_ipo_exit_lists_the_stake_and_sells_it_down(self):
        w, pf, _ = make_world()
        w.contribute_capital(pf.id, "USD", D(3_000_000_000))
        pe = w.pequity
        pipe = pe.pipeline(None, pf.id)
        big = [d for d in pipe if d["ebitda"] >= 60_000_000 and d["status"] == "OPEN" and pe.structure(d, d["ask_multiple"] + 1.0, 3.0)["equity_cheque"] < 2.5e9]
        if not big:
            self.skipTest("no listable deal in this month's flow")
        deal = max(big, key=lambda d: d["ebitda"])
        w.pe_bid(pf.id, deal["id"], deal["ask_multiple"] + 1.0, 3.0)
        w.advance(6)
        c = next(x for x in pf.pe_companies.values() if x.deal_id == deal["id"])
        with self.assertRaises(CommandError):
            w.pe_selldown(pf.id, c.id, 1.0)                                # nothing listed yet
        w.pe_exit(pf.id, c.id, "IPO")
        self.assertEqual(c.status, "IPO_PROCESS")
        w.advance(50)
        self.assertIn(c.status, ("LISTED", "ACTIVE"))
        if c.status == "LISTED":
            self.assertGreater(c.distributions, 0, "a quarter of the stake sold at the offer")
            self.assertGreater(c.listed["held"], 0); self.assertGreater(c.mark_value, 0)
            with self.assertRaises(CommandError):
                w.pe_selldown(pf.id, c.id, 1.0)                            # lock-up
            price0 = c.listed["price"]
            w.advance(5)
            self.assertNotEqual(c.listed["price"], price0, "a listed stake is marked daily")
            w.advance(260)
            self.assertIn(c.status, ("LISTED", "IPO_COMPLETE"))
            self.assertLess(c.listed["held"], c.listed["shares"] * c.ownership * 0.75 - 1, "blocks go after the lock-up")
        assert_ledger_invariants(self, w, pf)

    def test_service_routes_and_mandate(self):
        s = Service(EventStore(":memory:"))
        r = s.create_world("pe", 42, start_date="2026-01-05", job="PRIVATE_EQUITY", clock_mode="SANDBOX")
        w = s.worlds[r["world_id"]]; pf = w.portfolio(r["portfolio_id"])
        self.assertEqual(pf.job, "PRIVATE_EQUITY"); self.assertEqual(pf.cash_account("USD").balance, D(2_000_000_000))
        book = s.private_equity(w.id, pf.id)
        self.assertIn("pipeline", book); self.assertIn("initiatives", book); self.assertIsNone(book["totals"]["tvpi"])
        deal = affordable(w.pequity, book["pipeline"], cap=1.0e9)
        st = s.pe_structure(w.id, pf.id, deal["id"], deal["ask_multiple"], 4.0)
        self.assertEqual(st["leverage"], 4.0)
        r2 = s.pe_command(w.id, pf.id, "bid", {"deal_id": deal["id"], "multiple": deal["ask_multiple"] + 0.5, "leverage": 4.0})
        self.assertEqual(r2["status"], "PENDING")
        self.assertEqual(len(s.private_equity(w.id, pf.id)["pending_bids"]), 1)
        # a job without private equity in its mandate is refused
        r3 = s.create_world("fi", 42, start_date="2026-01-05", job="FIXED_INCOME_PM", clock_mode="SANDBOX")
        w3 = s.worlds[r3["world_id"]]
        with self.assertRaises(CommandError):
            w3.pe_bid(r3["portfolio_id"], deal["id"], deal["ask_multiple"], 3.0)
        self.assertIn("PRIVATE_EQUITY", [j["key"] for j in s.jobs()])

    def test_irr(self):
        self.assertAlmostEqual(irr([(date(2026, 1, 1), -100.0), (date(2028, 1, 1), 144.0)]), 0.2, places=2)
        self.assertIsNone(irr([(date(2026, 1, 1), -100.0)]))


if __name__ == "__main__":
    unittest.main()
