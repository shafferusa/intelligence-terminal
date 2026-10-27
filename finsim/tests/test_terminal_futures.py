"""WP-04 futures tests (TERMINAL_DESIGN.md §13.4, §11.2 FUTURES_VM / FUTURES_FINAL, §11.6, §19.8).  Every expected number is
worked by hand in a comment.  The scaffolding (runtime with the orders handlers, quote-record builder, SQL helpers) comes
from test_terminal_orders; the lifecycle scheduler (WP-08) is not run here — the jobs are executed as bus commands with the
request ids the scheduler would use, and the sweep-out / shortfall-resolve steps of §19.8's unfunded variant use test-local
handlers that call the same module functions WP-08 will call."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import T1, TREASURY, US_EXCHANGE, assert_integrity, bal, deposit, execute, fund
from tests import test_terminal_orders as sc
from finsim.terminal import execution, treasury as _treasury
from finsim.terminal.clock import epoch_of
from finsim.terminal.products import futures
from finsim.terminal.quotes import Bar1m

GROUP = "CME_EQUITY_INDEX"
ES = "ESZ26"


def _h_sweep_out(ctx, payload):
    """What WP-08's SWEEP_OUT does for one account: ``treasury.sweep_out`` of the idle amount."""
    return {"transfer_id": _treasury.sweep_out(ctx, payload["account_id"], "USD", int(payload["amount_c"]), "test: idle cash")}


def _h_resolve(ctx, payload):
    """What WP-08's housekeeping does after a transfer: resolve FUNDING_SHORTFALL once 1010 is back at or above zero."""
    return {"resolved": execution.auto_resolve_shortfall(ctx, payload["account_id"], "USD")}


def _h_fut_fill(ctx, payload):
    """A direct futures fill through the module's ``apply_fill`` (the path execution.apply_fill dispatches to), with the
    standard fee: ``{"account_id", "instrument_id", "side", "qty", "price"}``."""
    inst = ctx.services.instruments.get(payload["instrument_id"], ctx.conn)
    spec = execution.FillSpec(None, payload["account_id"], inst.instrument_id, payload["side"], int(payload["qty"]), Decimal(str(payload["price"])), "ORDER", None,
                              f"test:{ctx.request_id}", None, "last")
    return futures.apply_fill(ctx, spec, ctx.services).to_dict()


def _h_roll(ctx, payload):
    return futures.roll_listings(ctx, ctx.services)


class FuturesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-fut-")
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- scaffolding ----------------------------------------------------------------------------------------------------
    def start(self, at="2026-10-19T10:00:00-04:00"):
        rt = sc.runtime(self.tmp, at=at)
        rt.futures = futures                       # what the integrator binds: services.futures
        futures.register(rt.bus)
        rt.bus.register("test.sweep_out", _h_sweep_out)
        rt.bus.register("test.resolve", _h_resolve)
        rt.bus.register("test.fut_fill", _h_fut_fill)
        rt.bus.register("test.roll", _h_roll)
        self.rt = rt
        return rt

    def quote(self, iid, last, quote_time, fetched_at, bar=None, group=GROUP):
        self.rt.marks.set_quote(sc.rec(iid, last, quote_time, fetched_at, bar=bar, group=group))
        self.rt.marks.set(iid, last)

    def market(self, iid, side, qty, last, volume=1000, group=GROUP):
        now = self.rt.clock.epoch()
        self.quote(iid, last, now - 120, now, bar=(now - 180, volume), group=group)
        r = sc.submit(self.rt, account_id=US_EXCHANGE, instrument_id=iid, side=side, qty=qty, order_type="MARKET")
        self.assertTrue(r.ok, r.body)
        self.assertEqual("FILLED", r.body["order"]["status"])
        return r

    def settle_quote(self, iid, price, d, group=GROUP):
        """A record whose last trade is AT the settle time, fetched two minutes after it."""
        st = self.rt.sessions.settle_time(group, d)
        self.quote(iid, price, st, st + 120, group=group)
        return st

    def vm(self, iid, ds, request_id=None, account=US_EXCHANGE, **extra):
        payload = dict(account_id=account, instrument_id=iid, date=ds, **extra)
        return execute(self.rt, "futures.vm", payload, request_id or f"job:FUTURES_VM:{account}:{iid}:{ds}", actor="lifecycle")

    def final(self, iid, request_id=None, account=US_EXCHANGE):
        return execute(self.rt, "futures.final", {"account_id": account, "instrument_id": iid}, request_id or f"job:FUTURES_FINAL:{account}:{iid}", actor="lifecycle")

    def lines(self, entry_id):
        return [(l["gl"], l["amount_c"], l["instrument_id"]) for l in sc.q(self.rt, "SELECT gl, amount_c, instrument_id FROM journal_lines WHERE entry_id=? ORDER BY line_no", (entry_id,))]

    def entry_by_idem(self, idem):
        return sc.one(self.rt, "SELECT * FROM journal_entries WHERE idem_key=?", (idem,))

    def n_entries(self):
        return sc.one(self.rt, "SELECT COUNT(*) n FROM journal_entries")["n"]

    def fs_row(self, iid, ds, account=US_EXCHANGE):
        row = sc.one(self.rt, "SELECT * FROM futures_settle WHERE account_id=? AND instrument_id=? AND settle_date=?", (account, iid, ds))
        if row is not None:
            import json
            row["info"] = json.loads(row["quote_json"])
        return row

    # ---- pure rules -----------------------------------------------------------------------------------------------------
    def test_im_required_and_reference_arithmetic(self):
        rt = self.start()
        es = rt.instruments.get(ES)
        zc = rt.instruments.get("ZCZ26")
        # ES IM 17,500.00 per contract = 1,750,000 c; 2 contracts 3,500,000 c; a short of 3 uses |qty| -> 5,250,000 c
        self.assertEqual((3500000, 5250000, 1750000), (futures.im_required_c(es, 2), futures.im_required_c(es, -3), futures.im_required_c(es, 1)))
        # ES MM 15,900.00 x 2 = 31,800.00 = 3,180,000 c; ZC IM 1,500.00 x 3 = 4,500.00 = 450,000 c
        self.assertEqual((3180000, 450000), (futures.maintenance_c(es, 2), futures.im_required_c(zc, 3)))
        self.assertEqual((-2, 1, 1, -1), (futures.signed_qty("SELL", 2), futures.signed_qty("BUY_TO_COVER", 1), futures.signed_qty("BUY", 1), futures.signed_qty("SELL_SHORT", 1)))
        mult, scale = es.multiplier, es.price_scale
        # opening from flat: one leg at the fill price, nothing unposted, reference = the price
        legs, unposted = futures.merge_fill([], 0, 2, Decimal("7754.25"), mult, scale)
        self.assertEqual(([(2, Decimal("7754.25"))], 0), (legs, unposted))
        self.assertEqual(Decimal("7754.25"), futures.weighted_reference(legs, unposted, mult, scale))
        # closing 2 @ 7,745.00 against a settle leg at 7,741.50: unposted 2 x 3.50 x 50 = 350.00 = 35,000 c; flat -> no reference
        legs, unposted = futures.merge_fill([(2, Decimal("7741.50"))], 0, -2, Decimal("7745.00"), mult, scale)
        self.assertEqual(([], 35000), (legs, unposted))
        self.assertIsNone(futures.weighted_reference(legs, unposted, mult, scale))
        # flip: short 1 @ 7,754.25, BUY 3 @ 7,750.00: the short closes with (-1) x (7,750 - 7,754.25) x 50 = +212.50 = 21,250 c, a long leg of 2 opens;
        # R = (2 x 7,750 - 21,250 / (100 x 50)) / 2 = (15,500 - 4.25) / 2 = 7,747.875, so 2 x (7,750 - 7,747.875) x 50 = 212.50 is the unposted gain
        legs, unposted = futures.merge_fill([(-1, Decimal("7754.25"))], 0, 3, Decimal("7750.00"), mult, scale)
        self.assertEqual(([(2, Decimal("7750"))], 21250), (legs, unposted))
        self.assertEqual(Decimal("7747.875"), futures.weighted_reference(legs, unposted, mult, scale))
        # carried term: legs 2 @ 7,760 and 1 @ 7,765 settle at 7,770 -> 2 x 10 x 50 + 1 x 5 x 50 = 1,000 + 250 = 1,250.00 = 125,000 c
        self.assertEqual(125000, futures.carried_term_c([(2, Decimal("7760")), (1, Decimal("7765"))], 0, Decimal("7770"), mult, scale))
        # the §19.8 Thursday carried term from legs plus unposted: 2 @ 7,741.50 settle 7,750 -> 850.00 = 85,000 c (+ 35,000 unposted -> 120,000)
        self.assertEqual(120000, futures.carried_term_c([(2, Decimal("7741.50"))], 35000, Decimal("7750"), mult, scale))

    def test_select_print_rules(self):
        d = date(2026, 10, 19)
        settle = epoch_of(d, "16:00")
        lo = epoch_of(date(2026, 10, 18), "18:00")           # the CME session of trade date Mon opens Sun 18:00
        # fetched BEFORE the settle time: a later trade could still print at or before 16:00 -> no print
        self.assertIsNone(futures.select_print(sc.rec(ES, "7759.00", settle - 600, settle - 60, group=GROUP), d, settle, lo))
        # last trade at 16:00, fetched 16:02 -> the print
        obs = futures.select_print(sc.rec(ES, "7760.00", settle, settle + 120, group=GROUP), d, settle, lo)
        self.assertEqual((Decimal("7760"), "observed print at/before the settle time", settle, settle + 120), (obs.price, obs.method, obs.quote_time, obs.fetched_at))
        # trading continued: last 7,762 at 16:03 (after the settle); the tail has bars 15:58 / 15:59 / 16:00 / 16:01 -> the bar ending 16:00 (t = 15:59) wins
        rec = sc.rec(ES, "7762.00", settle + 180, settle + 240, group=GROUP)
        rec.bars_tail = [Bar1m(settle - 120, Decimal("7759.5"), Decimal("7759.5"), Decimal("7759.5"), Decimal("7759.5"), 10),
                         Bar1m(settle - 60, Decimal("7760.25"), Decimal("7760.25"), Decimal("7760.25"), Decimal("7760.25"), 12),
                         Bar1m(settle, Decimal("7761"), Decimal("7761"), Decimal("7761"), Decimal("7761"), 9),
                         Bar1m(settle + 60, Decimal("7762"), Decimal("7762"), Decimal("7762"), Decimal("7762"), 8)]
        obs = futures.select_print(rec, d, settle, lo)
        self.assertEqual((Decimal("7760.25"), "1-minute bar close at/before the settle time", settle), (obs.price, obs.method, obs.quote_time))
        # a stale record from before the session (Fri 16:30) fetched after the settle, no bars -> nothing
        self.assertIsNone(futures.select_print(sc.rec(ES, "7700.00", epoch_of(date(2026, 10, 16), "16:30"), settle + 120, group=GROUP), d, settle, lo))
        # an error record never supplies a print; neither does None
        self.assertIsNone(futures.select_print(sc.rec(ES, "7760.00", settle, settle + 120, group=GROUP, error="HTTP 503"), d, settle, lo))
        self.assertIsNone(futures.select_print(None, d, settle, lo))
        # the daily block: only for exactly that date, only with a close
        self.assertEqual(Decimal("7741.5"), futures.daily_from_payload({"daily": {"close": "7741.50", "date": "2026-10-20"}}, date(2026, 10, 20), 0).price)
        self.assertIsNone(futures.daily_from_payload({"daily": {"close": "7741.50", "date": "2026-10-20"}}, date(2026, 10, 19), 0))
        self.assertIsNone(futures.daily_from_payload({"daily": {"date": "2026-10-20"}}, date(2026, 10, 20), 0))
        self.assertIsNone(futures.daily_from_payload({}, date(2026, 10, 20), 0))

    # ---- §19.8 ----------------------------------------------------------------------------------------------------------
    def test_19_8_im_two_days_vm_close_out(self):
        rt = self.start("2026-10-19T10:00:00-04:00")
        fund(rt, US_EXCHANGE, 10000000)                      # US_EXCHANGE settled 100,000.00; Treasury 9,900,000.00
        self.assertEqual("2026-12-18", rt.instruments.get(ES).expiry)   # 3rd Friday of December 2026
        # Mon 10:00 ET: BUY 2 ESZ26 MARKET, last 7,754.25, bar volume 1,000 -> cap 100 >= 2
        r = self.market(ES, "BUY", 2, "7754.25")
        fill = r.body["fills"][0]
        # notional 2 x 7,754.25 x 50 = 775,425.00; fee 2 x 2.25 = 4.50; no settlement (cash the same day)
        self.assertEqual((77542500, 450, "2026-10-19", None, "7754.25", "last"), (fill["gross_c"], fill["fee_c"], fill["trade_date"], fill["settle_date"], fill["price"], fill["price_basis"]))
        self.assertEqual([], r.body["sweeps"])                 # own cash covered IM + fee
        # Dr 5000 ESZ26 4.50 / Cr 1010 4.50 (fill:{id}); IM 2 x 17,500 = 35,000.00: Dr 1300 ESZ26 / Cr 1010 (im:{id})
        self.assertEqual([("5000", 450, ES), ("1010", -450, None)], self.lines(fill["entry_id"]))
        im_entry = self.entry_by_idem(f"im:{fill['fill_id']}")
        self.assertEqual([("1300", 3500000, ES), ("1010", -3500000, None)], self.lines(im_entry["entry_id"]))
        self.assertEqual((3500000, 3500000), (fill["margin"]["im_delta_c"], fill["margin"]["im_posted_c"]))
        # settled 100,000 - 4.50 - 35,000 = 64,995.50; 1300 = 35,000.00 (L15)
        self.assertEqual((6499550, 3500000), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "1300", inst=ES)))
        lot = sc.one(rt, "SELECT * FROM lots WHERE account_id=? AND instrument_id=?", (US_EXCHANGE, ES))
        self.assertEqual(("LONG", 2, 2, "7754.25", 0), (lot["side"], lot["qty_open"], lot["qty_remaining"], lot["unit_cost"], lot["cost_c"]))
        seed = self.fs_row(ES, "2026-10-19")
        self.assertEqual(("7754.25", 2, 0, "seed", [[2, "7754.25"]], 0), (seed["settle_px"], seed["qty_after"], seed["vm_c"], seed["info"]["kind"], seed["info"]["legs"], seed["info"]["unposted_c"]))
        pos = rt.accounting.position(US_EXCHANGE, ES)
        self.assertEqual((2, 0), (pos.qty, pos.unrealised_c))            # mark 7,754.25 = the reference -> OTE 0
        # NAV(A) = settled 64,995.50 + IM 35,000.00 + OTE 0 = 99,995.50 = 100,000 - 4.50
        self.assertEqual(9999550, rt.accounting.nav(US_EXCHANGE).nav_c)
        assert_integrity(self, rt, "full")
        # Mon 16:05 FUTURES_VM, SETTLE 7,760.00 observed at 16:00: VM = 0 + 2 x (7,760 - 7,754.25) x 50 = 2 x 5.75 x 50 = 575.00
        rt.clock.set("2026-10-19T16:05:00-04:00")
        st = self.settle_quote(ES, "7760.00", date(2026, 10, 19))
        self.assertEqual(epoch_of(date(2026, 10, 19), "16:00"), st)
        n0 = self.n_entries()
        r = self.vm(ES, "2026-10-19")
        self.assertTrue(r.ok, r.body)
        b = r.body
        self.assertEqual(("DONE", 57500, 0, 2, 0, 57500, "7760", "observed print at/before the settle time"),
                         (b["status"], b["vm_c"], b["qty_prev"], b["qty_after"], b["carried_c"], b["fills_c"], b["settle_px"], b["print"]["method"]))
        vm_entry = self.entry_by_idem(f"vm:{US_EXCHANGE}:{ES}:2026-10-19")
        self.assertEqual([("1010", 57500, None), ("4400", -57500, ES)], self.lines(vm_entry["entry_id"]))
        self.assertEqual(("2026-10-19", "VM"), (vm_entry["business_date"], vm_entry["kind"]))
        self.assertEqual(n0 + 1, self.n_entries())
        # settled 64,995.50 + 575.00 = 65,570.50; 4400 natural +575.00; futures_settle(10-19) 7,760.00 (settle, 2 after); marks SETTLE row
        self.assertEqual((6557050, 57500), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "4400", inst=ES)))
        row = self.fs_row(ES, "2026-10-19")
        self.assertEqual(("7760", 2, 57500, "settle", [[2, "7760"]], vm_entry["entry_id"]), (row["settle_px"], row["qty_after"], row["vm_c"], row["info"]["kind"], row["info"]["legs"], row["entry_id"]))
        mark = sc.one(rt, "SELECT * FROM marks WHERE instrument_id=? AND mark_date='2026-10-19' AND kind='SETTLE'", (ES,))
        self.assertEqual(("7760", "close", st), (mark["price"], mark["quality"], mark["quote_time"]))
        self.assertEqual("DONE", sc.one(rt, "SELECT status FROM jobs_done WHERE job='FUTURES_VM' AND key=?", (f"{US_EXCHANGE}:{ES}:2026-10-19",))["status"])
        # rerun with the same request id -> replayed, nothing new; rerun with another id -> already settled, nothing posted
        r2 = self.vm(ES, "2026-10-19")
        self.assertTrue(r2.replayed)
        r3 = self.vm(ES, "2026-10-19", request_id="job:FUTURES_VM:retry-1")
        self.assertTrue(r3.ok, r3.body)
        self.assertEqual((True, False), (r3.body["already"], r3.body["posted"]))
        self.assertEqual(n0 + 1, self.n_entries())
        self.assertEqual(0, sc.one(rt, "SELECT COUNT(*) n FROM attention WHERE resolved_ts IS NULL")["n"])
        assert_integrity(self, rt, "full")
        # Tue 16:05 SETTLE 7,741.50: VM = 2 x (7,741.50 - 7,760.00) x 50 = 2 x (-18.50) x 50 = -1,850.00 -> Dr 4400 / Cr 1010
        rt.clock.set("2026-10-20T16:05:00-04:00")
        self.settle_quote(ES, "7741.50", date(2026, 10, 20))
        r = self.vm(ES, "2026-10-20")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((-185000, -185000, 0, 2, 2), (r.body["vm_c"], r.body["carried_c"], r.body["fills_c"], r.body["qty_prev"], r.body["qty_after"]))
        self.assertEqual(("own", True, None), (r.body["funding"]["source"], r.body["funding"]["funded"], r.body["funding"]["sweep_id"]))
        vm_entry = self.entry_by_idem(f"vm:{US_EXCHANGE}:{ES}:2026-10-20")
        self.assertEqual([("4400", 185000, ES), ("1010", -185000, None)], self.lines(vm_entry["entry_id"]))
        # settled 65,570.50 - 1,850.00 = 63,720.50; 4400 cumulative -1,275.00 = 2 x (7,741.50 - 7,754.25) x 50
        self.assertEqual((6372050, -127500), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "4400", inst=ES)))
        # Tue close mark 7,741.50 -> OTE 0; NAV(A) = 63,720.50 + 35,000.00 = 98,720.50 = 100,000 - 4.50 - 1,275.00
        rt.marks.set(ES, "7741.50")
        self.assertEqual(0, rt.accounting.position(US_EXCHANGE, ES).unrealised_c)
        self.assertEqual(9872050, rt.accounting.nav(US_EXCHANGE).nav_c)
        # Wed intraday mark 7,750.00 -> unrealised 2 x (7,750 - 7,741.50) x 50 = 850.00 (computed, not posted); NAV 99,570.50
        rt.clock.set("2026-10-21T10:00:00-04:00")
        rt.marks.set(ES, "7750.00")
        self.assertEqual(85000, rt.accounting.position(US_EXCHANGE, ES).unrealised_c)
        self.assertEqual(9957050, rt.accounting.nav(US_EXCHANGE).nav_c)
        self.assertEqual(n0 + 2, self.n_entries())
        assert_integrity(self, rt, "full")
        # Thu 10:00 SELL 2 @ 7,745.00: fee 4.50; IM released Dr 1010 / Cr 1300 35,000.00 (L15: flat -> 0); position 0; nothing realised in 4000
        rt.clock.set("2026-10-22T10:00:00-04:00")
        r = self.market(ES, "SELL", 2, "7745.00")
        fill = r.body["fills"][0]
        self.assertEqual((450, -3500000, 0, 2, 0), (fill["fee_c"], fill["margin"]["im_delta_c"], fill["margin"]["im_posted_c"], fill["margin"]["closing"], fill["margin"]["opening"]))
        self.assertEqual([("5000", 450, ES), ("1010", -450, None)], self.lines(fill["entry_id"]))
        im_entry = self.entry_by_idem(f"im:{fill['fill_id']}")
        self.assertEqual([("1010", 3500000, None), ("1300", -3500000, ES)], self.lines(im_entry["entry_id"]))
        # settled 63,720.50 - 4.50 + 35,000.00 = 98,716.00
        self.assertEqual((9871600, 0, 0), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "1300", inst=ES), bal(rt, US_EXCHANGE, "4000", inst=ES)))
        self.assertIsNone(sc.one(rt, "SELECT * FROM v_positions WHERE account_id=? AND instrument_id=?", (US_EXCHANGE, ES)))
        relief = sc.one(rt, "SELECT * FROM lot_relief WHERE close_fill_id=?", (fill["fill_id"],))
        self.assertEqual((2, 0, 0, 0), (relief["qty"], relief["cost_relieved_c"], relief["proceeds_c"], relief["realised_c"]))
        seed = self.fs_row(ES, "2026-10-22")
        # the carried 2 @ 7,741.50 closed at 7,745.00: unposted 2 x 3.50 x 50 = 350.00 until the settle; flat -> the reference stays 7,741.50
        self.assertEqual(("seed", [], 35000, 0, "7741.5"), (seed["info"]["kind"], seed["info"]["legs"], seed["info"]["unposted_c"], seed["qty_after"], seed["settle_px"]))
        assert_integrity(self, rt, "full")
        # Thu 16:05 SETTLE 7,750.00: VM = carried 2 x (7,750 - 7,741.50) x 50 = 850.00 + fills (-2) x (7,750 - 7,745) x 50 = -500.00 = +350.00
        rt.clock.set("2026-10-22T16:05:00-04:00")
        self.settle_quote(ES, "7750.00", date(2026, 10, 22))
        r = self.vm(ES, "2026-10-22")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((35000, 85000, -50000, 2, 0), (r.body["vm_c"], r.body["carried_c"], r.body["fills_c"], r.body["qty_prev"], r.body["qty_after"]))
        self.assertEqual([[2, "7741.5"]], r.body["reference"])
        # 4400 total -925.00 = 2 x (7,745 - 7,754.25) x 50; settled 98,716.00 + 350.00 = 99,066.00 = 100,000 - 9.00 - 925.00; NAV(A) = 99,066.00
        self.assertEqual((9906600, -92500), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "4400", inst=ES)))
        self.assertEqual(9906600, rt.accounting.nav(US_EXCHANGE).nav_c)
        self.assertEqual(900, bal(rt, US_EXCHANGE, "5000", inst=ES))
        row = self.fs_row(ES, "2026-10-22")
        self.assertEqual(("settle", "7750", 0, 35000, []), (row["info"]["kind"], row["settle_px"], row["qty_after"], row["vm_c"], row["info"]["legs"]))
        # nothing is due any more; Treasury untouched by sweeps (9,900,000.00)
        with rt.db.reader() as conn:
            self.assertEqual([], futures.vm_keys(conn, rt.clock.epoch() + 60, rt.sessions, rt.instruments))
        self.assertEqual(990000000, bal(rt, TREASURY, "1010"))
        self.assertEqual(0, sc.one(rt, "SELECT COUNT(*) n FROM transfers WHERE kind='SWEEP_IN'")["n"])
        assert_integrity(self, rt, "full")

    def test_19_8_unfunded_vm_variant(self):
        rt = self.start("2026-10-19T10:00:00-04:00")
        # the account holds exactly IM + fee: 35,000.00 + 4.50 = 35,004.50 -> settled 0 after the fill
        fund(rt, US_EXCHANGE, 3500450)
        self.market(ES, "BUY", 2, "7754.25")
        self.assertEqual((0, 3500000), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "1300", inst=ES)))
        # Mon VM +575.00 -> settled 575.00; the sweep-out returns it (settled 0 "after a sweep-out")
        rt.clock.set("2026-10-19T16:05:00-04:00")
        self.settle_quote(ES, "7760.00", date(2026, 10, 19))
        self.assertTrue(self.vm(ES, "2026-10-19").ok)
        self.assertEqual(57500, bal(rt, US_EXCHANGE, "1010"))
        rt.clock.set("2026-10-19T17:35:00-04:00")
        self.assertTrue(execute(rt, "test.sweep_out", {"account_id": US_EXCHANGE, "amount_c": 57500}, actor="lifecycle").ok)
        self.assertEqual(0, bal(rt, US_EXCHANGE, "1010"))
        # Treasury 10,000,000 - 35,004.50 + 575.00 = 9,965,570.50, emptied by a withdrawal
        self.assertEqual(996557050, bal(rt, TREASURY, "1010"))
        self.assertTrue(execute(rt, "treasury.withdraw", {"lifetime_id": T1, "amount_c": 996557050, "currency": "USD"}).ok)
        self.assertEqual(0, bal(rt, TREASURY, "1010"))
        # Tue VM -1,850.00 cannot be funded (own 0, Treasury 0): still posted, 1010 = -1,850.00, ERROR FUNDING_SHORTFALL, L18 accepts
        rt.clock.set("2026-10-20T16:05:00-04:00")
        self.settle_quote(ES, "7741.50", date(2026, 10, 20))
        r = self.vm(ES, "2026-10-20")
        self.assertTrue(r.ok, r.body)
        f = r.body["funding"]
        self.assertEqual((False, 185000, "overdraft", None, 0), (f["funded"], f["unfunded_c"], f["source"], f["sweep_id"], f["treasury_available_c"]))
        self.assertEqual(-185000, bal(rt, US_EXCHANGE, "1010"))
        self.assertEqual(-185000, r.body["settled_c"])
        self.assertEqual(0, sc.one(rt, "SELECT COUNT(*) n FROM transfers WHERE kind='SWEEP_IN'")["n"])
        att = sc.one(rt, "SELECT * FROM attention WHERE code='FUNDING_SHORTFALL' AND resolved_ts IS NULL")
        self.assertEqual(("ERROR", US_EXCHANGE), (att["severity"], att["account_id"]))
        self.assertIn("UNFUNDED", sc.one(rt, "SELECT note FROM jobs_done WHERE job='FUTURES_VM' AND key=?", (f"{US_EXCHANGE}:{ES}:2026-10-20",))["note"])
        assert_integrity(self, rt, "full")
        # a new order in the account -> 409 FUNDING_SHORTFALL
        now = rt.clock.epoch()
        self.quote(ES, "7741.50", now - 60, now)
        rs = sc.submit(rt, account_id=US_EXCHANGE, instrument_id=ES, side="BUY", qty=1, order_type="LIMIT", limit_px="7700.00")
        self.assertEqual((409, "FUNDING_SHORTFALL"), (rs.http, rs.code))
        # a deposit and a transfer of 2,000.00 -> 1010 = 150.00 and the item resolves AT THE TRANSFER (Build 2 P04: a transfer resolves the
        # lifetime's covered shortfalls; housekeeping then finds nothing left)
        self.assertTrue(deposit(rt, 200000).ok)
        r = fund(rt, US_EXCHANGE, 200000)
        self.assertTrue(r.ok, r.body)
        self.assertEqual(15000, bal(rt, US_EXCHANGE, "1010"))
        self.assertEqual(1, r.body["shortfalls_resolved"])
        self.assertEqual(0, execute(rt, "test.resolve", {"account_id": US_EXCHANGE}).body["resolved"])
        self.assertEqual(0, sc.one(rt, "SELECT COUNT(*) n FROM attention WHERE code='FUNDING_SHORTFALL' AND resolved_ts IS NULL")["n"])
        rs = sc.submit(rt, account_id=US_EXCHANGE, instrument_id=ES, side="BUY", qty=1, order_type="LIMIT", limit_px="7700.00")
        self.assertNotEqual("FUNDING_SHORTFALL", rs.code)
        assert_integrity(self, rt, "full")

    # ---- grains, missing print, daily fallback ----------------------------------------------------------------------------
    def test_grains_multiplier_50_on_cents_quote(self):
        rt = self.start("2026-10-19T10:00:00-04:00")
        fund(rt, US_EXCHANGE, 10000000)
        zc = rt.instruments.get("ZCZ26")
        self.assertEqual((Decimal("50"), "CBOT_GRAINS", 150000), (zc.multiplier, zc.session_group, int(zc.spec["im_c"])))
        # BUY 1 ZCZ26 at 525.25 (cents per bushel as Yahoo quotes it): notional 525.25 x 50 = 26,262.50; fee 2.25; IM 1,500.00
        r = self.market("ZCZ26", "BUY", 1, "525.25", group="CBOT_GRAINS")
        fill = r.body["fills"][0]
        self.assertEqual((2626250, 225, 150000), (fill["gross_c"], fill["fee_c"], fill["margin"]["im_delta_c"]))
        # settled 100,000 - 2.25 - 1,500.00 = 98,497.75
        self.assertEqual(9849775, bal(rt, US_EXCHANGE, "1010"))
        # 14:25 ET, settle time 14:20: print 526.00 -> VM = 1 x (526.00 - 525.25) x 50 = 0.75 x 50 = 37.50
        rt.clock.set("2026-10-19T14:25:00-04:00")
        st = self.settle_quote("ZCZ26", "526.00", date(2026, 10, 19), group="CBOT_GRAINS")
        self.assertEqual(epoch_of(date(2026, 10, 19), "14:20"), st)
        r = self.vm("ZCZ26", "2026-10-19")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((3750, "526"), (r.body["vm_c"], r.body["settle_px"]))
        self.assertEqual(9853525, bal(rt, US_EXCHANGE, "1010"))           # 98,497.75 + 37.50 = 98,535.25
        # a mark of 526.75 after the settle: OTE 1 x 0.75 x 50 = 37.50 against the 526.00 reference
        rt.marks.set("ZCZ26", "526.75")
        self.assertEqual(3750, rt.accounting.position(US_EXCHANGE, "ZCZ26").unrealised_c)
        assert_integrity(self, rt, "full")

    def test_missing_settle_print_is_skipped_then_daily_fallback(self):
        rt = self.start("2026-10-19T10:00:00-04:00")
        fund(rt, US_EXCHANGE, 10000000)
        self.market(ES, "BUY", 2, "7754.25")
        n0 = self.n_entries()
        key = f"{US_EXCHANGE}:{ES}:2026-10-19"
        # 16:05 with only the morning record (fetched 10:00, before the settle time): no print -> SKIPPED, nothing posted, attention WARN, key stays due
        rt.clock.set("2026-10-19T16:05:00-04:00")
        r = self.vm(ES, "2026-10-19")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("SKIPPED", "SETTLE_PRINT_MISSING", True), (r.body["status"], r.body["code"], r.body["retry"]))
        self.assertEqual(n0, self.n_entries())
        self.assertEqual("seed", self.fs_row(ES, "2026-10-19")["info"]["kind"])
        self.assertIsNone(sc.one(rt, "SELECT 1 FROM marks WHERE instrument_id=? AND kind='SETTLE'", (ES,)))
        self.assertEqual("SKIPPED", sc.one(rt, "SELECT status FROM jobs_done WHERE job='FUTURES_VM' AND key=?", (key,))["status"])
        att = sc.one(rt, "SELECT * FROM attention WHERE code='SETTLE_PRINT_MISSING' AND resolved_ts IS NULL")
        self.assertEqual(("WARN", key, US_EXCHANGE), (att["severity"], att["ref_id"], att["account_id"]))
        with rt.db.reader() as conn:
            self.assertEqual([(US_EXCHANGE, ES, "2026-10-19")], futures.vm_keys(conn, rt.clock.epoch(), rt.sessions, rt.instruments))
        # a second skip keeps ONE attention item
        self.assertTrue(self.vm(ES, "2026-10-19", request_id="job:FUTURES_VM:retry-a").ok)
        self.assertEqual(1, sc.one(rt, "SELECT COUNT(*) n FROM attention WHERE code='SETTLE_PRINT_MISSING' AND resolved_ts IS NULL")["n"])
        # the print arrives (16:00 observed, fetched 16:07): the retry (new request id) settles 575.00 and resolves the item
        rt.clock.set("2026-10-19T16:08:00-04:00")
        self.settle_quote(ES, "7760.00", date(2026, 10, 19))
        r = self.vm(ES, "2026-10-19", request_id="job:FUTURES_VM:retry-b")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("DONE", 57500), (r.body["status"], r.body["vm_c"]))
        self.assertEqual(n0 + 1, self.n_entries())
        self.assertEqual(0, sc.one(rt, "SELECT COUNT(*) n FROM attention WHERE code='SETTLE_PRINT_MISSING' AND resolved_ts IS NULL")["n"])
        self.assertEqual("DONE", sc.one(rt, "SELECT status FROM jobs_done WHERE job='FUTURES_VM' AND key=?", (key,))["status"])
        # Tue: the only record has its last trade AFTER the settle (16:03) and no bar tail -> no print; the daily close (fetched by the runner outside the
        # transaction with daily_fallback) supplies 7,741.50 "recorded late": VM = 2 x (7,741.50 - 7,760) x 50 = -1,850.00
        rt.clock.set("2026-10-20T16:05:00-04:00")
        st = rt.sessions.settle_time(GROUP, date(2026, 10, 20))
        self.quote(ES, "7742.00", st + 180, st + 240)
        r = self.vm(ES, "2026-10-20")
        self.assertEqual(("SKIPPED", "SETTLE_PRINT_MISSING"), (r.body["status"], r.body["code"]))
        inst = rt.instruments.get(ES)
        self.assertIsNone(futures.daily_fallback(rt, inst, date(2026, 10, 20)))          # no daily bar set yet -> None, never a number
        rt.fp.set_daily(inst.yahoo_symbol, [("2026-10-19", "7760.00"), ("2026-10-20", "7741.50")])
        daily = futures.daily_fallback(rt, inst, date(2026, 10, 20))
        self.assertEqual(("7741.5", "2026-10-20", "daily_close", rt.sessions.session_close(GROUP, date(2026, 10, 20))), (daily["close"], daily["date"], daily["method"], daily["quote_time"]))
        self.assertIsNone(futures.daily_fallback(rt, inst, date(2026, 10, 21)))
        r = self.vm(ES, "2026-10-20", request_id="job:FUTURES_VM:retry-c", daily=daily)
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("DONE", -185000, "daily_close"), (r.body["status"], r.body["vm_c"], r.body["print"]["method"]))
        self.assertIn("recorded late", r.body["print"]["note"])
        mark = sc.one(rt, "SELECT * FROM marks WHERE instrument_id=? AND mark_date='2026-10-20' AND kind='SETTLE'", (ES,))
        self.assertEqual(("7741.5", "daily_close"), (mark["price"], mark["method"]))
        self.assertEqual(6372050, bal(rt, US_EXCHANGE, "1010"))            # 100,000 - 4.50 - 35,000 + 575 - 1,850 = 63,720.50
        # a daily block of the wrong date is ignored: Wed's job with Tue's close stays SKIPPED
        rt.clock.set("2026-10-21T16:05:00-04:00")
        r = self.vm(ES, "2026-10-21", daily=daily)
        self.assertEqual(("SKIPPED", "SETTLE_PRINT_MISSING"), (r.body["status"], r.body["code"]))
        assert_integrity(self, rt, "full")

    # ---- final settlement, superseded catch-up -------------------------------------------------------------------------------
    def test_final_settlement_on_last_trade_date_and_superseded_catch_up(self):
        rt = self.start("2026-10-19T10:00:00-04:00")
        fund(rt, US_EXCHANGE, 10000000)
        self.market(ES, "BUY", 2, "7754.25")             # settled 64,995.50; IM 35,000.00; no VM ever ran before expiry
        key = f"{US_EXCHANGE}:{ES}"
        # Fri 2026-12-18 (last trade date) 16:05: the final waits for the day's VM
        rt.clock.set("2026-12-18T16:05:00-05:00")
        with rt.db.reader() as conn:
            self.assertEqual([(US_EXCHANGE, ES)], futures.final_keys(conn, rt.clock.epoch(), rt.instruments))
        r = self.final(ES)
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("SKIPPED", "FINAL_PENDING_VM"), (r.body["status"], r.body["code"]))
        self.assertEqual(1, sc.one(rt, "SELECT COUNT(*) n FROM attention WHERE code='FINAL_PENDING_VM' AND resolved_ts IS NULL")["n"])
        # the VM of 12-18 at 7,800.00 catches up from the seed exactly: 2 x (7,800 - 7,754.25) x 50 = 2 x 45.75 x 50 = 4,575.00
        self.settle_quote(ES, "7800.00", date(2026, 12, 18))
        r = self.vm(ES, "2026-12-18")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((457500, 2, [[2, "7754.25"]]), (r.body["vm_c"], r.body["qty_prev"], r.body["reference"]))
        self.assertEqual(6957050, bal(rt, US_EXCHANGE, "1010"))            # 64,995.50 + 4,575.00 = 69,570.50
        # FUTURES_FINAL: SELL 2 @ 7,800 commission-free, trade date 12-18, IM released 35,000.00, no 4000, EXPIRED
        n0 = self.n_entries()
        r = self.final(ES, request_id="job:FUTURES_FINAL:run-2")
        self.assertTrue(r.ok, r.body)
        f = r.body["fill"]
        self.assertEqual(("DONE", "SELL", "SELL", 2, "7800", 0, "2026-12-18", "settle", -3500000, 0),
                         (r.body["status"], f["side"], f["effect"], f["qty"], f["price"], f["fee_c"], f["trade_date"], f["price_basis"], f["margin"]["im_delta_c"], f["position_qty"]))
        frow = sc.one(rt, "SELECT * FROM fills WHERE fill_id=?", (f["fill_id"],))
        self.assertEqual(("FUTURES_FINAL", f"final:{key}", None), (frow["origin"], frow["idem_key"], frow["settle_date"]))
        self.assertEqual([("1010", 3500000, None), ("1300", -3500000, ES)], self.lines(f["entry_id"]))     # the only entry: im:{fill_id}
        self.assertIsNone(self.entry_by_idem(f"fill:{f['fill_id']}"))
        self.assertEqual(n0 + 1, self.n_entries())
        # settled 69,570.50 + 35,000 = 104,570.50 = 100,000 - 4.50 + 4,575.00; NAV(A) the same; flat; 1300 0; 4000 0
        self.assertEqual((10457050, 0, 0), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "1300", inst=ES), bal(rt, US_EXCHANGE, "4000", inst=ES)))
        self.assertEqual(10457050, rt.accounting.nav(US_EXCHANGE).nav_c)
        self.assertIsNone(sc.one(rt, "SELECT * FROM v_positions WHERE account_id=? AND instrument_id=?", (US_EXCHANGE, ES)))
        self.assertEqual("EXPIRED", sc.one(rt, "SELECT status FROM instruments WHERE instrument_id=?", (ES,))["status"])
        self.assertEqual("EXPIRED", rt.instruments.get(ES).status)
        self.assertEqual("DONE", sc.one(rt, "SELECT status FROM jobs_done WHERE job='FUTURES_FINAL' AND key=?", (key,))["status"])
        self.assertEqual(0, sc.one(rt, "SELECT COUNT(*) n FROM attention WHERE code='FINAL_PENDING_VM' AND resolved_ts IS NULL")["n"])
        with rt.db.reader() as conn:
            self.assertEqual([], futures.final_keys(conn, rt.clock.epoch(), rt.instruments))
        # the final again (new request id) -> already; the stale Mon 10-19 key -> DONE superseded, nothing posted
        r = self.final(ES, request_id="job:FUTURES_FINAL:run-3")
        self.assertEqual((True, False), (r.body["already"], r.body["posted"]))
        r = self.vm(ES, "2026-10-19")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("DONE", "VM_SUPERSEDED", False), (r.body["status"], r.body["code"], r.body["posted"]))
        self.assertEqual(n0 + 1, self.n_entries())
        self.assertEqual(1, sc.one(rt, "SELECT COUNT(*) n FROM fills WHERE origin='FUTURES_FINAL'")["n"])
        assert_integrity(self, rt, "full")

    # ---- after-settle fills, shorts and flips ---------------------------------------------------------------------------------
    def test_after_settle_fill_updates_reference_and_next_vm_is_exact(self):
        rt = self.start("2026-10-19T10:00:00-04:00")
        fund(rt, US_EXCHANGE, 10000000)
        self.market(ES, "BUY", 2, "7754.25")
        rt.clock.set("2026-10-19T16:05:00-04:00")
        self.settle_quote(ES, "7760.00", date(2026, 10, 19))
        self.assertTrue(self.vm(ES, "2026-10-19").ok)                       # +575.00 -> settled 65,570.50
        # Mon 16:30 (session open until 17:00, still trade date Mon): BUY 1 @ 7,765.00: fee 2.25, IM +17,500 -> settled 65,570.50 - 2.25 - 17,500 = 48,068.25
        rt.clock.set("2026-10-19T16:30:00-04:00")
        r = self.market(ES, "BUY", 1, "7765.00")
        self.assertEqual((225, 1750000, "2026-10-19"), (r.body["fills"][0]["fee_c"], r.body["fills"][0]["margin"]["im_delta_c"], r.body["fills"][0]["trade_date"]))
        self.assertEqual((4806825, 5250000), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "1300", inst=ES)))
        # the settle row keeps the print and carries the legs 2 @ 7,760 + 1 @ 7,765; R = (2 x 7,760 + 7,765) / 3 = 23,285 / 3 = 7,761.66666667
        row = self.fs_row(ES, "2026-10-19")
        self.assertEqual(("settle", "7760", [[2, "7760"], [1, "7765"]], 0, 3, "7761.66666667", 57500), (row["info"]["kind"], row["info"]["price"], row["info"]["legs"], row["info"]["unposted_c"], row["qty_after"], row["settle_px"], row["vm_c"]))
        # Tue 10:00 mark 7,770: OTE 3 x (7,770 - 7,761.66666667) x 50 = 1,249.9999995 -> 1,250.00 = the exact 2 x 10 x 50 + 1 x 5 x 50
        rt.clock.set("2026-10-20T10:00:00-04:00")
        rt.marks.set(ES, "7770.00")
        self.assertEqual(125000, rt.accounting.position(US_EXCHANGE, ES).unrealised_c)
        # Tue settle 7,770: VM from the legs = 1,000 + 250 = 1,250.00 exactly; settled 48,068.25 + 1,250 = 49,318.25
        rt.clock.set("2026-10-20T16:05:00-04:00")
        self.settle_quote(ES, "7770.00", date(2026, 10, 20))
        r = self.vm(ES, "2026-10-20")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((125000, 3, 3, [[2, "7760"], [1, "7765"]]), (r.body["vm_c"], r.body["qty_prev"], r.body["qty_after"], r.body["reference"]))
        self.assertEqual(4931825, bal(rt, US_EXCHANGE, "1010"))
        assert_integrity(self, rt, "full")

    def test_short_and_flip_fill_path(self):
        rt = self.start("2026-10-19T10:00:00-04:00")
        fund(rt, US_EXCHANGE, 10000000)
        # SELL 1 @ 7,754.25 from flat opens a short: fee 2.25, IM 17,500.00 -> settled 100,000 - 2.25 - 17,500 = 82,497.75; SHORT lot, cost 0
        r = execute(rt, "test.fut_fill", {"account_id": US_EXCHANGE, "instrument_id": ES, "side": "SELL", "qty": 1, "price": "7754.25"})
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("SELL", 225, 1750000, -1, 0, 1), (r.body["effect"], r.body["fee_c"], r.body["margin"]["im_delta_c"], r.body["position_qty"], r.body["margin"]["closing"], r.body["margin"]["opening"]))
        self.assertEqual((8249775, 1750000), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "1300", inst=ES)))
        lot = sc.one(rt, "SELECT * FROM lots WHERE account_id=? AND instrument_id=?", (US_EXCHANGE, ES))
        self.assertEqual(("SHORT", 1, "7754.25", 0), (lot["side"], lot["qty_remaining"], lot["unit_cost"], lot["cost_c"]))
        self.assertEqual([[-1, "7754.25"]], self.fs_row(ES, "2026-10-19")["info"]["legs"])
        # BUY 3 @ 7,750.00: covers the short (unposted (-1) x (7,750 - 7,754.25) x 50 = +212.50) and opens long 2; IM 17,500 x (2 - 1) = +17,500; fee 6.75
        # settled 82,497.75 - 6.75 - 17,500 = 64,991.00; 1300 = 35,000.00 (L15); R = (2 x 7,750 - 21,250 / 5,000) / 2 = 7,747.875
        r = execute(rt, "test.fut_fill", {"account_id": US_EXCHANGE, "instrument_id": ES, "side": "BUY", "qty": 3, "price": "7750.00"})
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("BUY", 675, 1750000, 2, 1, 2), (r.body["effect"], r.body["fee_c"], r.body["margin"]["im_delta_c"], r.body["position_qty"], r.body["margin"]["closing"], r.body["margin"]["opening"]))
        self.assertEqual((6499100, 3500000, 0), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "1300", inst=ES), bal(rt, US_EXCHANGE, "4000", inst=ES)))
        lots = sc.q(rt, "SELECT side, qty_remaining, unit_cost FROM lots WHERE account_id=? AND instrument_id=? ORDER BY lot_id", (US_EXCHANGE, ES))
        self.assertEqual([("SHORT", 0, "7754.25"), ("LONG", 2, "7750")], [(l["side"], l["qty_remaining"], l["unit_cost"]) for l in lots])
        row = self.fs_row(ES, "2026-10-19")
        self.assertEqual(([[2, "7750"]], 21250, "7747.875", 2), (row["info"]["legs"], row["info"]["unposted_c"], row["settle_px"], row["qty_after"]))
        rt.marks.set(ES, "7750.00")
        self.assertEqual(21250, rt.accounting.position(US_EXCHANGE, ES).unrealised_c)          # 2 x (7,750 - 7,747.875) x 50 = 212.50
        assert_integrity(self, rt, "full")
        # settle 7,748.00: VM = fills only: (-1) x (7,748 - 7,754.25) x 50 = +312.50; 3 x (7,748 - 7,750) x 50 = -300.00 -> +12.50
        rt.clock.set("2026-10-19T16:05:00-04:00")
        self.settle_quote(ES, "7748.00", date(2026, 10, 19))
        r = self.vm(ES, "2026-10-19")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((1250, 0, 1250, 0, 2), (r.body["vm_c"], r.body["carried_c"], r.body["fills_c"], r.body["qty_prev"], r.body["qty_after"]))
        self.assertEqual((6500350, 1250), (bal(rt, US_EXCHANGE, "1010"), bal(rt, US_EXCHANGE, "4400", inst=ES)))      # 64,991.00 + 12.50
        assert_integrity(self, rt, "full")

    # ---- lifecycle helpers ---------------------------------------------------------------------------------------------------
    def test_vm_keys_due_and_roll_listings(self):
        rt = self.start("2026-10-19T10:00:00-04:00")
        fund(rt, US_EXCHANGE, 10000000)
        self.market(ES, "BUY", 2, "7754.25")

        def keys(at):
            rt.clock.set(at)
            with rt.db.reader() as conn:
                return futures.vm_keys(conn, rt.clock.epoch(), rt.sessions, rt.instruments)

        k = (US_EXCHANGE, ES)
        self.assertEqual([], keys("2026-10-19T16:04:00-04:00"))                                   # settle + 5 min not reached
        self.assertEqual([k + ("2026-10-19",)], keys("2026-10-19T16:05:00-04:00"))
        self.settle_quote(ES, "7760.00", date(2026, 10, 19))
        self.assertTrue(self.vm(ES, "2026-10-19").ok)
        self.assertEqual([], keys("2026-10-19T16:06:00-04:00"))
        self.assertEqual([k + ("2026-10-20",)], keys("2026-10-20T16:05:00-04:00"))
        self.assertEqual([k + ("2026-10-20",), k + ("2026-10-21",)], keys("2026-10-21T16:05:00-04:00"))
        # Sunday 10-25: Tue, Wed, Thu, Fri are due (no weekend keys)
        self.assertEqual([k + (d,) for d in ("2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23")], keys("2026-10-25T12:00:00-04:00"))
        # HOUSEKEEPING roll on Mon 2026-12-21 (after ESZ26's last trade 12-18): ESZ26 EXPIRED, a new December 2027 month listed
        rt.clock.set("2026-12-21T00:15:00-05:00")
        self.assertIsNone(rt.instruments.get("ESZ27"))
        r = execute(rt, "test.roll", {}, actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertIn(ES, r.body["expired"])
        self.assertGreaterEqual(r.body["listed"], 1)
        self.assertEqual("EXPIRED", rt.instruments.get(ES).status)
        self.assertEqual(("FUTURE", "2027-12-17", "ACTIVE"), (rt.instruments.get("ESZ27").kind, rt.instruments.get("ESZ27").expiry, rt.instruments.get("ESZ27").status))


if __name__ == "__main__":
    unittest.main()
