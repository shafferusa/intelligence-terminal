"""WP-19 Private Markets tests (TERMINAL_DESIGN.md §13.13, §19.17, §7.2, §8.6 PRIVATE row, §16 private routes; TERMINAL_WORKPLAN.md
WP-19).  Every expected number is worked by hand in a comment from the inputs.

Scaffolding: ``make_runtime(books="real")`` (P1 / F1 / I1 as §19.14) with a ``DispatchMarks`` provider — a ``FixedMarks`` whose
``mark()`` routes PRIVATE instruments through ``services.private.private_mark(services, inst)``, exactly the call the real
``MarkProvider`` makes for kind PRIVATE (``marks.FrozenMarks._dispatch``), so the accounting read models value private lots
through the module under test.  The 2026-11-19 settlement is posted by a test-local handler that mirrors §5.4 / WP-08's
SETTLE_DUE (``Dr 2100 / Cr 1010``).  Nothing here touches the network or ``~/.finsim``.
"""
from __future__ import annotations

import copy
import http.client
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

from tests.terminal_helpers import DEFAULT_MARKS, assert_integrity, bal, execute, make_runtime   # noqa: E402
from finsim.terminal import config, risk, tables   # noqa: E402
from finsim.terminal.commands import RejectError   # noqa: E402
from finsim.terminal.ledger import Entry, cr, dr   # noqa: E402
from finsim.terminal.products import private   # noqa: E402
from finsim.terminal.quotes import FixedMarks, QuoteRecord, freshness   # noqa: E402

I1_PRIVATE = "I1:PRIVATE"
I1_TREASURY = "I1:TREASURY"
TESTCO = "PRV-TESTCO"
STATIC = os.path.join(ROOT, "finsim", "terminal", "static")
DATA_FILE = config.private_companies_path()
# §13.13 / the owner's amendment: the named companies (by company_id)
NAMED_IDS = ["PRV-ANTHROPIC", "PRV-OPENAI", "PRV-SPACEX", "PRV-XAI", "PRV-STRIPE", "PRV-DATABRICKS", "PRV-ANDURIL", "PRV-SCALEAI", "PRV-PERPLEXITY",
             "PRV-MISTRAL", "PRV-SSI", "PRV-ANYSPHERE", "PRV-GROQ", "PRV-REVOLUT", "PRV-BYTEDANCE", "PRV-CANVA", "PRV-EPICGAMES", "PRV-DISCORD",
             "PRV-FANATICS", "PRV-KRAKEN", "PRV-RAMP", "PRV-RIPPLING", "PRV-DEEL", "PRV-VERCEL", "PRV-CEREBRAS"]
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DispatchMarks(FixedMarks):
    """FixedMarks + the MarkProvider's PRIVATE dispatch (``services.private.private_mark(services, inst)``)."""

    def __init__(self, rt, marks=None, as_of=None, frozen=False):
        super().__init__(marks, as_of=as_of, frozen=frozen)
        self.rt = rt

    def snapshot(self, ids=None):
        snap = DispatchMarks(self.rt, {}, as_of=self._as_of, frozen=True)
        snap._marks = dict(self._marks)
        snap._quotes = {k: copy.deepcopy(v) for k, v in self._quotes.items()}
        return snap

    def mark(self, instrument_id):
        inst = self.rt.instruments.get(instrument_id)
        mod = getattr(self.rt, "private", None)
        if inst is not None and inst.kind == "PRIVATE" and mod is not None:
            return mod.private_mark(self.rt, inst)
        return super().mark(instrument_id)


def _h_test_settle(ctx, payload):
    """What WP-08's SETTLE_DUE posts for one settlement row (§5.4): payable ``Dr 2100 / Cr 1010``, receivable ``Dr 1010 / Cr 1200``."""
    s = ctx.conn.execute("SELECT * FROM settlements WHERE settle_id=?", (payload["settle_id"],)).fetchone()
    amt = int(s["amount_c"])
    aid, ccy = s["account_id"], s["currency"]
    lines = [dr(aid, "2100", ccy, -amt), cr(aid, "1010", ccy, -amt)] if amt < 0 else [dr(aid, "1010", ccy, amt), cr(aid, "1200", ccy, amt)]
    entry_id = ctx.post(Entry("SETTLE", f"settlement {s['settle_id']} due {s['due_date']}", lines, f"settle:{s['settle_id']}", ref_type="settlement", ref_id=s["settle_id"]))
    ctx.conn.execute("UPDATE settlements SET status='SETTLED', settled_ts=?, entry_id=? WHERE settle_id=?", (ctx.ts, entry_id, s["settle_id"]))
    ctx.touched_accounts.add(aid)
    return {"entry_id": entry_id}


