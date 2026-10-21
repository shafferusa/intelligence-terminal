"""Listed / acquired private companies (owner 2026-10-21: "spacex is in private markets for some reason"; TERMINAL_DECISIONS.md
owner amendment 17:48: "a company that lists switches its mark to the live ticker from the listing date"; products/private.py
LISTED and MASTER SYNC).  Every expected number is worked by hand in a comment.  Nothing here touches the network or
~/.finsim: temp homes, FixedMarks / FakeProvider prices only.

The master (re-verified 2026-10-21): SpaceX PUBLIC SPCX from 2026-06-12 and Cerebras PUBLIC CBRS from 2026-05-14 (both Nasdaq
Global Select Market); xAI (2026-02-02) and Anysphere (2026-08-14) acquired by SpaceX; Anthropic, OpenAI, Kraken and Discord
FILED_IPO (still private names until their shares trade); 25 companies in all, so 21 private names.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests.terminal_helpers import DEFAULT_MARKS, _Log, assert_integrity, execute, make_runtime   # noqa: E402
from tests.test_terminal_private import DispatchMarks   # noqa: E402
from finsim.terminal import config, server as _server   # noqa: E402
from finsim.terminal.clock import FakeClock   # noqa: E402
from finsim.terminal.commands import RejectError   # noqa: E402
from finsim.terminal.products import private   # noqa: E402
from finsim.terminal.quotes.providers import FakeProvider   # noqa: E402

I1_PRIVATE = "I1:PRIVATE"
I1_USX = "I1:US_EXCHANGE"
SPACEX_LINE = "Listed 2026-06-12: SPCX on Nasdaq - trade it in US Exchange"
CEREBRAS_LINE = "Listed 2026-05-14: CBRS on Nasdaq - trade it in US Exchange"
XAI_LINE = "Acquired 2026-02-02 by SpaceX (SPCX) - no longer privately traded; trade SPCX in US Exchange"
ANYSPHERE_LINE = "Acquired 2026-08-14 by SpaceX (SPCX) - no longer privately traded; trade SPCX in US Exchange"
CEREBRAS_SRC = "https://www.cerebras.ai/press-release/cerebras-systems-announces-pricing-of-initial-public-offering"
# by name (SQLite binary order): "Anysphere, Inc. (Cursor)" < "Cerebras Systems Inc." < "Space Exploration ..." < "xAI Corp."
NO_LONGER_PRIVATE = ["PRV-ANYSPHERE", "PRV-CEREBRAS", "PRV-SPACEX", "PRV-XAI"]
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STATIC = os.path.join(ROOT, "finsim", "terminal", "static")


def q(rt, sql, params=()):
    with rt.db.reader() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(rt, sql, params=()):
    rows = q(rt, sql, params)
    return rows[0] if rows else None


class FakeCache:
    """Records ``QuoteCache.set_pins`` calls (the pin hook of the listed tickers)."""

    def __init__(self):
        self.calls = []

    def set_pins(self, priority, ids):
        self.calls.append((priority, list(ids)))
        return list(ids), []


# ---- the master file ----------------------------------------------------------------------------------------------------------
class ListedMasterFileTests(unittest.TestCase):
    def test_listing_status_reverified_and_sourced(self):
        with open(config.private_companies_path(), "rb") as f:
            raw = json.loads(f.read().decode("ascii"))
        self.assertEqual(("2026-10-21", "2026-10-21"), (raw["researched_at"], raw["listing_status_verified_at"]))
        rows = {r["company_id"]: r for r in raw["companies"]}
        self.assertEqual(25, len(rows))
        public = sorted(cid for cid, r in rows.items() if r["status"] == "PUBLIC")
        filed = sorted(cid for cid, r in rows.items() if r["status"] == "FILED_IPO")
        acquired = sorted(cid for cid, r in rows.items() if r.get("acquired"))
        self.assertEqual(["PRV-CEREBRAS", "PRV-SPACEX"], public)
        self.assertEqual(["PRV-ANTHROPIC", "PRV-DISCORD", "PRV-KRAKEN", "PRV-OPENAI"], filed)
        self.assertEqual(["PRV-ANYSPHERE", "PRV-XAI"], acquired)
        for cid in public:                       # PUBLIC = ticker + venue + first trade date + the listing source
            r = rows[cid]
            self.assertRegex(r["listed_on"], ISO, cid)
            self.assertIn(r["exchange"].upper(), private.VENUES, cid)
            for k in ("listing_source_url", "first_trade_source_url"):
                self.assertTrue(r[k].startswith("https://"), (cid, k))
        sx, cb = rows["PRV-SPACEX"], rows["PRV-CEREBRAS"]
        self.assertEqual(("SPCX", "2026-06-12", "Nasdaq Global Select Market", "https://www.nasdaqtrader.com/TraderNews.aspx?id=DTN2026-8"),
                         (sx["ticker"], sx["listed_on"], sx["exchange"], sx["listing_source_url"]))
        self.assertEqual(("CBRS", "2026-05-14", "Nasdaq Global Select Market", CEREBRAS_SRC), (cb["ticker"], cb["listed_on"], cb["exchange"], cb["listing_source_url"]))
        for cid in filed:                        # FILED_IPO stays until the shares trade: no ticker yet
            self.assertIsNone(rows[cid]["ticker"], cid)
        self.assertEqual({"PRV-ANTHROPIC": "2026-06-01", "PRV-OPENAI": "2026-06-08", "PRV-KRAKEN": "2025-11-19"},
                         {cid: rows[cid]["status_as_of"] for cid in filed if rows[cid].get("status_source_url")})
        for cid in acquired:                     # an acquisition is not a listing of the company itself: status stays PRIVATE
            a = rows[cid]["acquired"]
            self.assertEqual(("PRIVATE", "SpaceX", "SPCX"), (rows[cid]["status"], a["by"], a["acquirer_ticker"]))
            self.assertTrue(a["source_url"].startswith("https://www.sec.gov/"), cid)
        self.assertEqual(("2026-02-02", "2026-08-14"), (rows["PRV-XAI"]["acquired"]["on"], rows["PRV-ANYSPHERE"]["acquired"]["on"]))

    def test_venue_names_and_codes(self):
        self.assertEqual(("NMS", "Nasdaq", "Nasdaq Global Select Market"), private.venue("Nasdaq Global Select Market"))
        self.assertEqual(("NMS", "Nasdaq", "Nasdaq"), private.venue("nms"))
        self.assertEqual(("NYQ", "NYSE", "New York Stock Exchange"), private.venue(" New York  Stock Exchange "))
        self.assertEqual((None, "London Stock Exchange", "London Stock Exchange"), private.venue("London Stock Exchange"))
        self.assertEqual((None, None, None), private.venue(None))


# ---- scenarios on the seeded master (make_runtime, books P1 / F1 / I1) -------------------------------------------------------
class ListedScenarioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-prvl-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, at="2026-10-20T14:00:00-04:00"):
        rt = make_runtime(self.tmp, at=at, books="real")
        dm = DispatchMarks(rt, dict(DEFAULT_MARKS), as_of=rt.clock.epoch())
        rt.marks = dm
        rt.bus.marks_provider = dm
        rt.accounting.marks = dm
        rt.private = private
        private.register(rt.bus)
        self.rt = rt
        return rt

    def refused(self, **req):
        with self.assertRaises(RejectError) as cm:
            private.preview(self.rt, req)
        return cm.exception

    def position(self, iid, account=I1_PRIVATE):
        pos = [p for p in self.rt.accounting.positions(account) if p.instrument_id == iid]
        self.assertEqual(1, len(pos))
        return pos[0]

    def test_listed_and_acquired_companies_leave_the_private_names(self):
        rt = self.start()
        names = [c["company_id"] for c in private.companies(rt, account_id=I1_PRIVATE)]
        self.assertEqual(21, len(names))                                    # 25 in the master - 2 listed - 2 acquired = 21
        for cid in NO_LONGER_PRIVATE:
            self.assertNotIn(cid, names)
        self.assertIn("PRV-ANTHROPIC", names)                               # FILED_IPO stays a private name until its shares trade
        view = private.companies_view(rt, None, I1_PRIVATE)
        self.assertEqual(names, [c["company_id"] for c in view["companies"]])
        self.assertEqual(NO_LONGER_PRIVATE, [c["company_id"] for c in view["listed"]])
        listed = {c["company_id"]: c for c in view["listed"]}
        sx = listed["PRV-SPACEX"]["listing"]
        self.assertEqual(("LISTED", "COMPANY_LISTED", True, SPACEX_LINE, "NMS", "Nasdaq Global Select Market", "2026-06-12"),
                         (sx["kind"], sx["code"], sx["effective"], sx["message"], sx["exchange_code"], sx["exchange_full"], sx["listed_on"]))
        self.assertEqual(({"account_id": I1_USX, "instrument_id": "SPCX"}, "https://www.nasdaqtrader.com/TraderNews.aspx?id=DTN2026-8", False),
                         (sx["trade_in"], sx["source_url"], listed["PRV-SPACEX"]["privately_tradable"]))
        self.assertEqual(CEREBRAS_LINE, listed["PRV-CEREBRAS"]["listing"]["message"])
        xa = listed["PRV-XAI"]["listing"]
        self.assertEqual(("ACQUIRED", "COMPANY_ACQUIRED", XAI_LINE, {"account_id": I1_USX, "instrument_id": "SPCX"}), (xa["kind"], xa["code"], xa["message"], xa["trade_in"]))
        self.assertEqual(ANYSPHERE_LINE, listed["PRV-ANYSPHERE"]["listing"]["message"])
        # the filter reaches the listed lines too: "spcx" matches SpaceX's ticker only (the acquired rows carry no ticker of their own)
        v = private.companies_view(rt, "spcx", I1_PRIVATE)
        self.assertEqual(([], ["PRV-SPACEX"]), ([c["company_id"] for c in v["companies"]], [c["company_id"] for c in v["listed"]]))
        # SpaceX takes the listed SPCX mark from 2026-06-12: none in the fixture -> unavailable with the reason; 147.79 once SPCX prints
        m = private.private_mark(rt, "PRV-SPACEX")
        self.assertEqual("unavailable", m.quality)
        self.assertIn("listed 2026-06-12 (SPCX)", m.note)
        rt.marks.set("SPCX", "147.79")
        m = private.private_mark(rt, "PRV-SPACEX")
        self.assertEqual((Decimal("147.79"), "delayed", False, "SPCX"), (m.mark, m.quality, m.executable, m.inputs["ticker"]))
        # an acquired company keeps its last private round (xAI: valuation only -> unavailable) with the acquisition in the note
        m = private.private_mark(rt, "PRV-XAI")
        self.assertEqual("unavailable", m.quality)
        self.assertIn("acquired 2026-02-02 by SpaceX", m.note)

    def test_ticket_and_api_refuse_a_private_trade_in_a_company_no_longer_private(self):
        rt = self.start()
        base = {"account_id": I1_PRIVATE, "company_id": "PRV-SPACEX", "side": "BUY", "qty": 10, "price": "150.00"}
        e = self.refused(**base)
        self.assertEqual(("COMPANY_LISTED", SPACEX_LINE), (e.code, e.message))
        self.assertEqual(({"account_id": I1_USX, "instrument_id": "SPCX"}, 0), (e.details["trade_in"], e.details["position"]))
        self.assertEqual("COMPANY_LISTED", self.refused(**dict(base, kind="ROUND")).code)            # joining a round is a purchase too
        self.assertEqual("COMPANY_LISTED", self.refused(**dict(base, side="SELL")).code)             # nothing held here: nothing to close
        self.assertEqual("COMPANY_LISTED", self.refused(**{k: v for k, v in base.items() if k != "price"}).code)   # before PRICE_REQUIRED
        self.assertEqual("NOT_TRADEABLE_HERE", self.refused(**dict(base, side="SELL_SHORT")).code)   # shorts keep their own code
        e = self.refused(**dict(base, company_id="PRV-CEREBRAS"))
        self.assertEqual(("COMPANY_LISTED", CEREBRAS_LINE), (e.code, e.message))
        e = self.refused(**dict(base, company_id="PRV-XAI"))
        self.assertEqual(("COMPANY_ACQUIRED", XAI_LINE), (e.code, e.message))
        # the API path (the bus command the ticket submits) answers the same 400 and stores nothing
        r = execute(rt, "private.trade", dict(base), "ui:listed-spcx-0001")
        self.assertEqual((False, 400, "COMPANY_LISTED", SPACEX_LINE), (r.ok, r.http, r.code, r.message))
        r = execute(rt, "private.trade", dict(base, company_id="PRV-ANYSPHERE"), "ui:acquired-anysphere-0001")
        self.assertEqual((400, "COMPANY_ACQUIRED", ANYSPHERE_LINE), (r.http, r.code, r.message))
        self.assertEqual((0, 0), (one(rt, "SELECT COUNT(*) n FROM fills")["n"], one(rt, "SELECT COUNT(*) n FROM commands WHERE kind='private.trade'")["n"]))
        # FILED_IPO is still private: BUY 10 @ 100.00 -> gross 10 x 100 = 1,000.00; fee to_c(1,000 x 0.03) = 30.00; net 1,030.00
        pv = private.preview(rt, dict(base, company_id="PRV-ANTHROPIC", price="100.00"))
        self.assertEqual(("1000.00", "30.00", "1030.00", [], None), (pv["gross"], pv["fee"], pv["net"], pv["blockers"], pv["company"]["listing"]))

    def test_a_held_position_follows_its_listing_and_can_only_be_closed(self):
        rt = self.start()
        r = execute(rt, "private.add_company", {"company_id": "PRV-TESTCO", "name": "Testco, Inc.", "last_round": {"round_date": "2026-09-01", "round_type": "Series X",
                                                                                                                "price_per_share": "100.00", "source_url": "test fixture"}},
                    "ui:add-testco-0001")
        self.assertTrue(r.ok, r.body)
        # Tue 2026-10-20: BUY 1,000 @ 95.00 -> gross 95,000.00; fee to_c(95,000 x 0.03) = 2,850.00; net 97,850.00; lock-up 6 months -> 2027-04-20
        r = execute(rt, "private.trade", {"account_id": I1_PRIVATE, "company_id": "PRV-TESTCO", "side": "BUY", "qty": 1000, "price": "95.00"}, "ui:buy-testco-0001")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((9_500_000, 285_000, "2027-04-20"), (r.body["fill"]["gross_c"], r.body["fill"]["fee_c"], r.body["lot"]["lock_until"]))
        # the company lists on Mon 2026-11-02 as TSTQ on the Nasdaq Global Select Market: the ticker is registered as a US Exchange instrument
        r = execute(rt, "private.set_status", {"company_id": "PRV-TESTCO", "status": "PUBLIC", "ticker": "TSTQ", "listed_on": "2026-11-02",
                                               "exchange": "Nasdaq Global Select Market", "source_url": "https://example.com/tstq-listing"}, "ui:list-testco-0001")
        self.assertTrue(r.ok, r.body)
        self.assertEqual({"ticker": "TSTQ", "instrument_id": "TSTQ", "in_master": True, "created": True, "kind": "EQUITY", "support": "FULL", "us_exchange": True},
                         r.body["listed_instrument"])
        inst = rt.instruments.get("TSTQ")
        self.assertEqual(("EQUITY", "FULL", ("US_EXCHANGE", "PB"), "US_EQUITY", "TSTQ", "NMS"),
                         (inst.kind, inst.support, inst.account_kinds, inst.session_group, inst.yahoo_symbol, inst.spec["exchange"]))
        self.assertEqual(("LISTED", False, "Lists 2026-11-02: TSTQ on Nasdaq - private trading until then", True),
                         (r.body["listing"]["kind"], r.body["listing"]["effective"], r.body["listing"]["message"], r.body["privately_tradable"]))
        # before the listing date it is still a private name with the private mark and the coming listing in the note
        self.assertIn("PRV-TESTCO", [c["company_id"] for c in private.companies(rt)])
        p = self.position("PRV-TESTCO")
        self.assertEqual((Decimal("100"), "private"), (p.mark.mark, p.mark.quality))
        self.assertIn("lists 2026-11-02 (TSTQ)", p.mark.note)
        # BUY 10 @ 99.00 is still a private secondary today -> gross 990.00; fee to_c(990 x 0.03) = 29.70
        pv = private.preview(rt, {"account_id": I1_PRIVATE, "company_id": "PRV-TESTCO", "side": "BUY", "qty": 10, "price": "99.00"})
        self.assertEqual(("990.00", "29.70", []), (pv["gross"], pv["fee"], pv["warnings"]))
        # Mon 2026-11-02 10:00 ET: listed; TSTQ prints 130.00 -> the held lots take the listed mark
        rt.clock.set("2026-11-02T10:00:00-05:00")
        rt.marks.set("TSTQ", "130.00")
        self.assertNotIn("PRV-TESTCO", [c["company_id"] for c in private.companies(rt)])
        held = [c for c in private.listed_companies(rt, account_id=I1_PRIVATE) if c["company_id"] == "PRV-TESTCO"][0]
        self.assertEqual(("Listed 2026-11-02: TSTQ on Nasdaq - trade it in US Exchange", {"account_id": I1_USX, "instrument_id": "TSTQ"}, 1000),
                         (held["listing"]["message"], held["listing"]["trade_in"], held["position"]["qty"]))
        # MV 1,000 x 130 = 130,000.00 = 13,000,000 c; cost 9,500,000 c; unrealised 13,000,000 - 9,500,000 = 3,500,000 c (35,000.00)
        p = self.position("PRV-TESTCO")
        self.assertEqual((1000, 9_500_000, 13_000_000, 3_500_000, "delayed"), (p.qty, p.cost_c, p.mv_c, p.unrealised_c, p.mark.quality))
        self.assertIn("listed 2026-11-02 (TSTQ)", p.mark.note)
        # no new private purchase; a SELL beyond the held 1,000 is OVERSELL (the listing check lets a held position through)
        e = self.refused(account_id=I1_PRIVATE, company_id="PRV-TESTCO", side="BUY", qty=10, price="130.00")
        self.assertEqual(("COMPANY_LISTED", "Listed 2026-11-02: TSTQ on Nasdaq - trade it in US Exchange", 1000), (e.code, e.message, e.details["position"]))
        self.assertEqual("OVERSELL", self.refused(account_id=I1_PRIVATE, company_id="PRV-TESTCO", side="SELL", qty=1500, price="128.00").code)
        # SELL 400 @ 128.00 closes pre-listing shares: gross 400 x 128 = 51,200.00; fee to_c(51,200 x 0.03) = 1,536.00; net 49,664.00;
        # FIFO cost 95,000 x 400 / 1,000 = 38,000.00; realised 51,200 - 38,000 = 13,200.00; discount (128 / 130 - 1) x 100 = -1.538... -> -1.54;
        # settles 2026-11-02 + 30 = 2026-12-02; warnings: the lock-up (until 2027-04-20) and the listing
        sell = {"account_id": I1_PRIVATE, "company_id": "PRV-TESTCO", "side": "SELL", "qty": 400, "price": "128.00"}
        pv = private.preview(rt, sell)
        self.assertEqual(("51200.00", "1536.00", "49664.00", 3_800_000, 1_320_000, "-1.54", "2026-12-02"),
                         (pv["gross"], pv["fee"], pv["net"], pv["cost_relieved_c"], pv["realised_c"], pv["discount_pct"], pv["settle_date"]))
        self.assertEqual(["LOCKUP", "LISTED"], [w["code"] for w in pv["warnings"]])
        self.assertTrue(pv["warnings"][1]["message"].startswith("Listed 2026-11-02: TSTQ on Nasdaq - trade it in US Exchange: this SELL closes pre-listing shares"))
        self.assertEqual([], pv["blockers"])
        r = execute(rt, "private.trade", sell, "ui:sell-testco-0001")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("NEGOTIATED", 1_320_000, ["LOCKUP", "LISTED"]), (r.body["fill"]["origin"], r.body["fill"]["realised_c"], [w["code"] for w in r.body["warnings"]]))
        # 600 left: cost 9,500,000 - 3,800,000 = 5,700,000 c; MV 600 x 130 = 7,800,000 c; unrealised 2,100,000 c
        p = self.position("PRV-TESTCO")
        self.assertEqual((600, 5_700_000, 7_800_000, 2_100_000, 1_320_000), (p.qty, p.cost_c, p.mv_c, p.unrealised_c, p.realised_itd_c))
        assert_integrity(self, rt, "full")
        # the held listed ticker is pinned for quotes (priority PRIVATE_LISTED) while lots are held
        self.assertEqual(["TSTQ"], private.listed_held_tickers(rt))
        rt.cache = FakeCache()
        self.assertEqual(["TSTQ"], private.refresh_pins(rt))
        self.assertEqual([("PRIVATE_LISTED", ["TSTQ"])], rt.cache.calls)
        # SELL the last 600 @ 128.00: gross 76,800.00; fee to_c(76,800 x 0.03) = 2,304.00; the lot's whole remaining cost 57,000.00 is relieved;
        # realised 76,800 - 57,000 = 19,800.00 = 1,980,000 c
        r = execute(rt, "private.trade", dict(sell, qty=600), "ui:sell-testco-0002")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((7_680_000, 230_400, 5_700_000, 1_980_000), (r.body["fill"]["gross_c"], r.body["fill"]["fee_c"], r.body["fill"]["cost_relieved_c"], r.body["fill"]["realised_c"]))
        self.assertEqual([], private.listed_held_tickers(rt))
        self.assertEqual([], private.refresh_pins(rt))
        self.assertEqual(("PRIVATE_LISTED", []), rt.cache.calls[-1])
        self.assertEqual("COMPANY_LISTED", self.refused(**dict(sell, qty=1)).code)     # nothing held any more: nothing to close
        # NAV(I1:ALL) = 1,000,000,000 - 97,850 (buy + fee) + 49,664 + 74,496 (sales net of fees) = 1,000,026,310.00
        self.assertEqual(100_002_631_000, rt.accounting.nav("I1:ALL").nav_c)
        assert_integrity(self, rt, "full")

    def test_master_sync_moves_an_older_database_forward_only(self):
        rt = self.start()
        with rt.db.writer() as tx:      # an older database: seeded before Cerebras's listing and Anthropic's filing were researched
            tx.conn.execute("UPDATE private_companies SET status='PRIVATE', ticker=NULL, notes='old note' WHERE company_id IN ('PRV-CEREBRAS', 'PRV-ANTHROPIC')")
            tx.conn.execute("UPDATE instruments SET spec_json=? WHERE instrument_id='PRV-CEREBRAS'", (json.dumps({"sector": "AI chips", "country": "US", "status": "PRIVATE"}),))
        rt.instruments.load()
        self.assertIn("PRV-CEREBRAS", [c["company_id"] for c in private.companies(rt)])
        self.assertIsNone(rt.instruments.get("CBRS"))
        # the owner moved Discord back to PRIVATE (an owner-set status is never moved by the file) and added a listed company of his own
        r = execute(rt, "private.set_status", {"company_id": "PRV-DISCORD", "status": "PRIVATE"}, "ui:owner-discord-0001")
        self.assertTrue(r.ok, r.body)
        r = execute(rt, "private.add_company", {"name": "Owner Listed Co", "status": "PUBLIC", "ticker": "OWNQ", "listed_on": "2026-10-01", "exchange": "NYSE",
                                                "source_url": "https://example.com/ownq"}, "ui:owner-ownq-0001")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((True, "Listed 2026-10-01: OWNQ on NYSE - trade it in US Exchange"), (r.body["listed_instrument"]["created"], r.body["listing"]["message"]))
        r = execute(rt, "private.sync_master", {"file_sha256": "test"}, "private:sync:test-0001", actor="init")
        self.assertTrue(r.ok, r.body)
        moves = [(m["company_id"], m["from"], m["to"], m["ticker"], m["source_url"]) for m in r.body["status_changes"]]
        self.assertEqual([("PRV-ANTHROPIC", "PRIVATE", "FILED_IPO", None, "https://www.anthropic.com/news/confidential-draft-s1-sec"),
                          ("PRV-CEREBRAS", "PRIVATE", "PUBLIC", "CBRS", CEREBRAS_SRC)], moves)
        self.assertEqual([], r.body["problems"])
        self.assertEqual(["PRV-ANTHROPIC", "PRV-CEREBRAS"], [c for c in r.body["notes"] if c in ("PRV-ANTHROPIC", "PRV-CEREBRAS")])
        rows = {x["company_id"]: (x["status"], x["ticker"]) for x in q(rt, "SELECT company_id, status, ticker FROM private_companies")}
        self.assertEqual((("PUBLIC", "CBRS"), ("FILED_IPO", None), ("PRIVATE", None)), (rows["PRV-CEREBRAS"], rows["PRV-ANTHROPIC"], rows["PRV-DISCORD"]))
        spec = json.loads(one(rt, "SELECT spec_json FROM instruments WHERE instrument_id='PRV-CEREBRAS'")["spec_json"])
        self.assertEqual(("2026-05-14", "Nasdaq", "Nasdaq Global Select Market", "NMS", CEREBRAS_SRC, "CBRS"),
                         (spec["listed_on"], spec["exchange"], spec["exchange_full"], spec["exchange_code"], spec["listing_source_url"], spec["ticker"]))
        inst = rt.instruments.get("CBRS")                    # registered through InstrumentMaster.upsert_from_yahoo_meta, no network call
        self.assertEqual(("EQUITY", "FULL", ("US_EXCHANGE", "PB"), "US_EQUITY", "CBRS", "NMS"),
                         (inst.kind, inst.support, inst.account_kinds, inst.session_group, inst.yahoo_symbol, inst.spec["exchange"]))
        self.assertTrue(rt.instruments.tradeable_in(inst, "US_EXCHANGE"))
        made = {x["ticker"]: (x["created"], x["us_exchange"]) for x in r.body["listed_instruments"]}
        self.assertEqual({"CBRS": (True, True), "OWNQ": (False, True), "SPCX": (False, True)}, made)   # SPCX is a universe row: never changed
        ev = q(rt, "SELECT e.ref_id FROM events e JOIN commands c ON c.id = e.command_id WHERE e.type='PRIVATE_STATUS_CHANGED' AND c.kind='private.sync_master' ORDER BY e.id")
        self.assertEqual(["PRV-ANTHROPIC", "PRV-CEREBRAS"], [x["ref_id"] for x in ev])
        att = q(rt, "SELECT severity, code, ref_id, message FROM attention WHERE source='private'")
        self.assertEqual([("INFO", "PRIVATE_COMPANY_LISTED", "PRV-CEREBRAS", f"Cerebras Systems Inc.: {CEREBRAS_LINE} (researched master; source {CEREBRAS_SRC})")],
                         [(a["severity"], a["code"], a["ref_id"], a["message"]) for a in att])
        names = [c["company_id"] for c in private.companies(rt)]
        self.assertNotIn("PRV-CEREBRAS", names)
        self.assertIn("PRV-OWNERLISTEDCO", [c["company_id"] for c in private.listed_companies(rt)])
        # forward only and idempotent: a second run moves and fills nothing, and raises no new attention
        r = execute(rt, "private.sync_master", {"file_sha256": "test"}, "private:sync:test-0002", actor="init")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(([], [], [], []), (r.body["status_changes"], r.body["filled"], r.body["notes"], [x for x in r.body["listed_instruments"] if x["created"]]))
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM attention WHERE source='private'")["n"])
        d = one(rt, "SELECT status, ticker FROM private_companies WHERE company_id='PRV-DISCORD'")
        self.assertEqual(("PRIVATE", None), (d["status"], d["ticker"]))                 # the owner's status stands
        with rt.db.writer() as tx:                           # the housekeeping hook runs the same sync: nothing new in the file
            self.assertEqual(0, private.reseed(tx, rt))

# ---- a served runtime: server.build_runtime (offline, FakeProvider) binds private, whose bind() runs the master sync -------------
class ListedServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-prvl-srv-")
        os.environ[config.HOME_ENV] = self.tmp
        os.environ[config.DB_ENV] = os.path.join(self.tmp, "terminal.db")
        self.clock = FakeClock("2026-10-20T14:00:00-04:00")          # Tuesday 14:00 ET: US_EQUITY open
        self.fp = FakeProvider(self.clock)
        self.rt = _server.build_runtime(self.tmp, os.path.join(self.tmp, "terminal.db"), clock=self.clock, offline=True, fake_provider=self.fp, log=_Log(),
                                        books=list(config.BOOKS), fetch_threads=0, news_enabled=False)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def handle(self, method, path, body=None):
        return self.rt.router.handle(method, path, None, body, "127.0.0.1", None)

    def test_spcx_and_cbrs_resolve_as_us_exchange_instruments_with_a_fake_provider(self):
        rt = self.rt
        self.assertEqual("bound", rt.modules["private"])
        cmds = q(rt, "SELECT request_id, http_status, actor FROM commands WHERE kind='private.sync_master'")
        self.assertEqual(1, len(cmds))
        self.assertTrue(cmds[0]["request_id"].startswith("private:sync:v1:"))
        self.assertEqual((200, "init"), (cmds[0]["http_status"], cmds[0]["actor"]))
        for sym in ("SPCX", "CBRS"):
            status, inst, _ = self.handle("GET", f"/api/instruments/resolve?symbol={sym}")
            self.assertEqual(200, status, inst)
            self.assertEqual((sym, "EQUITY", "FULL", ["US_EXCHANGE", "PB"], "US_EQUITY"), (inst["instrument_id"], inst["kind"], inst["support"], inst["account_kinds"], inst["session_group"]))
        self.assertEqual("NMS", inst["exchange"])                     # CBRS registered from the sourced venue (Nasdaq Global Select Market)
        self.assertEqual(0, self.fp.calls.get("meta", 0))            # no Yahoo meta call: resolvable without the network
        # a Yahoo-style SPCX print 120 s old, then a market BUY 10 SPCX previews in I1:US_EXCHANGE
        qt = self.clock.epoch() - 120
        self.fp.set("SPCX", last="147.79", quote_time=qt, bar=(qt - qt % 60 - 60, 50000))
        rt.cache.subscribe(["SPCX"], 300, "PAGE")
        rt.worker.tick()
        status, pv, _ = self.handle("POST", "/api/orders/preview", {"account_id": I1_USX, "instrument_id": "SPCX", "side": "BUY", "qty": 10, "order_type": "MARKET", "tif": "DAY"})
        self.assertEqual(200, status, pv)
        self.assertTrue(pv["ok"], pv.get("blockers"))
        # gross 10 x 147.79 = 1,477.90; fee max(10 x 0.005, 1.00) = max(0.05, 1.00) = 1.00
        self.assertEqual(("1477.90", "1.00"), (pv["economics"]["gross"], pv["economics"]["fee"]))
        # the Private Markets page data: 21 private names, the four no longer private, SpaceX's line + link target + its live SPCX mark
        status, body, _ = self.handle("GET", "/api/private/companies?account_id=I1:PRIVATE")
        self.assertEqual(200, status, body)
        self.assertEqual((21, NO_LONGER_PRIVATE), (len(body["companies"]), [c["company_id"] for c in body["listed"]]))
        sx = [c for c in body["listed"] if c["company_id"] == "PRV-SPACEX"][0]
        self.assertEqual((SPACEX_LINE, {"account_id": I1_USX, "instrument_id": "SPCX"}), (sx["listing"]["message"], sx["listing"]["trade_in"]))
        self.assertEqual((Decimal("147.79"), "delayed"), (Decimal(sx["mark"]["value"]), sx["mark"]["quality"]))
        # the API refuses a private trade in SpaceX with the line (preview 400; trade 400, nothing stored)
        req = {"account_id": I1_PRIVATE, "company_id": "PRV-SPACEX", "side": "BUY", "qty": 10, "price": "147.79"}
        status, out, _ = self.handle("POST", "/api/private/preview", dict(req))
        self.assertEqual((400, "COMPANY_LISTED", SPACEX_LINE), (status, out["error"]["code"], out["error"]["message"]))
        self.assertEqual({"account_id": I1_USX, "instrument_id": "SPCX"}, out["error"]["details"]["trade_in"])
        status, out, _ = self.handle("POST", "/api/private/trades", dict(req, request_id="ui:prvl-spcx-0001"))
        self.assertEqual((400, "COMPANY_LISTED"), (status, out["error"]["code"]))
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM fills")["n"])
        # a restart with the same master file replays the sync (no second command, no attention from the private desk)
        res = private.bind(rt)
        self.assertEqual((True, True), (res["sync"]["ok"], res["sync"]["replayed"]))
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM commands WHERE kind='private.sync_master'")["n"])
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM attention WHERE source='private'")["n"])


# ---- the UI files -----------------------------------------------------------------------------------------------------------------
class ListedStaticTests(unittest.TestCase):
    def test_page_and_ticket_keep_listed_companies_out_of_private_trading(self):
        from tests.test_terminal_static import js_balance
        with open(os.path.join(STATIC, "pages", "private_markets.js"), "rb") as f:
            page = f.read().decode("ascii")
        with open(os.path.join(STATIC, "components", "privateticket.js"), "rb") as f:
            ticket = f.read().decode("ascii")
        for src in (page, ticket):
            self.assertEqual((0, 0, 0), js_balance(src))
            self.assertFalse(re.search(r"https?://", src), "no external references")
        for need in ("splitCompanies", "c.listing.effective", "d.listed", "/api/instruments/resolve", "T.router.instrument", "no longer private", "trade_in"):
            self.assertIn(need, page, need)
        for need in ("COMPANY_LISTED", "COMPANY_ACQUIRED", "refusal(current(), side.value)", "/api/instruments/resolve", "sell only", "in US Exchange"):
            self.assertIn(need, ticket, need)


if __name__ == "__main__":
    unittest.main()
