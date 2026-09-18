"""Investment banking (mandates, pitches, execution, fees, league table, replay), the housing view, the newspaper reader,
and the real-market gating of the invented desks."""
import os
import tempfile
import unittest
from datetime import date

try:
    from helpers import make_world, assert_ledger_invariants
except ImportError:
    from tests.helpers import make_world, assert_ledger_invariants

from finsim.api.service import Service
from finsim.engines.investment_banking import KINDS
from finsim.engines.realnews import NewspaperNews, RealNews, parse_newspaper
from finsim.money import D, ZERO
from finsim.store import EventStore
from finsim.world import World, CommandError

EDITION = """<html><body>
<section class="paper-section" aria-labelledby="s-top"><h2 id="s-top">Top Stories</h2><div class="stories">
<article class="story story--lead"><h3>The 10-year yield hits 5% as the Fed meets</h3><p class="story-deck">Long-end rates pulled equities lower.</p><div class="story-body"><p>Body text.</p></div></article>
<article class="story"><h3>Congress passes the spending bill</h3><p class="story-deck">The shutdown is averted.</p></article></div></section>
<section class="paper-section" aria-labelledby="s-econ"><h2 id="s-econ">The Economy</h2><div class="story-body">
<p>The New York Fed&rsquo;s Empire State survey came in at 7.6, down 13 points from a strong August reading but still in expansion territory. Components were mixed.</p>
<p>Short.</p></div></section>
<section class="paper-section" aria-labelledby="s-tech"><h2 id="s-tech">Technology &amp; AI</h2><article class="story"><h3>A chip launch</h3></article></section>
<section class="paper-section" aria-labelledby="s-moved"><h2 id="s-moved">What Moved Markets</h2><article class="story"><h3>NVDA leads a semiconductor rally</h3><p class="story-deck">Chips outperformed.</p></article></section>
</body></html>"""


class NewspaperTest(unittest.TestCase):
    def test_parse_the_political_economic_and_financial_sections_only(self):
        items = parse_newspaper(EDITION, "https://x/report.html", "2026-09-15T16:30:00-04:00")
        titles = [i["title"] for i in items]
        self.assertIn("The 10-year yield hits 5% as the Fed meets", titles)
        self.assertIn("Congress passes the spending bill", titles)
        self.assertNotIn("A chip launch", titles, "technology stays out")
        econ = [i for i in items if i["category"] == "ECONOMY"]
        self.assertEqual(len(econ), 1, "a prose section gives one item per real paragraph; short lines are skipped")
        self.assertTrue(econ[0]["title"].startswith("The New York Fed’s Empire State survey came in at 7.6"))
        self.assertIn("still in expansion territory", econ[0]["body"])
        self.assertEqual([i["category"] for i in items if "NVDA" in i["title"]], ["MARKETS"])
        self.assertTrue(all(i["link"].startswith("https://x/report.html#s-") for i in items))

    def test_reader_finds_the_day_s_editions_and_the_wire_puts_them_first(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "2026", "09"))
            with open(os.path.join(td, "2026", "09", "2026-09-15-pm.html"), "w", encoding="utf-8") as f:
                f.write(EDITION)
            import json
            with open(os.path.join(td, "index.json"), "w") as f:
                json.dump([{"date": "2026-09-15", "slot": "pm", "path": "reports/2026/09/2026-09-15-pm.html", "title": "t"},
                           {"date": "2026-09-15", "slot": "learn", "path": "reports/2026/09/2026-09-15-learn.html", "title": "l"},
                           {"date": "2026-09-14", "slot": "pm", "path": "reports/2026/09/2026-09-14-pm.html", "title": "old"}], f)
            paper = NewspaperNews(td)
            items = paper.headlines_for(date(2026, 9, 15))
            self.assertEqual(len(items), 4)                      # two top stories, one economy paragraph, one markets story
            self.assertTrue(all("PM edition" in i["publisher"] for i in items))
            self.assertEqual(paper.headlines_for(date(2026, 9, 16)), [])
            from datetime import datetime
            from zoneinfo import ZoneInfo
            noon = int(datetime(2026, 9, 15, 12, 0, tzinfo=ZoneInfo("America/New_York")).timestamp())
            rn = RealNews(fetch_yahoo=lambda q, n: [{"title": f"wire {q}", "publisher": "Wire", "link": f"https://w/{q}", "time": noon, "tickers": [q]}], fetch_fed=lambda: [], paper=paper)
            out = rn.headlines_for(date(2026, 9, 15), ["NVDA"])
            self.assertTrue(out[0]["publisher"].startswith("Logan's Daily Newspaper"))
            self.assertEqual([o["refs"] for o in out if "NVDA leads" in o["headline"]], [["NVDA"]])
            self.assertTrue(any(o["publisher"] == "Wire" for o in out))
            # the real paper next to this repository is readable too (when present)
            real = NewspaperNews()
            if real.dir:
                self.assertTrue(os.path.isfile(os.path.join(real.dir, "index.json")))