def q(rt, sql, params=()):
    with rt.db.reader() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(rt, sql, params=()):
    rows = q(rt, sql, params)
    return rows[0] if rows else None


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---- the master file (§19.17 first bullet: the "never guessed" guard) --------------------------------------------------------
class PrivateMasterFileTests(unittest.TestCase):
    def test_master_file_rows_are_sourced(self):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        rows = raw["companies"]
        self.assertGreaterEqual(len(rows), 20)
        ids = [r["company_id"] for r in rows]
        self.assertEqual(len(ids), len(set(ids)), "duplicate company ids")
        for cid in NAMED_IDS:
            self.assertIn(cid, ids, f"named company missing: {cid}")
        priced = 0
        for r in rows:
            cid = r["company_id"]
            self.assertRegex(cid, r"^PRV-[A-Z0-9]+$")
            self.assertTrue(r.get("name"))
            self.assertIn(r["status"], ("PRIVATE", "FILED_IPO", "PUBLIC"), cid)
            if r["status"] == "PUBLIC":
                self.assertTrue(r.get("ticker"), f"{cid}: PUBLIC needs a ticker")
                self.assertRegex(r.get("listed_on") or "", ISO, f"{cid}: PUBLIC needs listed_on")
            lr = r["last_round"]
            self.assertIsInstance(lr, dict, cid)
            self.assertRegex(lr["round_date"], ISO, cid)
            self.assertTrue(lr["round_type"], cid)
            numbers = [("post_money_usd", lr.get("post_money_usd")), ("price_per_share", lr.get("price_per_share"))]
            for name, v in numbers:
                if v is None:
                    continue
                self.assertFalse(isinstance(v, bool), f"{cid}: {name} is a bool")
                self.assertNotIsInstance(v, float, f"{cid}: {name} must not be a float")
                self.assertGreater(Decimal(str(v)), 0, f"{cid}: {name} must be positive")
                # the rule under test: a number without a source and an as-of date is a failure
                src = lr.get("source_url")
                self.assertTrue(isinstance(src, str) and src.startswith("http"), f"{cid}: {name} has no source_url")
                self.assertRegex(lr.get("as_of") or "", ISO, f"{cid}: {name} has no as_of")
            if lr.get("price_per_share") is not None:
                priced += 1
                self.assertIsInstance(lr["price_per_share"], str, f"{cid}: price_per_share is a decimal string")
            self.assertTrue(lr.get("source_url"), f"{cid}: every seeded round needs a source (the seed skips rows without one)")
        # some names do have a reported per-share price (Revolut, ByteDance, the SpaceX and Cerebras IPO prices)
        self.assertGreaterEqual(priced, 3)
        with open(DATA_FILE, "rb") as f:
            f.read().decode("ascii")


