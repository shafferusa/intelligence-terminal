"""WP-07 prime-broker tests (TERMINAL_DESIGN.md §12.4, §13.7, §6.1, §11.3, §19.23, §19.24 D10; TERMINAL_WORKPLAN.md WP-07).
Every expected number is worked by hand in a comment.  No network: FakeClock, FixedMarks, a temp database; the local
``accrue_segment`` copy and the SETTLE_DUE stand-in come from the fx / equities test modules (functions only, so unittest
does not load their cases twice).  The runtime is the orders runtime (WP-02) with credit (WP-05) and prime bound, so a
PB buy runs through the real ``order.submit`` -> ``execution.apply_fill`` -> ``fee_for`` -> ``prime.schedule`` path and the
PB draw through ``execution.fund`` -> ``credit.pb_draw_for_funding``."""
from __future__ import annotations

import json
import shutil
import tempfile
import types
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import T1, TREASURY, US_EXCHANGE, assert_integrity, bal, buy, deposit, execute, fund, make_runtime, pb, rid
from tests import test_terminal_orders as sc
from tests.test_terminal_fx import accrue_segment, lines_of, one, q
from tests.test_terminal_equities import _h_settle_due
from finsim.terminal import books, credit, execution, orders, prime, risk
from finsim.terminal.books import account_row
from finsim.terminal.commands import RejectError
from finsim.terminal.quotes.rates import RateStore

PB_GS, PB_JPM, PB_BOFA, PB_MS, PB_DB = pb("GS"), pb("JPM"), pb("BOFA"), pb("MS"), pb("DB")
SEED_GS = {"name": "Goldman Sachs", "equity_per_share": "0.0030", "equity_min_c": 100, "option_per_contract": "0.55", "futures_per_side": "2.00",
           "financing_spread": "0.0125", "credit_interest_spread": "0.0075", "gc_rate": "0.0040", "htb_add": "0.0010", "otc_spread_mult": "1.0",
           "swap_margin_mode": "CTM", "monthly_minimum_c": 0, "irs_half_width_bp": "1.0", "trs_spread": "0.0060", "note": "modelled assumptions; real PB schedules vary",
           # SR-10 / C22: the OTC terms of the GS seed (prime.OTC_SEEDS, assumed): IM 50 bp of |DV01| / 10 % / 2 % / 15 % / 6 %, half-widths
           # 1.00 vol point (equity options), 0.30 (FX options), 2 bp (forwards)
           "irs_im_bp": "50", "trs_im_pct": "0.10", "xccy_im_pct": "0.02", "option_im_pct": "0.15", "forward_im_pct": "0.06", "eq_option_hw_vol": "0.0100",
           "fx_option_hw_vol": "0.0030", "forward_hw_bp": "2.0",
           # FINSIM1 P6 task 6 (1.0.6): the CSA securities terms of the GS seed (prime.CSA_SEEDS, assumed): Treasuries, bills 2 %, notes / bonds 4 %
           "csa_securities": "TREASURIES", "csa_bill_haircut": "0.02", "csa_bond_haircut": "0.04",
           # FX_CCS_PLAN rev 2 D5 (1.0.6.1, intended change): the cross-currency terms of the GS seed (prime.XCCY_SEEDS, ASSUMED): EUR/USD and
           # GBP/USD basis levels 0.0 bp, the cross-currency swap half-width = GS's IRS half-width 1.0 bp, the EUR OIS spread 0.0 bp
           "xccy_basis_eur_bp": "0.0", "xccy_basis_gbp_bp": "0.0", "xccy_basis_hw_bp": "1.0", "eur_ois_spread_bp": "0.0"}


def _h_fund(ctx, payload):
    """A test-only call of execution.fund (what a fill or a call does) so the PB-draw hook runs inside a command."""
    res = execution.fund(ctx, payload["account_id"], "USD", int(payload["need_c"]), payload.get("link_type", "fill"), payload.get("link_id", "x"), None,
                         immediate=True, overdraft=bool(payload.get("overdraft")))
    return res.to_dict()


def runtime(tmp, at="2026-10-19T10:00:00-04:00", books_kind="test"):
    """make_runtime + orders (WP-02) + credit (WP-05) + prime, SOFR 3.87 observed 2026-10-19, the local accrue_segment."""
    rt = make_runtime(tmp, at=at, books=books_kind)
    orders.register(rt.bus)
    credit.register(rt.bus)
    rt.credit = credit
    rt.rates = RateStore()
    rt.rates.put("SOFR", [(date(2026, 10, 19), Decimal("3.87"))], persist=False)
    rt.lifecycle = types.SimpleNamespace(accrue_segment=accrue_segment)
    prime.register(rt.bus)
    prime.bind(rt)
    rt.bus.register("test.fund", _h_fund)
    rt.bus.register("test.settle_due", _h_settle_due)
    return rt


def open_pb(rt, broker, lifetime=T1, request_id=None, **extra):
    payload = {"lifetime_id": lifetime, "broker": broker}
    payload.update(extra)
    return execute(rt, "account.open_pb", payload, request_id)


def update_schedule(rt, account, request_id=None, **fields):
    payload = {"account_id": account}
    payload.update(fields)
    return execute(rt, "account.update_schedule", payload, request_id)


def accrue(rt, account, night, kind="pb", request_id=None):
    return execute(rt, "prime.pb_accrue", {"account_id": account, "night": night, "kind": kind}, request_id or f"job:ACCRUE_DAILY:{kind}:{account}:{night}", actor="lifecycle")


def monthly_min(rt, account, month, request_id=None):
    return execute(rt, "prime.monthly_minimum", {"account_id": account, "month": month}, request_id or f"job:PAY_MONTHLY:pbmin:{account}:{month}", actor="lifecycle")


def row(rt, account):
    with rt.db.reader() as conn:
        return dict(account_row(conn, account))


class PrimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-prime-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, at="2026-10-19T10:00:00-04:00", books_kind="test"):
        self.rt = runtime(self.tmp, at=at, books_kind=books_kind)
        return self.rt

    def eligible(self):
        """The Test Book is a FUND at 10,000,000: a 15,000,000.00 deposit takes NAV to the 25,000,000.00 PB/OTC threshold."""
        self.assertTrue(deposit(self.rt, 1500000000).ok)
        self.assertTrue(books.eligibility(self.rt, T1).can_onboard)

    def market_buy(self, account, qty, last, volume=120000):
        rt = self.rt
        now = rt.clock.epoch()
        rt.marks.set_quote(sc.rec("AAPL", last, now - 120, now, bar=(now - 180, volume)))
        rt.marks.set("AAPL", last)
        r = sc.submit(rt, account_id=account, instrument_id="AAPL", side="BUY", qty=qty, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        self.assertEqual("FILLED", r.body["order"]["status"])
        return r

    def settle(self, as_of):
        r = execute(self.rt, "test.settle_due", {"as_of": as_of}, actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        return r.body["settled"]

    # ---- open: schedule copy, replay, 409 on reopen, nine brokers incl. BofA, unknown broker, ineligible lifetime -----------------------
    def test_open_gs_schedule_reopen_409_bofa_unknown_and_ineligible(self):
        rt = self.start()
        # NAV 10,000,000 < threshold 25,000,000 -> a NEW relationship is refused through books; nothing is written
        r = open_pb(rt, "GS")
        self.assertEqual((400, "NOT_ELIGIBLE", 1000000000, 2500000000), (r.http, r.code, r.body["error"]["details"]["nav_c"], r.body["error"]["details"]["threshold_c"]))
        self.assertIsNone(one(rt, "SELECT 1 FROM accounts WHERE account_id=?", (PB_GS,)))
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM pb_schedules")["n"])
        self.eligible()
        r = open_pb(rt, "GS", request_id="ui:open-gs-00001")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((PB_GS, "GS", True), (r.body["account_id"], r.body["broker"], r.body["created"]))
        a = r.body["account"]
        self.assertEqual((PB_GS, "PB", "PB_GS", "GS", "GS", "OPEN", 0, 0, 60, "Goldman Sachs (PB)", rt.clock.epoch(), T1, "T"),
                         (a["account_id"], a["kind"], a["role"], a["broker"], a["fee_schedule"], a["status"], a["pb_financing"], a["pb_limit_c"], a["sort_order"],
                          a["name"], a["opened_ts"], a["lifetime_id"], a["book_code"]))
        self.assertEqual(SEED_GS, r.body["schedule"])                       # the seed row copied verbatim (gc_rate = 0.0030 + 0.0010 = 0.0040)
        srow = one(rt, "SELECT * FROM pb_schedules WHERE account_id=?", (PB_GS,))
        self.assertEqual((rt.clock.epoch(), SEED_GS), (srow["updated_ts"], json.loads(srow["schedule_json"])))
        self.assertEqual([("ACCOUNT_OPENED", PB_GS, "lifetime", T1)], [(e["type"], e["account_id"], e["ref_type"], e["ref_id"]) for e in q(rt, "SELECT * FROM events WHERE type='ACCOUNT_OPENED'")])
        # idempotent per broker: the same request id replays; a second click is 409 EXISTS
        r2 = open_pb(rt, "GS", request_id="ui:open-gs-00001")
        self.assertEqual((True, True, PB_GS), (r2.ok, r2.replayed, r2.body["account_id"]))
        r3 = open_pb(rt, "GS")
        self.assertEqual((409, "EXISTS", "OPEN"), (r3.http, r3.code, r3.body["error"]["details"]["status"]))
        r3 = open_pb(rt, "GS", lifetime=None, book_code="T")                 # the book resolves to its open lifetime T1
        self.assertEqual((409, "EXISTS"), (r3.http, r3.code))
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM accounts WHERE kind='PB'")["n"])
        # nine brokers, BofA among them
        self.assertEqual(["GS", "JPM", "MS", "DB", "BARC", "CITI", "UBS", "BNP", "BOFA"], [b["broker"] for b in prime.brokers()])
        r = open_pb(rt, "bofa")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((PB_BOFA, "BofA Securities (PB)", 68, "BofA Securities", "0.0125", "0.0030"),
                         (r.body["account_id"], r.body["account"]["name"], r.body["account"]["sort_order"], r.body["schedule"]["name"], r.body["schedule"]["financing_spread"],
                          r.body["schedule"]["equity_per_share"]))
        r = open_pb(rt, "XYZ")
        self.assertEqual((400, "UNKNOWN_BROKER", 9), (r.http, r.code, len(r.body["error"]["details"]["brokers"])))
        r = execute(rt, "account.open_pb", {"broker": "GS"})
        self.assertEqual((400, "LIFETIME_REQUIRED"), (r.http, r.code))
        # the live schedule through services.prime (what fee_for / pb_draw read)
        s = rt.prime.schedule(PB_GS)
        self.assertEqual((Decimal("0.0030"), 100, "pb_schedules", "GS", SEED_GS), (s.equity_per_share, s.equity_min_c, s.source, s.broker, s.to_dict()))
        with self.assertRaises(prime.NotFound) as cm:
            rt.prime.schedule(US_EXCHANGE)
        self.assertEqual(("NOT_PB", 404), (cm.exception.code, cm.exception.http))
        with self.assertRaises(prime.NotFound) as cm:
            rt.prime.schedule("T1:NOPE")
        self.assertEqual("ACCOUNT_UNKNOWN", cm.exception.code)
        inst = rt.instruments.get("AAPL")
        # 1,000 shares: GS 1,000 x 0.0030 = 3.00 (min 1.00); the DEFAULT schedule 1,000 x 0.005 = 5.00
        self.assertEqual((300, "USD 0.0030/share, min 1.00"), execution.fee_for(rt, row(rt, PB_GS), inst, 1000))
        self.assertEqual((500, "USD 0.005/share, min 1.00"), execution.fee_for(rt, row(rt, US_EXCHANGE), inst, 1000))
        self.assertEqual(("0.0125", "Goldman Sachs"), (credit.pb_schedule(rt, row(rt, PB_GS))["financing_spread"], credit.pb_schedule(rt, row(rt, PB_GS))["name"]))
        # an account inserted without account.open_pb (older tests do this) reads the seed, labelled as such
        with rt.db.writer() as tx:
            books._insert_account(tx.conn, tx.ts, "T", T1, "PB_MS", "Morgan Stanley (PB)", "PB", 62, broker="MS")
        s = rt.prime.schedule(PB_MS)
        self.assertEqual(("seed", Decimal("0.0035"), "Morgan Stanley"), (s.source, s.equity_per_share, s.name))
        v = rt.prime.schedule_view(PB_GS)
        self.assertEqual((PB_GS, "GS", SEED_GS, SEED_GS, "pb_schedules", list(prime.EDITABLE_FIELDS)), (v["account_id"], v["broker"], v["schedule"], v["seed"], v["source"], v["editable"]))
        assert_integrity(self, rt, "full")

    def test_f1_opens_gs_p1_cannot_i1_can(self):
        """§19.14 / §19.23 naming: the Fund Book's first lifetime F1 (25,000,000) opens F1:PB_GS; the Personal Book's P1 (1,000,000) cannot."""
        rt = self.start(books_kind="real")
        r = open_pb(rt, "GS", lifetime="F1")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("F1:PB_GS", "F1", "F"), (r.body["account_id"], r.body["account"]["lifetime_id"], r.body["account"]["book_code"]))
        r = open_pb(rt, "GS", lifetime="P1")
        self.assertEqual((400, "NOT_ELIGIBLE"), (r.http, r.code))
        self.assertIn("25,000,000.00", r.message)
        r = open_pb(rt, "JPM", lifetime="I1")
        self.assertEqual((True, "I1:PB_JPM"), (r.ok, r.body["account_id"]))
        r = open_pb(rt, "GS", lifetime="F9")
        self.assertEqual((400, "LIFETIME_UNKNOWN"), (r.http, r.code))
        assert_integrity(self, rt, "full")

    # ---- D10: the Goldman account's commission, DVP settlement rows, margin per PB, exposure per PB ------------------------------------------
    def test_d10_goldman_buy(self):
        rt = self.start()                                                   # Mon 2026-10-19 10:00 ET
        self.eligible()
        self.assertTrue(open_pb(rt, "GS").ok)
        self.assertTrue(open_pb(rt, "JPM", pb_financing=1).ok)              # a second PB, financing on, no positions
        r = self.market_buy(PB_GS, 1000, "339.50")
        fill = r.body["fills"][0]
        # gross 1,000 x 339.50 = 339,500.00; GS fee 1,000 x 0.0030 = 3.00; DVP: Dr 1100 / Dr 5000 / Cr 2100 339,503.00 due T+1 (Tue 10-20) in the PB account
        self.assertEqual((33950000, 300, "2026-10-19", "2026-10-20"), (fill["gross_c"], fill["fee_c"], fill["trade_date"], fill["settle_date"]))
        lines = q(rt, "SELECT gl, amount_c, instrument_id, account_id FROM journal_lines WHERE entry_id=? ORDER BY line_no", (fill["entry_id"],))
        self.assertEqual([("1100", 33950000, "AAPL", PB_GS), ("5000", 300, "AAPL", PB_GS), ("2100", -33950300, None, PB_GS)],
                         [(l["gl"], l["amount_c"], l["instrument_id"], l["account_id"]) for l in lines])
        st = one(rt, "SELECT * FROM settlements WHERE fill_id=?", (fill["fill_id"],))
        self.assertEqual((PB_GS, "CASH", "USD", -33950300, "2100", "2026-10-20", "PENDING"), (st["account_id"], st["leg"], st["currency"], st["amount_c"], st["gl"], st["due_date"], st["status"]))
        # the PB account held no cash: one Treasury sweep of exactly 339,503.00 linked to the fill
        self.assertEqual([(33950300, "fill", fill["fill_id"], TREASURY, PB_GS)], [(s["amount_c"], s["linked_type"], s["linked_id"], s["from_account"], s["to_account"]) for s in r.body["sweeps"]])
        pv = orders.preview(rt, {"account_id": PB_GS, "instrument_id": "AAPL", "side": "BUY", "qty": 1000, "order_type": "MARKET"})
        self.assertEqual(("3.00", "USD 0.0030/share, min 1.00"), (pv["economics"]["fee"], pv["economics"]["fee_basis"]))
        cash = rt.accounting.cash(PB_GS)["USD"]
        self.assertEqual((33950300, 33950300), (cash.settled_c, cash.payable_c))
        # Tue 00:35: SETTLE_DUE Dr 2100 / Cr 1010 339,503.00 in the PB account (the PB received the shares, we paid)
        rt.clock.set("2026-10-20T00:35:00-04:00")
        self.assertEqual([st["settle_id"]], self.settle("2026-10-20"))
        self.assertEqual((0, 0, 1000, 33950000), (bal(rt, PB_GS, "1010"), bal(rt, PB_GS, "2100"), rt.accounting.position(PB_GS, "AAPL").qty, bal(rt, PB_GS, "1100", inst="AAPL")))
        # NAV(PB_GS) at 339.50 = advances 339,503.00 - fee 3.00 = 339,500.00 (= the position at its mark, cash 0); the lifetime bears the fee:
        # NAV(T1:ALL) = 25,000,000.00 - 3.00 = 24,999,997.00
        rt.clock.set("2026-10-20T10:00:00-04:00")
        self.assertEqual((33950000, 2499999700, 300), (rt.accounting.nav(PB_GS).nav_c, rt.accounting.nav("T1:ALL").nav_c, rt.accounting.nav(PB_GS).fees_c))
        # margin per PB: financing off -> headroom 0; on -> 50 % x 339,500.00 = 169,750.00; JPM (no positions) 0 — a long at GS never helps JPM
        self.assertEqual(0, rt.accounting.buying_power(PB_GS, "USD").pb_headroom_c)
        h = rt.prime.headroom(PB_GS)
        self.assertEqual((False, 0, 33950000, 16975000, 0, 0, 0), (h["pb_financing"], h["headroom_c"], h["long_mv_c"], h["loan_value_c"], h["financed_c"], h["otc_im_c"], h["otc_vm_posted_c"]))
        self.assertTrue(execute(rt, "account.update_settings", {"account_id": PB_GS, "pb_financing": 1}).ok)
        self.assertEqual((16975000, 0), (rt.accounting.buying_power(PB_GS, "USD").pb_headroom_c, rt.accounting.buying_power(PB_JPM, "USD").pb_headroom_c))
        self.assertEqual((16975000, 16975000, None), (rt.prime.headroom(PB_GS)["headroom_c"], rt.prime.headroom(PB_GS)["uncapped_c"], rt.prime.headroom(PB_GS)["headroom_by_limit_c"]))
        # Risk shows the exposure per PB (WP-09) with the same headroom numbers
        v = risk.report(rt, "ALL")
        self.assertEqual([(PB_GS, "GS", True, 16975000, 0, 33950000), (PB_JPM, "JPM", True, 0, 0, 0)],
                         [(p["account_id"], p["broker"], p["pb_financing"], p["headroom_c"], p["financed_c"], p["long_mv_c"]) for p in v.financing_liquidity["pb"]])
        self.assertEqual({PB_GS: 33950000}, {k: b["long_c"] for k, b in v.exposure_by_account.items() if b["long_c"]})
        assert_integrity(self, rt, "full")

    # ---- financing: the switch, the draw up to headroom through fund(), nightly interest, per-PB isolation, L14 ---------------------------
    def test_financing_draws_up_to_headroom_interest_and_isolation(self):
        rt = self.start()                                                   # Mon 2026-10-19 10:00 ET
        self.eligible()
        self.assertTrue(open_pb(rt, "GS", pb_financing=1).ok)
        self.assertTrue(open_pb(rt, "JPM", pb_financing=1).ok)
        self.assertEqual(1, row(rt, PB_GS)["pb_financing"])
        r = self.market_buy(PB_GS, 1000, "339.50")                          # fee 3.00; swept 339,503.00 from Treasury (25,000,000.00 there)
        fill_id = r.body["fills"][0]["fill_id"]
        rt.clock.set("2026-10-20T00:35:00-04:00")
        self.settle("2026-10-20")                                           # PB settled 0, payable 0
        # Treasury settled = 10,000,000 + 15,000,000 - 339,503.00 = 24,660,497.00 -> withdrawn: Treasury empty
        r = execute(rt, "treasury.withdraw", {"lifetime_id": T1, "amount_c": 2466049700, "currency": "USD"})
        self.assertTrue(r.ok, r.body)
        self.assertEqual(0, bal(rt, TREASURY, "1010"))
        # Tue 10:00: a 50,000.00 need with Treasury empty draws PB financing: headroom 50 % x 339,500 = 169,750.00 >= 50,000.00 -> Dr 1010 / Cr 2700 50,000.00
        rt.clock.set("2026-10-20T10:00:00-04:00")
        r = execute(rt, "test.fund", {"account_id": PB_GS, "need_c": 5000000, "link_type": "fill", "link_id": fill_id})
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("pb", 5000000, 5000000, True, None, 0), (r.body["source"], r.body["shortfall_c"], r.body["pb_draw_c"], r.body["funded"], r.body["sweep_id"], r.body["treasury_available_c"]))
        self.assertEqual((5000000, 5000000), (bal(rt, PB_GS, "2700"), bal(rt, PB_GS, "1010")))
        loan = one(rt, "SELECT * FROM loans WHERE loan_id=?", (f"PB-{PB_GS}",))
        self.assertEqual(("PB_MARGIN", 5000000, "SOFR", "0.0125", 360, "Goldman Sachs", "OPEN"), (loan["kind"], loan["principal_c"], loan["index_name"], loan["spread"], loan["day_basis"], loan["lender"], loan["status"]))
        # headroom after the draw: 169,750.00 - 50,000.00 = 119,750.00
        self.assertEqual(11975000, rt.accounting.buying_power(PB_GS, "USD").pb_headroom_c)
        assert_integrity(self, rt, "full")                                  # L14: PB_MARGIN principal 50,000 = balance(2700)
        # night of Tue 10-20 (posted Wed 00:35): base = 2700 at 17:00 ET = 50,000.00; SOFR 3.87 (obs 10-19) + 1.25 = 5.12 %;
        # to_c(50,000 x 0.0512 / 360) = to_c(2,560 / 360) = to_c(7.1111) = 7.11 -> Dr 5100 / Cr 2300
        rt.clock.set("2026-10-21T00:35:00-04:00")
        a = accrue(rt, PB_GS, "2026-10-20")
        self.assertTrue(a.ok, a.body)
        self.assertEqual((711, "0.0512", "0.0125", {"series": "SOFR", "value": "3.87", "obs_date": "2026-10-19"}, 5000000, 711, "2026-10-20", False),
                         (a.body["amount_c"], a.body["rate"], a.body["spread"], a.body["index"], a.body["base_c"], a.body["cum_c"], a.body["segment_start"], a.body["skipped"]))
        self.assertEqual([("5100", "USD", 711, 711, "1"), ("2300", "USD", -711, -711, "1")], lines_of(rt, a.body["entry_id"]))
        memo = one(rt, "SELECT memo FROM journal_entries WHERE entry_id=?", (a.body["entry_id"],))["memo"]
        self.assertIn("SOFR 3.87 % (2026-10-19) + 1.25 % = 5.12 %, ACT/360, night of 2026-10-20", memo)
        self.assertIn("base 50,000.00", memo)
        self.assertEqual([("ACCRUED", PB_GS, "pb", 711)], [(e["type"], e["account_id"], json.loads(e["payload_json"])["kind"], json.loads(e["payload_json"])["amount_c"])
                                                           for e in q(rt, "SELECT * FROM events WHERE type='ACCRUED'")])
        # idempotent: the same request id replays; another id for the same night is skipped without a second posting
        a2 = accrue(rt, PB_GS, "2026-10-20")
        self.assertEqual((True, True, 711), (a2.ok, a2.replayed, a2.body["amount_c"]))
        a3 = accrue(rt, PB_GS, "2026-10-20", request_id=rid("job"))
        self.assertEqual((True, 0, "already posted", a.body["entry_id"]), (a3.body["skipped"], a3.body["amount_c"], a3.body["note"], a3.body["entry_id"]))
        self.assertEqual(711, bal(rt, PB_GS, "2300"))
        self.assertEqual((400, "NIGHT_NOT_ENDED"), (lambda x: (x.http, x.code))(accrue(rt, PB_GS, "2026-10-21")))
        self.assertEqual((400, "NIGHT_BEFORE_OPEN"), (lambda x: (x.http, x.code))(accrue(rt, PB_GS, "2026-10-18")))
        self.assertEqual((400, "ACCRUAL_KIND"), (lambda x: (x.http, x.code))(accrue(rt, PB_GS, "2026-10-20", kind="x")))
        # Wed 10:00: a real order: BUY 200 AAPL @ 339.50 = 67,900.00 + fee max(200 x 0.0030 = 0.60, 1.00) = 1.00 -> need 67,901.00 at T+1;
        # own available 50,000.00 -> shortfall 17,901.00; Treasury empty -> PB draw 17,901.00 (headroom 169,750 - 50,000 = 119,750.00 covers it)
        rt.clock.set("2026-10-21T10:00:00-04:00")
        r = self.market_buy(PB_GS, 200, "339.50")
        f2 = r.body["fills"][0]
        self.assertEqual((6790000, 100, []), (f2["gross_c"], f2["fee_c"], r.body["sweeps"]))
        self.assertEqual((6790100, 6790100, 6790100, 1200), (bal(rt, PB_GS, "2700"), bal(rt, PB_GS, "1010"), bal(rt, PB_GS, "2100"), rt.accounting.position(PB_GS, "AAPL").qty))
        self.assertEqual(6790100, one(rt, "SELECT principal_c FROM loans WHERE loan_id=?", (f"PB-{PB_GS}",))["principal_c"])
        # headroom now: 1,200 x 339.50 = 407,400.00 -> 50 % = 203,700.00 - 67,901.00 drawn = 135,799.00
        self.assertEqual(13579900, rt.accounting.buying_power(PB_GS, "USD").pb_headroom_c)
        # night of Wed 10-21: base 67,901.00 -> the segment restarts: to_c(67,901 x 0.0512 / 360) = to_c(3,476.5312 / 360) = to_c(9.6570) = 9.66
        rt.clock.set("2026-10-22T00:35:00-04:00")
        a = accrue(rt, PB_GS, "2026-10-21")
        self.assertEqual((966, 6790100, 966, "2026-10-21"), (a.body["amount_c"], a.body["base_c"], a.body["cum_c"], a.body["segment_start"]))
        st = one(rt, "SELECT segment_start, base_c, rate, day_basis, cum_c, accrued_through FROM accrual_state WHERE kind='pb' AND ref_id=?", (PB_GS,))
        self.assertEqual(("2026-10-21", 6790100, "0.0512", 360, 966, "2026-10-21"), tuple(st.values()))
        self.assertEqual((1677, 1677), (bal(rt, PB_GS, "5100"), bal(rt, PB_GS, "2300")))    # 7.11 + 9.66 = 16.77 accrued, paid monthly (WP-08)
        with rt.db.reader() as conn:
            self.assertEqual([date(2026, 10, 22), date(2026, 10, 23)], prime.nights_due(conn, PB_GS, date(2026, 10, 24), "pb"))
        # per-PB isolation: JPM holds nothing -> headroom 0 -> a need there is refused even though GS has headroom
        rt.clock.set("2026-10-22T10:00:00-04:00")
        r = execute(rt, "test.fund", {"account_id": PB_JPM, "need_c": 10000000, "link_type": "fill", "link_id": fill_id})
        self.assertEqual((409, "INSUFFICIENT_FUNDS", 0, 0), (r.http, r.code, r.body["error"]["details"]["pb_draw_c"], rt.accounting.buying_power(PB_JPM, "USD").pb_headroom_c))
        # beyond the headroom at GS: need 300,000.00 - own 67,901.00 = 232,099.00 > 135,799.00 -> 409 INSUFFICIENT_FUNDS, nothing drawn (rolled back)
        r = execute(rt, "test.fund", {"account_id": PB_GS, "need_c": 30000000, "link_type": "fill", "link_id": fill_id})
        d = r.body["error"]["details"]
        self.assertEqual((409, "INSUFFICIENT_FUNDS", 6790100, 0, 13579900, 23209900 - 13579900), (r.http, r.code, d["own_available_c"], d["treasury_available_c"], d["pb_draw_c"], d["shortfall_c"]))
        self.assertEqual(6790100, bal(rt, PB_GS, "2700"))
        # pb_limit_c 80,000.00 caps the headroom at limit - drawn = 80,000.00 - 67,901.00 = 12,099.00 (L14: 2700 <= pb_limit_c)
        self.assertTrue(execute(rt, "account.update_settings", {"account_id": PB_GS, "pb_limit_c": 8000000}).ok)
        h = rt.prime.headroom(PB_GS)
        self.assertEqual((1209900, 1209900, 13579900, 8000000), (h["headroom_c"], h["headroom_by_limit_c"], h["uncapped_c"], h["pb_limit_c"]))
        self.assertEqual(1209900, rt.accounting.buying_power(PB_GS, "USD").pb_headroom_c)
        self.assertEqual([("LOAN_DRAWN", 2)], [(t, one(rt, "SELECT COUNT(*) n FROM events WHERE type=?", (t,))["n"]) for t in ("LOAN_DRAWN",)])
        assert_integrity(self, rt, "full")

    # ---- FX_CCS_PLAN rev 2 D5 (1.0.6.1): the cross-currency terms - seeds, signed values, ranges, the re-mark hint, the event -----------------
    def test_fx_ccs_d5_cross_currency_terms(self):
        # seeds on all nine brokers (ASSUMED): both basis levels and the EUR OIS spread 0.0 bp; the CCS half-width = the broker's IRS half-width
        for b in prime.BROKERS:
            sd = prime.seed_schedule(b)
            self.assertEqual(("0.0", "0.0", "0.0", sd["irs_half_width_bp"]),
                             (sd["xccy_basis_eur_bp"], sd["xccy_basis_gbp_bp"], sd["eur_ois_spread_bp"], sd["xccy_basis_hw_bp"]), b)
        self.assertEqual({"GS": "1.0", "JPM": "1.0", "MS": "1.2", "DB": "1.5", "BARC": "1.2", "CITI": "1.0", "UBS": "1.2", "BNP": "1.5", "BOFA": "1.0"},
                         {b: prime.seed_schedule(b)["xccy_basis_hw_bp"] for b in prime.BROKERS})
        specs = {r["field"]: r for r in prime.field_specs()}
        self.assertEqual(("EUR/USD cross-currency basis level (mid)", "bp", "-200", "200", True),
                         tuple(specs["xccy_basis_eur_bp"][k] for k in ("label", "unit", "min", "max", "signed")))
        self.assertEqual(("-200", "200", True), (specs["xccy_basis_gbp_bp"]["min"], specs["xccy_basis_gbp_bp"]["max"], specs["xccy_basis_gbp_bp"]["signed"]))
        self.assertEqual(("0", "100"), (specs["xccy_basis_hw_bp"]["min"], specs["xccy_basis_hw_bp"]["max"]))
        self.assertNotIn("signed", specs["xccy_basis_hw_bp"])
        self.assertEqual(("-100", "100", True), (specs["eur_ois_spread_bp"]["min"], specs["eur_ois_spread_bp"]["max"], specs["eur_ois_spread_bp"]["signed"]))
        for k in ("xccy_basis_eur_bp", "xccy_basis_gbp_bp", "eur_ois_spread_bp"):
            self.assertIn("ASSUMED", specs[k]["hint"])
            self.assertIn("re-marks every open", specs[k]["hint"])              # an edit re-marks the open contracts at the next VM
        self.assertIn("not a neutral one", specs["xccy_basis_eur_bp"]["hint"])
        rt = self.start()
        self.eligible()
        self.assertTrue(open_pb(rt, "GS").ok)
        # a schedule stored before 1.0.6.1 (without the four fields) reads the seeds
        with rt.db.writer() as tx:
            stored = json.loads(tx.conn.execute("SELECT schedule_json FROM pb_schedules WHERE account_id=?", (PB_GS,)).fetchone()[0])
            for k in prime.XCCY_TERM_FIELDS:
                stored.pop(k, None)
            tx.conn.execute("UPDATE pb_schedules SET schedule_json=? WHERE account_id=?", (json.dumps(stored), PB_GS))
        s = rt.prime.schedule(PB_GS)
        self.assertEqual((Decimal("0.0"), Decimal("0.0"), Decimal("1.0"), Decimal("0.0")),
                         (s.xccy_basis_eur_bp, s.xccy_basis_gbp_bp, s.xccy_basis_hw_bp, s.eur_ois_spread_bp))
        # signed values: the EUR/USD level -12.5 bp and the EUR OIS spread -100 bp (the lower bound) are accepted; one SETTINGS_CHANGED
        r = update_schedule(rt, PB_GS, request_id="ui:sched-gs-xccy-1", xccy_basis_eur_bp="-12.5", eur_ois_spread_bp="-100")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(({"eur_ois_spread_bp": "0.0", "xccy_basis_eur_bp": "0.0"}, {"eur_ois_spread_bp": "-100", "xccy_basis_eur_bp": "-12.5"}),
                         (r.body["before"], r.body["after"]))
        ev = one(rt, "SELECT * FROM events WHERE type='SETTINGS_CHANGED' AND account_id=? ORDER BY id DESC LIMIT 1", (PB_GS,))
        pl = json.loads(ev["payload_json"])
        self.assertEqual((["eur_ois_spread_bp", "xccy_basis_eur_bp"], {"xccy_basis_eur_bp": "-12.5", "eur_ois_spread_bp": "-100"}), (pl["fields"], pl["after"]))
        t = prime.xccy_terms(rt, PB_GS)
        self.assertEqual((Decimal("-12.5"), Decimal("0.0"), Decimal("1.0"), Decimal("-100"), Decimal("1.0"), "GS"),
                         (t["xccy_basis_eur_bp"], t["xccy_basis_gbp_bp"], t["xccy_basis_hw_bp"], t["eur_ois_spread_bp"], t["otc_spread_mult"], t["broker"]))
        self.assertEqual(("Goldman Sachs schedule (as edited), ASSUMED", rt.clock.epoch()), (t["source"], t["updated_ts"]))
        self.assertEqual("FX desk: no prime-broker schedule (0.0 bp, ASSUMED)", prime.xccy_terms(rt, None)["source"])
        # out of range or unsigned: refused, nothing written
        for fields in ({"xccy_basis_eur_bp": "-200.1"}, {"xccy_basis_gbp_bp": "250"}, {"xccy_basis_hw_bp": "-1"}, {"eur_ois_spread_bp": "100.5"},
                       {"xccy_basis_hw_bp": "101"}, {"xccy_basis_eur_bp": ""}):
            r = update_schedule(rt, PB_GS, **fields)
            self.assertEqual((400, "SCHEDULE_VALUE"), (r.http, r.code), fields)
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM events WHERE type='SETTINGS_CHANGED' AND account_id=?", (PB_GS,))["n"])
        self.assertIn("-200 to 200 bp", update_schedule(rt, PB_GS, xccy_basis_eur_bp="-201").body["error"]["message"])

    # ---- §19.23: schedule edit 0.0030 -> 0.0025 = fee 2.50 with the before/after event; validation; past postings never restated -----------
    def test_19_23_schedule_edit_fee_2_50_before_after_event_and_validation(self):
        rt = self.start()                                                   # Mon 2026-10-19 10:00 ET
        self.eligible()
        self.assertTrue(open_pb(rt, "GS").ok)
        self.assertTrue(fund(rt, PB_GS, 100000000).ok)                       # 1,000,000.00 advanced: the buys need no sweep
        f1 = self.market_buy(PB_GS, 1000, "339.50").body["fills"][0]
        self.assertEqual(300, f1["fee_c"])                                  # seed: 1,000 x 0.0030 = 3.00
        entries_before = one(rt, "SELECT COUNT(*) n FROM journal_entries")["n"]
        r = update_schedule(rt, PB_GS, request_id="ui:sched-gs-00001", equity_per_share="0.0025")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(({"equity_per_share": "0.0030"}, {"equity_per_share": "0.0025"}, "0.0025", rt.clock.epoch(), "GS"),
                         (r.body["before"], r.body["after"], r.body["schedule"]["equity_per_share"], r.body["updated_ts"], r.body["broker"]))
        ev = one(rt, "SELECT * FROM events WHERE type='SETTINGS_CHANGED' AND account_id=?", (PB_GS,))
        pl = json.loads(ev["payload_json"])
        self.assertEqual(("schedule", PB_GS, {"equity_per_share": "0.0030"}, {"equity_per_share": "0.0025"}, ["equity_per_share"], False), (ev["ref_type"], ev["ref_id"], pl["before"], pl["after"], pl["fields"], pl["restated"]))
        self.assertEqual(entries_before, one(rt, "SELECT COUNT(*) n FROM journal_entries")["n"])     # light: nothing posted
        self.assertTrue(update_schedule(rt, PB_GS, request_id="ui:sched-gs-00001", equity_per_share="0.0025").replayed)
        v = rt.prime.schedule_view(PB_GS)
        self.assertEqual(("0.0025", "0.0030", 100, "pb_schedules"), (v["schedule"]["equity_per_share"], v["seed"]["equity_per_share"], v["schedule"]["equity_min_c"], v["source"]))
        stored = json.loads(one(rt, "SELECT schedule_json FROM pb_schedules WHERE account_id=?", (PB_GS,))["schedule_json"])
        self.assertEqual(("0.0025", "0.0030"), (stored["equity_per_share"], v["seed"]["equity_per_share"]))
        # the next 1,000-share buy: 1,000 x 0.0025 = 2.50; the first fill keeps its 3.00 (never restated); 5000 AAPL = 3.00 + 2.50 = 5.50
        rt.clock.set("2026-10-19T10:05:00-04:00")
        pv = orders.preview(rt, {"account_id": PB_GS, "instrument_id": "AAPL", "side": "BUY", "qty": 1000, "order_type": "MARKET"})
        self.assertEqual(("2.50", "USD 0.0025/share, min 1.00"), (pv["economics"]["fee"], pv["economics"]["fee_basis"]))
        f2 = self.market_buy(PB_GS, 1000, "339.50").body["fills"][0]
        self.assertEqual((250, 300), (f2["fee_c"], one(rt, "SELECT fee_c FROM fills WHERE fill_id=?", (f1["fill_id"],))["fee_c"]))
        self.assertEqual(550, bal(rt, PB_GS, "5000", inst="AAPL"))
        # minimum 1.50: 100 shares x 0.0025 = 0.25 -> floored to 1.50
        self.assertTrue(update_schedule(rt, PB_GS, equity_min_c=150).ok)
        rt.clock.set("2026-10-19T10:06:00-04:00")
        self.assertEqual(150, self.market_buy(PB_GS, 100, "339.50").body["fills"][0]["fee_c"])
        # validation: every refusal leaves the schedule and the event log untouched
        bad = [({"equity_per_share": "-1"}, "SCHEDULE_VALUE"), ({"swap_margin_mode": "XYZ"}, "SCHEDULE_VALUE"), ({"bogus": "1"}, "SCHEDULE_FIELD"),
               ({"equity_per_share": "0.0025"}, "NOTHING_TO_UPDATE"), ({}, "NOTHING_TO_UPDATE"), ({"equity_min_c": "abc"}, "SCHEDULE_VALUE"),
               ({"equity_min_c": -5}, "SCHEDULE_VALUE"), ({"equity_per_share": 0.0025}, "SCHEDULE_VALUE"), ({"otc_spread_mult": "0"}, "SCHEDULE_VALUE"),
               ({"equity_per_share": "0.123456789"}, "SCHEDULE_VALUE"), ({"financing_spread": "1.5"}, "SCHEDULE_VALUE"), ({"fields": "x"}, "SCHEDULE_FIELD")]
        for fields, code in bad:
            r = update_schedule(rt, PB_GS, **fields)
            self.assertEqual((400, code), (r.http, r.code), fields)
        self.assertEqual((404, "NOT_PB"), (lambda x: (x.http, x.code))(update_schedule(rt, US_EXCHANGE, equity_per_share="0.0025")))
        self.assertEqual((404, "ACCOUNT_UNKNOWN"), (lambda x: (x.http, x.code))(update_schedule(rt, "T1:NOPE", equity_per_share="0.0025")))
        self.assertEqual(2, one(rt, "SELECT COUNT(*) n FROM events WHERE type='SETTINGS_CHANGED' AND ref_type='schedule'")["n"])
        # several fields in one command (nested form, enum case-insensitive): one event with every before / after pair
        r = update_schedule(rt, PB_GS, fields={"swap_margin_mode": "stm", "monthly_minimum_c": 50000, "otc_spread_mult": "1.5", "trs_spread": "0.0070", "equity_min_c": 150})
        self.assertTrue(r.ok, r.body)
        self.assertEqual(({"swap_margin_mode": "CTM", "monthly_minimum_c": 0, "otc_spread_mult": "1.0", "trs_spread": "0.0060"},
                          {"swap_margin_mode": "STM", "monthly_minimum_c": 50000, "otc_spread_mult": "1.5", "trs_spread": "0.0070"}), (r.body["before"], r.body["after"]))
        s = rt.prime.schedule(PB_GS)
        self.assertEqual(("STM", 50000, Decimal("1.5"), Decimal("0.0070"), Decimal("0.0025"), 150), (s.swap_margin_mode, s.monthly_minimum_c, s.otc_spread_mult, s.trs_spread, s.equity_per_share, s.equity_min_c))
        self.assertEqual(3, one(rt, "SELECT COUNT(*) n FROM events WHERE type='SETTINGS_CHANGED' AND ref_type='schedule'")["n"])
        self.assertEqual("0.0030", prime.parse_field("equity_per_share", "0.0030"))
        self.assertEqual(("CTM", 150), (prime.parse_field("swap_margin_mode", "ctm"), prime.parse_field("equity_min_c", "150")))
        assert_integrity(self, rt, "full")

    # ---- §19.23: PB credit interest on positive settled USD: 1,000,000 x (3.87 - 0.75) % / 360 = 86.67 -------------------------------------------
    def test_19_23_pb_credit_interest_86_67(self):
        rt = self.start(at="2026-10-20T11:00:00-04:00")                     # Tue 2026-10-20 11:00 ET
        self.eligible()
        self.assertTrue(open_pb(rt, "GS").ok)
        self.assertTrue(open_pb(rt, "JPM").ok)
        self.assertTrue(fund(rt, PB_GS, 100000000).ok)                       # settled USD 1,000,000.00 in F1:PB_GS's role here, T1:PB_GS
        # night of Tue 10-20 (posted Wed 00:35): rate max(0, 3.87 - 0.75) = 3.12 %; to_c(1,000,000 x 0.0312 / 360) = to_c(31,200 / 360) = to_c(86.6667) = 86.67
        rt.clock.set("2026-10-21T00:35:00-04:00")
        a = accrue(rt, PB_GS, "2026-10-20", kind="pbcredit")
        self.assertTrue(a.ok, a.body)
        self.assertEqual((8667, "0.0312", "0.0075", 100000000, 8667, "2026-10-20", False, "pbcredit"),
                         (a.body["amount_c"], a.body["rate"], a.body["spread"], a.body["base_c"], a.body["cum_c"], a.body["segment_start"], a.body["skipped"], a.body["kind"]))
        self.assertEqual([("1230", "USD", 8667, 8667, "1"), ("4300", "USD", -8667, -8667, "1")], lines_of(rt, a.body["entry_id"]))
        self.assertEqual([(None,), ("USD",)], [(l["instrument_id"],) for l in q(rt, "SELECT instrument_id FROM journal_lines WHERE entry_id=? ORDER BY line_no", (a.body["entry_id"],))])
        memo = one(rt, "SELECT memo FROM journal_entries WHERE entry_id=?", (a.body["entry_id"],))["memo"]
        self.assertIn("SOFR 3.87 % (2026-10-19) - 0.75 % = 3.12 %, ACT/360, night of 2026-10-20", memo)
        self.assertEqual("accr:pbcredit:%s:2026-10-20" % PB_GS, one(rt, "SELECT idem_key FROM journal_entries WHERE entry_id=?", (a.body["entry_id"],))["idem_key"])
        # night of Wed 10-21: cum to_c(2 x 86.6667) = to_c(173.3333) = 173.33 -> 86.66
        rt.clock.set("2026-10-22T00:35:00-04:00")
        a = accrue(rt, PB_GS, "2026-10-21", kind="pbcredit")
        self.assertEqual((8666, 17333), (a.body["amount_c"], a.body["cum_c"]))
        st = one(rt, "SELECT segment_start, base_c, rate, day_basis, cum_c, accrued_through FROM accrual_state WHERE kind='pbcredit' AND ref_id=?", (PB_GS,))
        self.assertEqual(("2026-10-20", 100000000, "0.0312", 360, 17333, "2026-10-21"), tuple(st.values()))
        self.assertEqual((17333, 17333), (bal(rt, PB_GS, "1230"), bal(rt, PB_GS, "4300")))
        # NAV(PB_GS) = 1,000,000.00 + 173.33 accrued = 1,000,173.33; income 173.33; no position was created by the 4300 'USD' line
        nav = rt.accounting.nav(PB_GS)
        self.assertEqual((100017333, 17333, 0), (nav.nav_c, nav.income_c, nav.unrealised_c))
        self.assertEqual(17333, rt.accounting.cash(PB_GS)["USD"].accrued_c)
        self.assertEqual([], rt.accounting.positions(PB_GS))
        with rt.db.reader() as conn:
            self.assertEqual([date(2026, 10, 22), date(2026, 10, 23)], prime.nights_due(conn, PB_GS, date(2026, 10, 24), "pbcredit"))
            self.assertEqual([date(2026, 10, 20), date(2026, 10, 21)], prime.nights_due(conn, PB_GS, date(2026, 10, 22), "pb"))
        # financing kind with nothing drawn: the night is recorded with 0 (no entry, never retried); JPM holds no cash: credit interest 0
        a = accrue(rt, PB_GS, "2026-10-20", kind="pb")
        self.assertEqual((True, 0, None, "no PB financing drawn at 17:00 ET"), (a.body["skipped"], a.body["amount_c"], a.body["entry_id"], a.body["note"]))
        a = accrue(rt, PB_JPM, "2026-10-20", kind="pbcredit")
        self.assertEqual((True, "no positive settled USD at 17:00 ET"), (a.body["skipped"], a.body["note"]))
        with rt.db.reader() as conn:
            self.assertEqual([date(2026, 10, 21)], prime.nights_due(conn, PB_GS, date(2026, 10, 22), "pb"))
        # a 5 % spread floors the rate at 0: night of Thu 10-22 posts nothing, 1230 unchanged
        self.assertTrue(update_schedule(rt, PB_GS, credit_interest_spread="0.05").ok)
        rt.clock.set("2026-10-23T00:35:00-04:00")
        a = accrue(rt, PB_GS, "2026-10-22", kind="pbcredit")
        self.assertEqual((True, 0, "0"), (a.body["skipped"], a.body["amount_c"], a.body["rate"]))
        self.assertIn("floored", a.body["note"])
        self.assertEqual(17333, bal(rt, PB_GS, "1230"))
        # no SOFR observation -> 409 RATE_UNAVAILABLE, nothing defaulted; no accrual helper -> 400 NOT_IN_THIS_BUILD
        self.assertTrue(update_schedule(rt, PB_GS, credit_interest_spread="0.0075").ok)
        rt.rates = RateStore()
        rt.clock.set("2026-10-24T00:35:00-04:00")
        a = accrue(rt, PB_GS, "2026-10-23", kind="pbcredit")
        self.assertEqual((409, "RATE_UNAVAILABLE", "SOFR"), (a.http, a.code, a.body["error"]["details"]["series"]))
        rt.rates.put("SOFR", [(date(2026, 10, 19), Decimal("3.87"))], persist=False)
        rt.lifecycle = None
        a = accrue(rt, PB_GS, "2026-10-23", kind="pbcredit")
        self.assertEqual((400, "NOT_IN_THIS_BUILD"), (a.http, a.code))
        self.assertEqual(17333, bal(rt, PB_GS, "1230"))
        assert_integrity(self, rt, "full")

    # ---- §19.23: monthly minimum 500.00 with 120.00 of fees -> 380.00; with 600.00 -> SKIPPED; sweep and overdraft funding ------------------
    def test_19_23_monthly_minimum_380_and_skipped(self):
        rt = self.start(at="2026-10-20T11:00:00-04:00")                     # Tue 2026-10-20 11:00 ET
        self.eligible()
        for b in ("GS", "JPM", "BOFA", "MS", "DB"):
            self.assertTrue(open_pb(rt, b).ok)
        for acct in (PB_GS, PB_JPM, PB_MS, PB_DB):
            self.assertTrue(update_schedule(rt, acct, monthly_minimum_c=50000).ok)
        self.assertTrue(fund(rt, PB_GS, 40000000).ok)                        # 400,000.00 each
        self.assertTrue(fund(rt, PB_JPM, 40000000).ok)
        # October fees: GS 120.00 on one fill, JPM 600.00 (test fills with explicit fees; gross 339,500.00 each)
        self.assertTrue(buy(rt, PB_GS, "AAPL", 1000, "339.50", fee_c=12000).ok)
        self.assertTrue(buy(rt, PB_JPM, "AAPL", 1000, "339.50", fee_c=60000).ok)
        rt.clock.set("2026-10-21T00:35:00-04:00")
        self.settle("2026-10-21")                                           # GS settled 400,000 - 339,620 = 60,380.00; JPM 400,000 - 340,100 = 59,900.00
        self.assertEqual((6038000, 5990000), (bal(rt, PB_GS, "1010"), bal(rt, PB_JPM, "1010")))
        with rt.db.reader() as conn:
            self.assertEqual((12000, 60000, 0), (prime.fees_charged_c(conn, PB_GS, "2026-10"), prime.fees_charged_c(conn, PB_JPM, "2026-10"), prime.fees_charged_c(conn, PB_GS, "2026-09")))
        r = monthly_min(rt, PB_GS, "2026-10")
        self.assertEqual((400, "MONTH_NOT_ENDED"), (r.http, r.code))
        # Nov 1 00:45 (PAY_MONTHLY): GS max(0, 500.00 - 120.00) = 380.00 -> Dr 5000 (PB:GS) / Cr 1010 380.00; settled 60,380.00 - 380.00 = 60,000.00
        rt.clock.set("2026-11-01T00:45:00-04:00")
        r = monthly_min(rt, PB_GS, "2026-10")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((38000, 50000, 12000, 38000, False, "own", 6000000), (r.body["amount_c"], r.body["minimum_c"], r.body["fees_charged_c"], r.body["shortfall_c"], r.body["skipped"],
                                                                             r.body["funding"]["source"], r.body["settled_c"]))
        lines = q(rt, "SELECT gl, amount_c, instrument_id FROM journal_lines WHERE entry_id=? ORDER BY line_no", (r.body["entry_id"],))
        self.assertEqual([("5000", 38000, "PB:GS"), ("1010", -38000, None)], [(l["gl"], l["amount_c"], l["instrument_id"]) for l in lines])
        e = one(rt, "SELECT idem_key, memo, kind FROM journal_entries WHERE entry_id=?", (r.body["entry_id"],))
        self.assertEqual(("pay:pbmin:%s:2026-10" % PB_GS, "PB monthly minimum 2026-10: minimum 500.00 - fees 120.00 = 380.00", "FEE"), (e["idem_key"], e["memo"], e["kind"]))
        self.assertEqual((6000000, 38000), (bal(rt, PB_GS, "1010"), bal(rt, PB_GS, "5000", inst="PB:GS")))
        self.assertEqual([("PAID", PB_GS, "pbmin", 38000)], [(x["type"], x["account_id"], json.loads(x["payload_json"])["kind"], json.loads(x["payload_json"])["amount_c"]) for x in q(rt, "SELECT * FROM events WHERE type='PAID'")])
        # JPM: 600.00 of fees >= 500.00 -> SKIPPED, nothing posted; GS again -> already charged; BofA has no minimum
        r = monthly_min(rt, PB_JPM, "2026-10")
        self.assertEqual((True, 0, 60000, "fees 600.00 >= minimum 500.00", None), (r.body["skipped"], r.body["shortfall_c"], r.body["fees_charged_c"], r.body["note"], r.body["entry_id"]))
        self.assertEqual(5990000, bal(rt, PB_JPM, "1010"))
        r = monthly_min(rt, PB_GS, "2026-10", request_id=rid("job"))
        self.assertEqual((True, "already charged"), (r.body["skipped"], r.body["note"]))
        self.assertEqual((True, "no monthly minimum"), (lambda x: (x.body["skipped"], x.body["note"]))(monthly_min(rt, PB_BOFA, "2026-10")))
        for m, code in (("2026-11", "MONTH_NOT_ENDED"), ("2026-1", "MONTH"), ("2026-13", "MONTH"), ("", "MONTH")):
            r = monthly_min(rt, PB_GS, m, request_id=rid("job"))
            self.assertEqual((400, code), (r.http, r.code), m)
        # MS: no fees, no cash -> the 500.00 is swept from Treasury (linked to the charge's idem key, L19) and paid: MS settled 0
        r = monthly_min(rt, PB_MS, "2026-10")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((50000, "sweep", 50000, 0), (r.body["amount_c"], r.body["funding"]["source"], r.body["funding"]["sweep_c"], bal(rt, PB_MS, "1010")))
        t = one(rt, "SELECT * FROM transfers WHERE kind='SWEEP_IN' AND to_account=?", (PB_MS,))
        self.assertEqual((50000, "pbmin", "pay:pbmin:%s:2026-10" % PB_MS, 50000), (t["amount_c"], t["linked_type"], t["linked_id"], t["shortfall_c"]))
        assert_integrity(self, rt, "full")
        # DB with Treasury empty: Treasury settled = 25,000,000 - 400,000 - 400,000 - 500 = 24,199,500.00 withdrawn -> the §11.6 overdraft rule:
        # the charge is still posted, DB settled -500.00, one ERROR FUNDING_SHORTFALL open, L18 accepts it
        self.assertTrue(execute(rt, "treasury.withdraw", {"lifetime_id": T1, "amount_c": 2419950000, "currency": "USD"}).ok)
        self.assertEqual(0, bal(rt, TREASURY, "1010"))
        r = monthly_min(rt, PB_DB, "2026-10")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((50000, "overdraft", False, -50000), (r.body["amount_c"], r.body["funding"]["source"], r.body["funding"]["funded"], bal(rt, PB_DB, "1010")))
        self.assertEqual([("ERROR", PB_DB, None)], [(x["severity"], x["account_id"], x["resolved_ts"]) for x in q(rt, "SELECT * FROM attention WHERE code='FUNDING_SHORTFALL'")])
        assert_integrity(self, rt, "full")

    # ---- the OTC gate (Build 2 P12: options and forwards are live); account.close only a flat PB account (PO-16: the relationship read) ---------
    def test_otc_gate_and_close(self):
        self.assertEqual(("IRS", "TRS", "XCCY"), (prime.require_otc_kind("irs"), prime.require_otc_kind("TRS"), prime.require_otc_kind(" xccy ")))
        self.assertEqual(("EQ_OPTION", "FX_OPTION", "EQ_FORWARD", "FX_FORWARD"), tuple(prime.require_otc_kind(k) for k in ("eq_option", "FX_OPTION", "EQ_FORWARD", " fx_forward")))
        for k in ("FRA", "CAP", "FLOOR", "SWAPTION", "OPTION", "FORWARD"):           # not offered, and the two generic names (not products)
            with self.assertRaises(RejectError) as cm:
                prime.require_otc_kind(k)
            self.assertEqual(("NOT_IN_THIS_BUILD", 400), (cm.exception.code, cm.exception.http), k)
        with self.assertRaises(RejectError) as cm:
            prime.require_otc_kind("OPTION")
        self.assertIn("EQ_OPTION (equity) or FX_OPTION (FX)", cm.exception.message)
        with self.assertRaises(RejectError) as cm:
            prime.require_otc_kind("BANANA")
        self.assertEqual("OTC_KIND", cm.exception.code)
        rows = prime.otc_unsupported()
        self.assertEqual((4, {"NOT_IN_THIS_BUILD"}), (len(rows), {x["state"] for x in rows}))
        self.assertIn("PB OTC: forward rate agreements", [x["item"] for x in rows])
        rt = self.start()
        self.eligible()
        self.assertTrue(open_pb(rt, "GS").ok)
        self.assertTrue(fund(rt, PB_GS, 100000).ok)                          # 1,000.00 in the account: not flat
        r = execute(rt, "account.close", {"account_id": PB_GS})
        self.assertEqual((409, "NOT_FLAT"), (r.http, r.code))
        self.assertIn("Cash - settled USD 1,000.00", r.body["error"]["details"]["reasons"])   # what it holds, in words (the advance on 3100 is history)
        with rt.db.reader() as conn:                                        # PO-16: the same test as a read, with what the account was opened with
            rel = prime.relationship(conn, PB_GS)
        self.assertEqual((False, r.body["error"]["details"]["reasons"], "GS", 0, None), (rel["flat"], rel["reasons"], rel["broker"], rel["schedule_edits"], rel["last_edit"]))
        self.assertEqual("no agreements recorded with Goldman Sachs (the account was opened without the onboarding dialog)", rel["agreements_line"])
        self.assertTrue(execute(rt, "treasury.transfer", {"from_account": PB_GS, "to_account": TREASURY, "currency": "USD", "amount_c": 100000}).ok)
        with rt.db.reader() as conn:
            self.assertEqual((True, []), (prime.relationship(conn, PB_GS)["flat"], prime.close_check(conn, PB_GS)))
        r = execute(rt, "account.close", {"account_id": PB_GS})
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("CLOSED", rt.clock.epoch()), (r.body["status"], r.body["closed_ts"]))
        self.assertEqual(("CLOSED", rt.clock.epoch()), (row(rt, PB_GS)["status"], row(rt, PB_GS)["closed_ts"]))
        self.assertEqual((409, "EXISTS", "CLOSED"), (lambda x: (x.http, x.code, x.body["error"]["details"]["status"]))(open_pb(rt, "GS")))
        self.assertEqual((400, "ACCOUNT_CLOSED"), (lambda x: (x.http, x.code))(update_schedule(rt, PB_GS, equity_per_share="0.0025")))
        self.assertEqual((404, "NOT_PB"), (lambda x: (x.http, x.code))(execute(rt, "account.close", {"account_id": US_EXCHANGE})))
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM events WHERE type='ACCOUNT_CLOSED'")["n"])
        assert_integrity(self, rt, "full")


    # ---- SR-10 / C22: initial margin follows each prime broker's schedule (money) ----------------------------------------------------------
    def test_sr10_im_terms_follow_the_pb_schedule(self):
        from tests import test_terminal_swaps as sw
        from finsim.terminal.products import swaps
        rt = self.rt = sw.runtime(self.tmp)
        self.assertTrue(deposit(rt, 1510000000).ok)                          # T1 at 25,100,000.00: past the PB/OTC threshold (sw's module docstring)
        for broker in ("GS", "DB"):
            self.assertTrue(execute(rt, "test.open_pb_unchecked", {"lifetime_id": T1, "broker": broker}).ok)
        # the seeds differ by broker: GS keeps this build's static rule (50 bp / 10 % / 2 %), DB asks 60 bp / 15 % / 3 %; options 15 % / 20 %, forwards 6 % / 10 %
        gs, db = prime.im_params(rt, PB_GS), prime.im_params(rt, PB_DB)
        self.assertEqual((Decimal("50"), Decimal("0.10"), Decimal("0.02"), Decimal("0.15"), Decimal("0.06")), tuple(gs[k] for k in prime.IM_FIELDS))
        self.assertEqual((Decimal("60"), Decimal("0.15"), Decimal("0.03"), Decimal("0.20"), Decimal("0.10")), tuple(db[k] for k in prime.IM_FIELDS))
        # the IRS 10,000,000 2y pay-fixed of test_terminal_swaps (flat 4.00 %): annuity 1.90386435, |DV01| = 10,000,000 x 0.0001 x 1.90386435 =
        # 1,903.86 -> IM GS = 1,903.86 x 50 = 95,193.00; DB = 1,903.86 x 60 = 114,231.60 (its half-width 1.5 bp moves the dealt rate, not the DV01)
        p_gs, p_db = swaps.preview(rt, PB_GS, "IRS", sw.TERMS), swaps.preview(rt, PB_DB, "IRS", sw.TERMS)
        self.assertEqual((190386, 9519300, "0.04010"), (p_gs["dv01_c"], p_gs["im_c"], p_gs["dealt_rate"]))
        self.assertEqual((190386, 11423160, "0.04015"), (p_db["dv01_c"], p_db["im_c"], p_db["dealt_rate"]))
        # an edit applies to the next deal: DB irs_im_bp 40 -> 1,903.86 x 40 = 76,154.40; an option IM of 25 % is stored as 0.25
        self.assertTrue(update_schedule(rt, PB_DB, irs_im_bp="40", option_im_pct="0.25").ok)
        self.assertEqual((7615440, Decimal("0.25")), (swaps.preview(rt, PB_DB, "IRS", sw.TERMS)["im_c"], prime.im_params(rt, PB_DB)["option_im_pct"]))
        # ranges are the field's: a percentage of 100 % or more is refused (stored as a fraction), a vol half-width above 20 vol points too
        for fields in ({"option_im_pct": "1.5"}, {"eq_option_hw_vol": "0.25"}, {"irs_im_bp": "-1"}):
            r = update_schedule(rt, PB_DB, **fields)
            self.assertEqual((400, "SCHEDULE_VALUE"), (r.http, r.code), fields)


if __name__ == "__main__":
    unittest.main()