class HousingTest(unittest.TestCase):
    def test_housing_view_groups_the_names_and_derives_indicators_in_a_sandbox(self):
        s = Service(EventStore(":memory:"))
        r = s.create_world("h", 42, start_date="2026-01-05")
        h = s.housing(r["world_id"])
        ind = h["indicators"]
        self.assertTrue(ind["simulated"]); self.assertGreater(ind["mortgage30"], 3.0); self.assertGreater(ind["housing_starts"], 500)
        self.assertTrue({r["id"] for r in h["homebuilders"]} >= {"DHI", "LEN", "NVR", "PHM", "TOL"})
        self.assertTrue({r["id"] for r in h["mortgage_reits"]} >= {"AGNC", "NLY"})
        self.assertTrue(all(r["asset_class"] == "REIT" and r.get("subsector") for r in h["reits"]))
        self.assertTrue(any(r["subsector"] == "Towers" for r in h["reits"]))
        self.assertTrue(all(b["id"].startswith("AGY-") for b in h["agency_bonds"]))
        self.assertTrue({r["id"] for r in h["etfs"]} >= {"VNQ", "XHB", "ITB"})


class InvestmentBankingTest(unittest.TestCase):
    def test_mandates_pitches_execution_fees_and_replay(self):
        w, pf, _ = make_world()
        store = w.store
        w.contribute_capital(pf.id, "USD", D(500_000_000))
        ib = w.ibank
        book = ib.book(pf)
        pipe = book["pipeline"]
        self.assertGreaterEqual(len(pipe), 3)
        self.assertEqual(ib.pipeline(), ib.pipeline(), "deterministic within the month")
        self.assertEqual(set(k["label"] for k in book["market"]["kinds"].values()), set(v["label"] for v in KINDS.values()))
        self.assertEqual(book["stats"]["reputation"], 50.0)
        self.assertEqual([r["bank"] for r in book["league"] if r["bank"] == "You"], ["You"])
        with self.assertRaises(CommandError):
            w.ib_pitch(pf.id, pipe[0]["id"], 0.5, 10)                      # an absurd fee
        # pitch every kind on offer at the market fee with a sensible promise
        seen = set()
        for m in pipe:
            if m["kind"] in seen or m["status"] != "OPEN":
                continue
            seen.add(m["kind"])
            lo, hi = m["fee_norm"]
            promise = {"SELL_SIDE": m.get("public_multiple", 10) * 1.03, "IPO": m.get("public_multiple", 10) * 0.95, "BUY_SIDE": 30, "FOLLOW_ON": 4.0, "BOND": 20, "LEV_LOAN": 400}[m["kind"]]
            p = w.ib_pitch(pf.id, m["id"], (lo + hi) / 2, promise)
            self.assertEqual(p["status"], "PENDING")
        self.assertEqual(len(ib.book(pf)["pending_pitches"]), len(seen))
        w.advance(4)
        self.assertEqual(ib.book(pf)["pending_pitches"], [])
        self.assertTrue(pf.ib_engagements or pf.ib_log, "every pitch is answered: won or lost")
        assert_ledger_invariants(self, w, pf)
        # run the engagements to their end, taking each decision as it comes
        for _ in range(45):
            w.advance(1)
            for e in list(pf.ib_engagements.values()):
                if e.status == "DECISION" and e.pending:
                    if e.pending["type"] == "BIDS":
                        w.ib_decide(pf.id, e.id, "ACCEPT")
                    elif e.pending["type"] == "PRICE":
                        w.ib_decide(pf.id, e.id, "PRICE", e.pending["range"][1])
            if pf.ib_engagements and all(e.status not in ("ACTIVE", "DECISION") for e in pf.ib_engagements.values()):
                break
        for e in pf.ib_engagements.values():
            self.assertIn(e.status, ("CLOSED", "LOST", "PULLED"))
            self.assertTrue(e.outcome)
            if e.status == "CLOSED" and e.kind != "FOLLOW_ON":
                self.assertGreater(e.fee_earned, 0)
        st = ib.book(pf)["stats"]
        if any(e.status == "CLOSED" for e in pf.ib_engagements.values()):
            self.assertGreater(st["deals"], 0); self.assertNotEqual(st["reputation"], 50.0)
            self.assertTrue(any(n.category == "DEALS" for n in w.news) or all(e.kind in ("BUY_SIDE", "LEV_LOAN", "BOND", "SELL_SIDE", "IPO", "FOLLOW_ON") for e in pf.ib_engagements.values()))
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, w.id)
        self.assertEqual(w2.replay_errors, [])
        p2 = w2.portfolio(pf.id)
        for eid, e in pf.ib_engagements.items():
            e2 = p2.ib_engagements[eid]
            self.assertEqual((e.status, e.fee_earned, e.pnl, e.outcome, e.phase), (e2.status, e2.fee_earned, e2.pnl, e2.outcome, e2.phase))
        self.assertEqual(p2.ib_stats.get("reputation"), pf.ib_stats.get("reputation"))
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())
        self.assertEqual(len([n for n in w2.news if n.category == "DEALS"]), len([n for n in w.news if n.category == "DEALS"]))

    def test_job_routes_and_real_market_gating(self):
        s = Service(EventStore(":memory:"))
        r = s.create_world("ib", 42, start_date="2026-01-05", job="INVESTMENT_BANKER", clock_mode="SANDBOX")
        w = s.worlds[r["world_id"]]; pf = w.portfolio(r["portfolio_id"])
        self.assertEqual(pf.cash_account("USD").balance, D(500_000_000))
        book = s.investment_banking(w.id, pf.id)
        self.assertIn("pipeline", book); self.assertIn("league", book)
        m = book["pipeline"][0]
        lo, hi = m["fee_norm"]
        promise = {"SELL_SIDE": m.get("public_multiple", 10), "IPO": m.get("public_multiple", 10), "BUY_SIDE": 25, "FOLLOW_ON": 4.0, "BOND": 15, "LEV_LOAN": 375}[m["kind"]]
        r2 = s.ib_command(w.id, pf.id, "pitch", {"mandate_id": m["id"], "fee_pct": (lo + hi) / 2 if hi else 0, "promise": promise})
        self.assertEqual(r2["status"], "PENDING")
        self.assertTrue(s.world_info(w.id)["made_up_desks"])
        self.assertIn("INVESTMENT_BANKER", [j["key"] for j in s.jobs()])
        # a fixed-income job has no banking mandate; a real-market save has no invented desks at all
        r3 = s.create_world("fi", 42, start_date="2026-01-05", job="FIXED_INCOME_PM", clock_mode="SANDBOX")
        with self.assertRaises(CommandError):
            s.worlds[r3["world_id"]].ib_pitch(r3["portfolio_id"], m["id"], 0.01, 10)
        try:
            from test_live import career_world
        except ImportError:
            from tests.test_live import career_world
        cw, cpf, _ = career_world()
        self.assertEqual(cw.market_source, "REAL")
        with self.assertRaises(CommandError):
            cw.ib_pitch(cpf.id, m["id"], 0.01, 10)
        with self.assertRaises(CommandError):
            cw.pe_bid(cpf.id, "PED-0000-01", 10, 4)
        svc = Service(cw.store); svc.worlds[cw.id] = cw
        self.assertFalse(svc.world_info(cw.id)["made_up_desks"])