# ---- scenarios ----------------------------------------------------------------------------------------------------------------
class PrivateScenarioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-priv-")
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
        rt.private = private                      # what the integrator binds: services.private
        private.register(rt.bus)
        rt.bus.register("test.settle", _h_test_settle)
        self.rt = rt
        return rt

    def add_testco(self, price="100.00"):
        """§19.17: PRV-TESTCO with a round on 2026-09-01 at price_per_share 100.00 (source 'test fixture')."""
        r = execute(self.rt, "private.add_company", {"company_id": TESTCO, "name": "Testco, Inc.", "sector": "Software", "country": "US",
                                                     "last_round": {"round_date": "2026-09-01", "round_type": "Series X", "price_per_share": price,
                                                                    "source_url": "test fixture", "as_of": "2026-09-01"}}, "ui:add-testco-0001")
        self.assertTrue(r.ok, r.body)
        return r

    def trade(self, rid, **payload):
        payload.setdefault("account_id", I1_PRIVATE)
        payload.setdefault("company_id", TESTCO)
        return execute(self.rt, "private.trade", payload, rid)

    def lines(self, entry_id):
        return [(l["gl"], l["amount_c"], l["instrument_id"]) for l in q(self.rt, "SELECT gl, amount_c, instrument_id FROM journal_lines WHERE entry_id=? ORDER BY line_no", (entry_id,))]

    def position(self, account=I1_PRIVATE, iid=TESTCO):
        pos = [p for p in self.rt.accounting.positions(account) if p.instrument_id == iid]
        self.assertEqual(1, len(pos), [p.instrument_id for p in self.rt.accounting.positions(account)])
        return pos[0]

    # ---- §19.17 end to end ---------------------------------------------------------------------------------------------------
    def test_19_17_buy_round_sell_public_switch(self):
        rt = self.start()
        self.add_testco()
        m = private.private_mark(rt, TESTCO)
        self.assertEqual((Decimal("100"), "private", "private_round", False, "last round 2026-09-01 (Series X)"), (m.mark, m.quality, m.method, m.executable, m.note))
        # I1 starts with 1,000,000,000.00 in Treasury (§19.14); I1:PRIVATE holds 0
        self.assertEqual(100_000_000_000, bal(rt, I1_TREASURY, "1010"))
        self.assertEqual(0, bal(rt, I1_PRIVATE, "1010"))
        # BUY 10,000 @ 95.00 (5 % below the 100.00 mark), fee 3 %, T+30 calendar, lock-up 6 months, on Tue 2026-10-20:
        # gross 10,000 x 95 = 950,000.00 = 95,000,000 c; fee to_c(950,000 x 0.03) = 28,500.00 = 2,850,000 c; need 978,500.00 = 97,850,000 c
        pv = private.preview(rt, {"account_id": I1_PRIVATE, "company_id": TESTCO, "side": "BUY", "qty": 10000, "price": "95.00", "kind": "SECONDARY",
                                  "fee_pct": "0.03", "settle_days": 30, "lockup_months": 6})
        self.assertEqual(("950000.00", "28500.00", "978500.00", "2026-11-19", "2027-04-20"), (pv["gross"], pv["fee"], pv["net"], pv["settle_date"], pv["lock_until"]))
        self.assertEqual(("100", "private", "-5.00"), (pv["mark"]["value"], pv["mark"]["quality"], pv["discount_pct"]))
        # own available at the settle date 0 -> the whole 978,500.00 is a Treasury sweep (Treasury available 1,000,000,000.00)
        self.assertEqual((97_850_000, 0, 97_850_000, "sweep"), (pv["funding"]["need_c"], pv["funding"]["own_available_c"], pv["funding"]["sweep_in_c"], pv["funding"]["source"]))
        self.assertEqual(([], []), (pv["warnings"], pv["blockers"]))
        self.assertIn("pending until 2026-11-19", pv["note"])
        self.assertEqual(tables.CONVENTIONS["PRIVATE_TRADE"], pv["sentence"])
        r = self.trade("ui:buy-testco-0001", side="BUY", qty=10000, price="95.00", kind="SECONDARY", fee_pct="0.03", settle_days=30, lockup_months=6)
        self.assertTrue(r.ok, r.body)
        fill = r.body["fill"]
        self.assertEqual(("NEGOTIATED", "negotiated", 95_000_000, 2_850_000, "2026-10-20", "2026-11-19"),
                         (fill["origin"], fill["price_basis"], fill["gross_c"], fill["fee_c"], fill["trade_date"], fill["settle_date"]))
        # SWEEP_IN of exactly the shortfall 978,500.00 from I1:TREASURY, linked to the fill
        self.assertEqual((97_850_000, "sweep"), (fill["funding"]["sweep_c"], fill["funding"]["source"]))
        self.assertEqual(97_850_000, bal(rt, I1_PRIVATE, "1010"))
        self.assertEqual(100_000_000_000 - 97_850_000, bal(rt, I1_TREASURY, "1010"))     # 999,021,500.00
        # the entry: Dr 1120 PRV-TESTCO 950,000.00 / Dr 5000 28,500.00 / Cr 2100 978,500.00
        self.assertEqual([("1120", 95_000_000, TESTCO), ("5000", 2_850_000, TESTCO), ("2100", -97_850_000, None)], self.lines(fill["entry_id"]))
        # settlement due T+30 calendar days = Thu 2026-11-19; the lot carries lock_until 2026-10-20 + 6 months = 2027-04-20
        self.assertEqual(("2026-11-19", -97_850_000, "2100", "PENDING"), (r.body["settlement"]["due_date"], r.body["settlement"]["amount_c"], r.body["settlement"]["gl"], r.body["settlement"]["status"]))
        self.assertEqual((10000, "95", 95_000_000, "2027-04-20"), (r.body["lot"]["qty_open"], r.body["lot"]["unit_cost"], r.body["lot"]["cost_c"], r.body["lot"]["lock_until"]))
        self.assertEqual("test fixture", json.loads(one(rt, "SELECT quote_json FROM fills WHERE fill_id=?", (fill["fill_id"],))["quote_json"])["source_url"])
        # mark 100.00 -> MV 10,000 x 100 = 1,000,000.00; unrealised 1,000,000 - 950,000 = +50,000.00
        p = self.position()
        self.assertEqual((10000, 95_000_000, 100_000_000, 5_000_000, "private"), (p.qty, p.cost_c, p.mv_c, p.unrealised_c, p.mark.quality))
        self.assertEqual("last round 2026-09-01 (Series X)", p.mark.note)
        self.assertEqual("2027-04-20", p.extra.get("lock_until"))
        # NAV(I1:ALL) = 1,000,000,000 - 28,500 + 50,000 = 1,000,021,500.00; quality is not partial
        nav = rt.accounting.nav("I1:ALL")
        self.assertEqual((100_002_150_000, False), (nav.nav_c, nav.partial))
        assert_integrity(self, rt, "full")
        # replaying the request id returns the stored result and fills nothing twice
        r2 = self.trade("ui:buy-testco-0001", side="BUY", qty=10000, price="95.00", kind="SECONDARY", fee_pct="0.03", settle_days=30, lockup_months=6)
        self.assertTrue(r2.ok and r2.replayed, r2.body)
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM fills")["n"])
        # record a reported round: 2026-11-01 Series Y at 120.00 -> mark 120.00; unrealised 10,000 x 120 - 950,000 = 1,200,000 - 950,000 = +250,000.00
        r = execute(rt, "private.record_round", {"company_id": TESTCO, "round_date": "2026-11-01", "round_type": "Series Y", "price_per_share": "120.00",
                                                  "source_url": "https://example.com/testco-series-y"}, "ui:round-testco-0001")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("120", "private", "last round 2026-11-01 (Series Y)", "owner"), (r.body["mark"]["value"], r.body["mark"]["quality"], r.body["mark"]["note"], r.body["round"]["recorded_by"]))
        p = self.position()
        self.assertEqual((120_000_000, 25_000_000), (p.mv_c, p.unrealised_c))
        # ... without a source URL -> 400 SOURCE_REQUIRED (nothing recorded)
        r = execute(rt, "private.record_round", {"company_id": TESTCO, "round_date": "2026-11-05", "round_type": "secondary indication", "price_per_share": "130.00"}, "ui:round-testco-0002")
        self.assertEqual((False, 400, "SOURCE_REQUIRED"), (r.ok, r.http, r.code))
        self.assertEqual(2, one(rt, "SELECT COUNT(*) n FROM private_rounds WHERE company_id=?", (TESTCO,))["n"])
        # Thu 2026-11-19: the settlement pays Dr 2100 / Cr 1010 978,500.00 (what SETTLE_DUE does)
        rt.clock.set("2026-11-19T09:00:00-05:00")
        sid = one(rt, "SELECT settle_id FROM settlements WHERE fill_id=?", (fill["fill_id"],))["settle_id"]
        r = execute(rt, "test.settle", {"settle_id": sid}, f"job:SETTLE_DUE:{sid}", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual([("2100", 97_850_000, None), ("1010", -97_850_000, None)], self.lines(r.body["entry_id"]))
        self.assertEqual((0, 0), (bal(rt, I1_PRIVATE, "1010"), bal(rt, I1_PRIVATE, "2100")))
        # Fri 2026-11-20: SELL 2,000 @ 118.00 inside the lock-up -> preview warns LOCKUP (not a blocker)
        rt.clock.set("2026-11-20T10:00:00-05:00")
        pv = private.preview(rt, {"account_id": I1_PRIVATE, "company_id": TESTCO, "side": "SELL", "qty": 2000, "price": "118.00", "fee_pct": "0.03", "settle_days": 30})
        self.assertEqual(["LOCKUP"], [w["code"] for w in pv["warnings"]])
        self.assertIn("lock-up until 2027-04-20", pv["warnings"][0]["message"])
        self.assertEqual([], pv["blockers"])
        # gross 2,000 x 118 = 236,000.00 = 23,600,000 c; fee to_c(236,000 x 0.03) = 7,080.00 = 708,000 c; FIFO cost 2,000 x 95 = 190,000.00 = 19,000,000 c
        # (pro-rata share of the lot's 950,000.00: 950,000 x 2,000 / 10,000); realised 236,000 - 190,000 = +46,000.00 = 4,600,000 c
        self.assertEqual(("236000.00", "7080.00", "228920.00", 19_000_000, 4_600_000, "2026-12-20"), (pv["gross"], pv["fee"], pv["net"], pv["cost_relieved_c"], pv["realised_c"], pv["settle_date"]))
        r = self.trade("ui:sell-testco-0001", side="SELL", qty=2000, price="118.00", kind="SECONDARY", fee_pct="0.03", settle_days=30)
        self.assertTrue(r.ok, r.body)
        self.assertEqual(["LOCKUP"], [w["code"] for w in r.body["warnings"]])
        sell = r.body["fill"]
        self.assertEqual((23_600_000, 708_000, 19_000_000, 4_600_000, "NEGOTIATED"), (sell["gross_c"], sell["fee_c"], sell["cost_relieved_c"], sell["realised_c"], sell["origin"]))
        # the exact entry: Dr 1200 228,920.00 / Dr 5000 7,080.00 / Cr 1120 190,000.00 / Cr 4000 46,000.00
        self.assertEqual([("1200", 22_892_000, None), ("5000", 708_000, TESTCO), ("1120", -19_000_000, TESTCO), ("4000", -4_600_000, TESTCO)], self.lines(sell["entry_id"]))
        self.assertEqual(("2026-12-20", 22_892_000, "1200"), (r.body["settlement"]["due_date"], r.body["settlement"]["amount_c"], r.body["settlement"]["gl"]))
        # remaining 8,000 @ 95 = 760,000.00 cost; MV 8,000 x 120 = 960,000.00; unrealised 200,000.00
        lot = one(rt, "SELECT qty_remaining, cost_c, lock_until FROM lots WHERE account_id=? AND instrument_id=?", (I1_PRIVATE, TESTCO))
        self.assertEqual((8000, 76_000_000, "2027-04-20"), (lot["qty_remaining"], lot["cost_c"], lot["lock_until"]))
        p = self.position()
        self.assertEqual((8000, 76_000_000, 96_000_000, 20_000_000, 4_600_000), (p.qty, p.cost_c, p.mv_c, p.unrealised_c, p.realised_itd_c))
        # NAV(I1:ALL) = 1,000,000,000 - 28,500 - 7,080 + 46,000 + 200,000 = 1,000,210,420.00
        self.assertEqual(100_021_042_000, rt.accounting.nav("I1:ALL").nav_c)
        # risk: the "Private / illiquid" bucket carries MV 960,000.00 (not shocked) and the lock-up calendar lists 2027-04-20
        rv = risk.report(rt, "I1:ALL")
        self.assertEqual(96_000_000, rv.private["mv_c"])
        self.assertEqual([("2027-04-20", 8000, TESTCO)], [(l["lock_until"], l["qty"], l["instrument_id"]) for l in rv.private["lockups"]])
        assert_integrity(self, rt, "full")
        # shorts are not possible; a trade needs a price; the Personal book is not a qualified purchaser
        r = self.trade("ui:short-testco-0001", side="SELL_SHORT", qty=100, price="118.00")
        self.assertEqual((400, "NOT_TRADEABLE_HERE"), (r.http, r.code))
        r = self.trade("ui:cover-testco-0001", side="BUY_TO_COVER", qty=100, price="118.00")
        self.assertEqual((400, "NOT_TRADEABLE_HERE"), (r.http, r.code))
        r = self.trade("ui:noprice-testco-0001", side="BUY", qty=100)
        self.assertEqual((400, "PRICE_REQUIRED"), (r.http, r.code))
        r = self.trade("ui:p1-testco-0001", account_id="P1:PRIVATE", side="BUY", qty=100, price="118.00")
        self.assertEqual((400, "NOT_ELIGIBLE_QP"), (r.http, r.code))
        self.assertIn("5,000,000.00", r.message)
        self.assertEqual(2, one(rt, "SELECT COUNT(*) n FROM fills")["n"])
        # the company lists: PUBLIC with ticker TESTCO from 2026-12-01 -> before that date the private mark stays (with the notice)
        # a listing set by the owner needs its source (SR-06: nothing is recorded without one)
        r = execute(rt, "private.set_status", {"company_id": TESTCO, "status": "PUBLIC", "ticker": "TESTCO", "listed_on": "2026-12-01"}, "ui:list-testco-0000")
        self.assertEqual((400, "SOURCE_REQUIRED"), (r.http, r.code))
        r = execute(rt, "private.set_status", {"company_id": TESTCO, "status": "PUBLIC", "ticker": "TESTCO", "listed_on": "2026-12-01",
                                               "source_url": "https://example.com/testco-listing"}, "ui:list-testco-0001")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("PUBLIC", "TESTCO", "2026-12-01"), (r.body["status"], r.body["ticker"], r.body["listed_on"]))
        p = self.position()
        self.assertEqual(("120", "private"), (str(p.mark.mark), p.mark.quality))
        self.assertIn("lists 2026-12-01 (TESTCO)", p.mark.note)
        # from the listing date the position takes the listed instrument's mark 125.00 with that record's quality
        rt.clock.set("2026-12-01T10:00:00-05:00")
        rt.marks.set("TESTCO", "125.00")
        p = self.position()
        self.assertEqual(("125", "delayed", False), (str(p.mark.mark), p.mark.quality, p.mark.executable))
        self.assertIn("listed 2026-12-01 (TESTCO)", p.mark.note)
        # MV 8,000 x 125 = 1,000,000.00; unrealised 1,000,000 - 760,000 = 240,000.00
        self.assertEqual((100_000_000, 24_000_000), (p.mv_c, p.unrealised_c))
        assert_integrity(self, rt, "full")                 # L24: every private fill is NEGOTIATED with a settlement row

    # ---- validation codes and preview economics -----------------------------------------------------------------------------
    def test_validation_codes_and_defaults(self):
        rt = self.start()
        self.add_testco()
        base = {"account_id": I1_PRIVATE, "company_id": TESTCO, "side": "BUY", "qty": 100, "price": "95.00"}

        def code(**over):
            req = dict(base, **over)
            for k in [k for k, v in req.items() if v is None]:
                del req[k]
            with self.assertRaises(RejectError) as cm:
                private.preview(rt, req)
            return cm.exception.code

        self.assertEqual("PRICE_REQUIRED", code(price=None))
        self.assertEqual("PRICE_INVALID", code(price="0"))
        self.assertEqual("PRICE_INVALID", code(price="abc"))
        self.assertEqual("QTY_INVALID", code(qty=0))
        self.assertEqual("QTY_INVALID", code(qty="1.5"))
        self.assertEqual("KIND_INVALID", code(kind="IPO"))
        self.assertEqual("SIDE_INVALID", code(side="HOLD"))
        self.assertEqual("NOT_TRADEABLE_HERE", code(side="SELL_SHORT"))
        self.assertEqual("NOT_TRADEABLE_HERE", code(side="STO"))
        self.assertEqual("NOT_TRADEABLE_HERE", code(account_id="I1:US_EXCHANGE"))
        self.assertEqual("NOT_ELIGIBLE_QP", code(account_id="P1:PRIVATE"))
        self.assertEqual("ACCOUNT_INVALID", code(account_id="nonsense"))
        self.assertEqual("COMPANY_UNKNOWN", code(company_id="PRV-NOPE"))
        self.assertEqual("OVERSELL", code(side="SELL", qty=1))
        self.assertEqual("FEE_INVALID", code(fee_pct="2"))
        self.assertEqual("SETTLE_DAYS_INVALID", code(settle_days=-1))
        self.assertEqual("LOCKUP_INVALID", code(lockup_months=121))
        # defaults (settings.private_defaults: fee 0.03, T+30, 6 months): BUY 100 @ 95 -> gross 9,500.00; fee to_c(9,500 x 0.03) = 285.00; net 9,785.00
        pv = private.preview(rt, base)
        self.assertEqual(("9500.00", "285.00", "9785.00", "0.03", 30, 6, "2026-11-19", "2027-04-20"),
                         (pv["gross"], pv["fee"], pv["net"], pv["fee_pct"], pv["settle_days"], pv["lockup_months"], pv["settle_date"], pv["lock_until"]))
        self.assertEqual(["PRIVATE_TRADE", "PRIVATE_MARK", "QP", "FIFO"], pv["conventions"])
        # participate in a round: kind ROUND defaults to no placement fee -> fee 0.00, net = gross 9,500.00, no lock-up when lockup_months 0
        pv = private.preview(rt, dict(base, kind="ROUND", lockup_months=0))
        self.assertEqual(("0.00", "9500.00", "0", None), (pv["fee"], pv["net"], pv["fee_pct"], pv["lock_until"]))
        # a purchase Treasury cannot fund: 100,000,000 @ 95 -> gross 9,500,000,000.00, fee 285,000,000.00, need 9,785,000,000.00 > 1,000,000,000.00 available
        pv = private.preview(rt, dict(base, qty=100_000_000))
        self.assertEqual(["INSUFFICIENT_FUNDS"], [b["code"] for b in pv["blockers"]])
        # own 0 + Treasury 1,000,000,000.00 -> sweep 1,000,000,000.00, unfunded 8,785,000,000.00
        self.assertEqual((978_500_000_000, 100_000_000_000, 878_500_000_000, "none"), (pv["funding"]["need_c"], pv["funding"]["sweep_in_c"], pv["funding"]["unfunded_c"], pv["funding"]["source"]))
        r = self.trade("ui:toobig-testco-0001", side="BUY", qty=100_000_000, price="95.00")
        self.assertEqual((409, "INSUFFICIENT_FUNDS"), (r.http, r.code))
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM fills")["n"])
        # a company without any priced round: valuation only -> the mark is unavailable, a trade still works at the negotiated price
        r = execute(rt, "private.add_company", {"name": "Valuation Only Co", "last_round": {"round_date": "2026-10-01", "round_type": "Series A", "post_money": "500000000.00",
                                                                                             "source_url": "https://example.com/valonly"}}, "ui:add-valonly-0001")
        self.assertTrue(r.ok, r.body)
        self.assertEqual("PRV-VALUATIONONLYCO", r.body["company_id"])
        self.assertEqual("unavailable", r.body["mark"]["quality"])
        self.assertIn("valuation only: no price per share disclosed", r.body["mark"]["note"])
        self.assertIn("500,000,000.00", r.body["mark"]["note"])
        pv = private.preview(rt, dict(base, company_id="PRV-VALUATIONONLYCO", price="12.50"))
        self.assertEqual((None, "unavailable", "1250.00"), (pv["discount_pct"], pv["mark"]["quality"], pv["gross"]))

    # ---- owner records: add_company / record_round rules ----------------------------------------------------------------------
    def test_owner_records_rules(self):
        rt = self.start()
        r = execute(rt, "private.add_company", {"sector": "AI"}, "ui:add-noname-0001")
        self.assertEqual((400, "NAME_REQUIRED"), (r.http, r.code))
        r = execute(rt, "private.add_company", {"name": "Listed Co", "status": "PUBLIC"}, "ui:add-noticker-0001")
        self.assertEqual((400, "TICKER_REQUIRED"), (r.http, r.code))
        r = execute(rt, "private.add_company", {"name": "Bad Status Co", "status": "DELISTED"}, "ui:add-badstatus-0001")
        self.assertEqual((400, "STATUS_INVALID"), (r.http, r.code))
        # a number in last_round without a source URL is refused: nothing is created
        r = execute(rt, "private.add_company", {"name": "Unsourced Co", "last_round": {"round_date": "2026-10-01", "round_type": "Series A", "price_per_share": "10.00"}}, "ui:add-unsourced-0001")
        self.assertEqual((400, "SOURCE_REQUIRED"), (r.http, r.code))
        self.assertIsNone(one(rt, "SELECT 1 FROM private_companies WHERE company_id='PRV-UNSOURCEDCO'"))
        self.assertIsNone(rt.instruments.get("PRV-UNSOURCEDCO"))
        # a plain company (no round): PRV-<slug>, instrument kind PRIVATE / support COMPUTED / PRIVATE accounts only; mark unavailable "no reported round"
        r = execute(rt, "private.add_company", {"name": "Anysphere Two (Cursor)", "sector": "AI", "country": "US"}, "ui:add-plain-0001")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("PRV-ANYSPHERETWOCURSOR", "PRIVATE", 0, "no reported round"), (r.body["company_id"], r.body["status"], r.body["rounds_count"], r.body["mark"]["note"]))
        inst = rt.instruments.get("PRV-ANYSPHERETWOCURSOR")
        self.assertEqual(("PRIVATE", "COMPUTED", ("PRIVATE",), "PRIVATE_MARKETS", 30), (inst.kind, inst.support, inst.account_kinds, inst.session_group, inst.settle_days))
        self.assertTrue(rt.instruments.tradeable_in(inst, "PRIVATE"))
        self.assertFalse(rt.instruments.tradeable_in(inst, "US_EXCHANGE"))
        r = execute(rt, "private.add_company", {"name": "Anysphere Two (Cursor)"}, "ui:add-plain-0002")
        self.assertEqual((409, "EXISTS"), (r.http, r.code))
        # record_round: the source is mandatory, a number is required, the (company, date, type) key is unique
        cid = "PRV-ANYSPHERETWOCURSOR"
        r = execute(rt, "private.record_round", {"company_id": cid, "round_date": "2026-10-15", "round_type": "Series B", "price_per_share": "20.00"}, "ui:rr-0001")
        self.assertEqual((400, "SOURCE_REQUIRED"), (r.http, r.code))
        r = execute(rt, "private.record_round", {"company_id": cid, "round_date": "2026-10-15", "round_type": "Series B", "source_url": "https://example.com/b"}, "ui:rr-0002")
        self.assertEqual((400, "NUMBER_REQUIRED"), (r.http, r.code))
        r = execute(rt, "private.record_round", {"company_id": cid, "round_date": "15/10/2026", "round_type": "Series B", "price_per_share": "20.00", "source_url": "https://example.com/b"}, "ui:rr-0003")
        self.assertEqual((400, "DATE_INVALID"), (r.http, r.code))
        r = execute(rt, "private.record_round", {"company_id": "PRV-NOPE", "round_date": "2026-10-15", "round_type": "Series B", "price_per_share": "20.00", "source_url": "https://example.com/b"}, "ui:rr-0004")
        self.assertEqual((400, "COMPANY_UNKNOWN"), (r.http, r.code))
        # post-money 1,250,000,000.00 = 125,000,000,000 c; price 20.00; lead investors as a list are joined
        r = execute(rt, "private.record_round", {"company_id": cid, "round_date": "2026-10-15", "round_type": "Series B", "price_per_share": "20.00", "post_money": "1250000000.00",
                                                  "lead_investors": ["Fund A", "Fund B"], "source_url": "https://example.com/b"}, "ui:rr-0005")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((125_000_000_000, "20", "Fund A, Fund B", "2026-10-15", "owner"), (r.body["round"]["post_money_c"], r.body["round"]["price_per_share"], r.body["round"]["lead_investors"],
                                                                                       r.body["round"]["as_of"], r.body["round"]["recorded_by"]))
        self.assertEqual(("20", "last round 2026-10-15 (Series B)"), (r.body["mark"]["value"], r.body["mark"]["note"]))
        r = execute(rt, "private.record_round", {"company_id": cid, "round_date": "2026-10-15", "round_type": "Series B", "price_per_share": "21.00", "source_url": "https://example.com/b2"}, "ui:rr-0006")
        self.assertEqual((409, "ROUND_EXISTS"), (r.http, r.code))
        # the LATEST round by date wins, not the latest recorded: an older tender at 18.00 recorded later leaves the mark at 20.00
        r = execute(rt, "private.record_round", {"company_id": cid, "round_date": "2026-09-01", "round_type": "tender offer", "price_per_share": "18.00", "source_url": "https://example.com/t"}, "ui:rr-0007")
        self.assertTrue(r.ok, r.body)
        self.assertEqual("20", r.body["mark"]["value"])
        self.assertEqual(["2026-10-15", "2026-09-01"], [x["round_date"] for x in private.company(rt, cid)["rounds"]])
        # the company list carries mark, source and as-of for the grid
        rows = {c["company_id"]: c for c in private.companies(rt, q="cursor", account_id=I1_PRIVATE)}
        self.assertIn(cid, rows)
        self.assertEqual(("https://example.com/b", "2026-10-15", "20", {"qty": 0, "cost_c": 0, "cost": "0.00", "lock_until": None}),
                         (rows[cid]["source_url"], rows[cid]["as_of"], rows[cid]["mark"]["value"], rows[cid]["position"]))

    # ---- the seeded master and the mark rules ---------------------------------------------------------------------------------
    def test_seeded_master_and_mark_rules(self):
        rt = self.start()
        self.assertGreaterEqual(one(rt, "SELECT COUNT(*) n FROM private_companies")["n"], 20)
        insts = q(rt, "SELECT instrument_id, support, session_group, account_kinds, settle_days FROM instruments WHERE kind='PRIVATE'")
        self.assertGreaterEqual(len(insts), 20)
        for i in insts:
            self.assertEqual(("COMPUTED", "PRIVATE_MARKETS", '["PRIVATE"]', 30), (i["support"], i["session_group"], i["account_kinds"], i["settle_days"]), i)
        self.assertGreaterEqual(one(rt, "SELECT COUNT(*) n FROM private_rounds WHERE recorded_by='seed'")["n"], 20)
        # Anthropic Series H: post-money USD 965,000,000,000 -> 96,500,000,000,000 c; no price per share -> valuation only, mark unavailable
        a = one(rt, "SELECT * FROM private_rounds WHERE company_id='PRV-ANTHROPIC' ORDER BY round_date DESC LIMIT 1")
        self.assertEqual((96_500_000_000_000, None, "2026-05-28", "seed"), (a["post_money_c"], a["price_per_share"], a["round_date"], a["recorded_by"]))
        m = private.private_mark(rt, "PRV-ANTHROPIC")
        self.assertEqual(("unavailable", None), (m.quality, m.mark))
        self.assertIn("valuation only: no price per share disclosed", m.note)
        # Revolut's secondary at USD 2,017 per share -> mark 2017, quality private, never executable, label with the round date and type
        m = private.private_mark(rt, "PRV-REVOLUT")
        self.assertEqual((Decimal("2017"), "private", "private_round", False, "last round 2026-07-22 (secondary share sale)"), (m.mark, m.quality, m.method, m.executable, m.note))
        self.assertEqual("https://tech.eu/2026/07/22/revolut-confirms-fresh-secondary-share-sale-at-reported-115bn-valuation/", m.inputs["source_url"])
        # a private record never ages into stale: freshness() of a private_round record is "private" whatever the time
        rec = QuoteRecord(instrument_id="PRV-REVOLUT", provider="private", source="last reported round", method="private_round", last=Decimal("2017"),
                          quote_time=0, fetched_at=0, delay_s=0, delay_label="last reported round")
        self.assertEqual("private", freshness(rec, None, rt.clock.epoch() + 10 * 365 * 86400))
        # Cerebras listed 2026-05-14 as CBRS: the mark comes from the listed instrument (none in the fixture -> unavailable with the reason)
        m = private.private_mark(rt, "PRV-CEREBRAS")
        self.assertEqual("unavailable", m.quality)
        self.assertIn("listed 2026-05-14 (CBRS)", m.note)
        rt.marks.set("CBRS", "311.00")
        m = private.private_mark(rt, "PRV-CEREBRAS")
        self.assertEqual((Decimal("311"), "delayed", False, "CBRS"), (m.mark, m.quality, m.executable, m.inputs["ticker"]))
        # the whole list renders: every row has a mark dict with a quality and, when sourced, the source and as-of
        rows = private.companies(rt)
        self.assertGreaterEqual(len(rows), 20)
        for c in rows:
            self.assertIn(c["mark"]["quality"], ("private", "delayed", "close", "stale", "computed", "unavailable"), c["company_id"])
            self.assertTrue(c["source_url"] and c["as_of"], c["company_id"])
        # reseed is idempotent: nothing new when the file has not changed
        with rt.db.writer() as tx:
            self.assertEqual(0, private.reseed(tx, rt))


# ---- the UI files (page + ticket component) served by the fixture server ------------------------------------------------------
class PrivateStaticTests(unittest.TestCase):
    PAGE = os.path.join(STATIC, "pages", "private_markets.js")
    TICKET = os.path.join(STATIC, "components", "privateticket.js")

    def test_files_register_balance_and_use_the_designed_routes(self):
        from tests.test_terminal_static import js_balance
        page, ticket = read(self.PAGE), read(self.TICKET)
        for path in (self.PAGE, self.TICKET):
            with open(path, "rb") as f:
                f.read().decode("ascii")
        self.assertIn("T.pages.private_markets = ", page)
        self.assertIn("T.components.privateticket = ", ticket)
        self.assertEqual((0, 0, 0), js_balance(page))
        self.assertEqual((0, 0, 0), js_balance(ticket))
        for need in ("/api/private/companies", "/rounds", "QP", "PRIVATE_MARK", "PRIVATE_TRADE", "T.pxHtml", "LOCKUP"):
            self.assertIn(need, page + ticket, need)
        for need in ("/api/private/preview", "/api/private/trades", "lockup_months", "settle_days", "fee_pct", "LOCKUP"):
            self.assertIn(need, ticket, need)
        self.assertNotIn("Math.random", page + ticket)
        self.assertFalse(re.search(r"https?://", page + ticket), "no external references")

    def test_fixture_server_serves_the_page_and_its_routes(self):
        from tests import terminal_fixture_server as fx
        for method, path, name in (("GET", "/api/private/companies", "private_companies"), ("POST", "/api/private/companies", "private_companies_post"),
                                   ("POST", "/api/private/companies/PRV-TESTCO/rounds", "private_rounds_post"), ("POST", "/api/private/preview", "private_preview"),
                                   ("POST", "/api/private/trades", "private_trades")):
            self.assertEqual(name, fx.match_route(method, path), path)
        server = fx.FixtureServer().start()
        try:
            for rel in ("pages/private_markets.js", "components/privateticket.js"):
                c = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
                c.request("GET", "/static/" + rel)
                r = c.getresponse()
                body = r.read().decode("ascii")
                hdrs = {k.lower(): v for k, v in r.getheaders()}
                c.close()
                self.assertEqual(200, r.status, rel)
                self.assertIsNone(hdrs.get("x-fixture-stub"), rel + " was served as a stub")
                self.assertIn("javascript", hdrs.get("content-type", ""), rel)
                self.assertIn("no-store", hdrs.get("cache-control", ""), rel)
                self.assertIn("T.pages.private_markets = " if rel.startswith("pages") else "T.components.privateticket = ", body)
            c = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
            c.request("GET", "/api/private/companies?account_id=I1:PRIVATE")
            r = c.getresponse()
            rows = json.loads(r.read())
            c.close()
            self.assertEqual(200, r.status)
            self.assertTrue(isinstance(rows, list) and rows and rows[0]["mark"]["quality"] == "private")
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