if __name__ == "__main__":
    unittest.main()


class AllBooksTest(unittest.TestCase):
    def test_overall_view_adds_the_books_up_and_flat_books_can_be_deleted(self):
        s = Service(EventStore(":memory:"))
        r = s.create_world("many", 42, start_date="2026-01-05", capital=10_000_000)
        w = s.worlds[r["world_id"]]
        b2 = s.create_portfolio(w.id, "Bonds", "PERSONAL", 5_000_000, "PROFESSIONAL", "SANDBOX", None)
        b3 = s.create_portfolio(w.id, "Equity - Short", "PERSONAL", 3_000_000, "PROFESSIONAL", "SANDBOX", None)
        p1, p2, p3 = r["portfolio_id"], b2["portfolio_id"], b3["portfolio_id"]
        s.place_order(w.id, p1, "AAPL", "BUY", 1000)
        s.place_order(w.id, p2, "UST-10Y", "BUY", 1_000_000)
        s.place_order(w.id, p2, "AAPL", "BUY", 500)
        w.advance(3)
        o = s.overall(w.id)
        self.assertEqual(o["world"]["name"], "many"); self.assertEqual(len(o["books"]), 3)
        self.assertAlmostEqual(float(o["totals"]["nav"]), sum(float(b["nav"]) for b in o["books"]), places=2)
        self.assertAlmostEqual(float(o["totals"]["day_pnl"]), sum(float(b["day_pnl"]) for b in o["books"]), places=2)
        aapl = next(p for p in o["positions"] if p["security_id"] == "AAPL")
        self.assertEqual(float(aapl["quantity"]), 1500.0); self.assertEqual(sorted(b["book"] for b in aapl["books"]), ["Bonds", "Main Portfolio"])
        self.assertIn("USD", o["cash"]); self.assertTrue(o["explain"])
        # a book with positions cannot be deleted; an empty one can; the last one never
        with self.assertRaises(CommandError):
            w.delete_portfolio(p2)
        d = s.delete_portfolio(w.id, p3)
        self.assertEqual(d["deleted"], p3); self.assertNotIn(p3, w.portfolios); self.assertEqual(len(s.overall(w.id)["books"]), 2)
        w2 = World.load(w.store, w.id)
        self.assertEqual(w2.replay_errors, []); self.assertNotIn(p3, w2.portfolios); self.assertIn(p2, w2.portfolios)
        with self.assertRaises(CommandError):
            w.delete_portfolio(p1)                           # not flat: it holds AAPL


class TreasuryTest(unittest.TestCase):
    def test_capital_sits_in_the_treasury_and_books_draw_from_it(self):
        s = Service(EventStore(":memory:"))
        r = s.create_world("firm", 42, start_date="2026-01-05", capital=50_000_000, treasury=True)
        w = s.worlds[r["world_id"]]; p1 = r["portfolio_id"]
        self.assertEqual(r["treasury"], 50_000_000.0)
        self.assertEqual(w.treasury_cash["USD"], D(50_000_000)); self.assertEqual(w.portfolio(p1).cash_account("USD").balance, D(0))
        self.assertEqual(s.world_info(w.id)["treasury"]["total_usd"], 50_000_000.0)
        with self.assertRaises(CommandError):
            w.allocate_from_treasury(p1, "USD", 60_000_000)                  # more than it holds
        s.treasury_command(w.id, "allocate", {"portfolio_id": p1, "amount": 20_000_000})
        pf1 = w.portfolio(p1)
        self.assertEqual(pf1.cash_account("USD").balance, D(20_000_000)); self.assertEqual(pf1.contributed_capital, D(20_000_000)); self.assertEqual(w.treasury_cash["USD"], D(30_000_000))
        # a second book draws its capital from the treasury; a third with fresh money leaves it alone
        b2 = s.create_portfolio(w.id, "Bonds", "PERSONAL", 10_000_000, "PROFESSIONAL", "SANDBOX", None, from_treasury=True)
        self.assertEqual(b2["allocated"], 10_000_000.0); self.assertEqual(w.treasury_cash["USD"], D(20_000_000))
        b3 = s.create_portfolio(w.id, "Fresh", "PERSONAL", 5_000_000, "PROFESSIONAL", "SANDBOX", None)
        self.assertEqual(w.treasury_cash["USD"], D(20_000_000)); self.assertEqual(w.portfolio(b3["portfolio_id"]).cash_account("USD").balance, D(5_000_000))
        # a book can return only what it can spare; deleting a flat book sweeps its cash back
        s.place_order(w.id, p1, "AAPL", "BUY", 1000)
        w.advance(1)
        with self.assertRaises(CommandError):
            w.return_to_treasury(p1, "USD", 20_000_000)
        w.return_to_treasury(p1, "USD", 5_000_000)
        self.assertEqual(w.treasury_cash["USD"], D(25_000_000)); self.assertEqual(pf1.contributed_capital, D(15_000_000))
        s.delete_portfolio(w.id, b3["portfolio_id"])
        self.assertEqual(w.treasury_cash["USD"], D(30_000_000))
        o = s.overall(w.id)
        self.assertEqual(o["totals"]["treasury"], 30_000_000.0); self.assertAlmostEqual(o["totals"]["nav_with_treasury"], o["totals"]["nav"] + 30_000_000.0, places=2)
        t = s.treasury(w.id)
        self.assertEqual(t["total_usd"], 30_000_000.0); self.assertTrue(any(l["kind"] == "TO_TREASURY" for l in t["log"]))
        assert_ledger_invariants(self, w, pf1)
        w2 = World.load(w.store, w.id)
        self.assertEqual(w2.replay_errors, []); self.assertEqual(w2.treasury_cash["USD"], D(30_000_000)); self.assertEqual(len(w2.treasury_log), len(w.treasury_log))
        self.assertEqual(w2.ledgers[p1].nav(), w.ledgers[p1].nav())
