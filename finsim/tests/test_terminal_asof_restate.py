"""P3 (FINSIM1_PLAN C9, PORTFOLIO-01 / -08 / -14): the as-of NAV engine, the restated snapshot versions and their split, the
latest-version readers and seam (C9i), business-date positions, and the tier-1 accounting hooks C9a / C9b / C9c / C9g / C9h.

Every money figure is worked by hand in the comments (section 3 rule 3).  The as-of rows are booked by test handlers that post
with an explicit business date (what C8 / C15 do for the import), so these tests stand without P2 / P6 in the tree."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import (T1, TREASURY, US_EXCHANGE, buy, execute, fund, make_runtime, set_mark, snapshot)
from finsim.terminal import accounting as acc, navhistory
from finsim.terminal.clock import epoch_of
from finsim.terminal.db import next_id
from finsim.terminal.ledger import CHART, Entry, cr, dr, open_lot

D1, D2, D3, D4 = "2026-10-15", "2026-10-16", "2026-10-17", "2026-10-18"     # Thu, Fri, Sat, Sun
BATCH = "T-ASOF-1"
PB = "T1:PB_GS"


# ---- test-only as-of handlers (explicit business dates, as C8 / C15 book them) --------------------------------------------------
def _xfer(ctx, kind, src, dst, amount_c, bd, linked_type=None, linked_id=None, shortfall_c=None):
    tid = next_id(ctx.conn, "transfer")
    idem = f"xfer:{tid}"
    eid = ctx.post(Entry(kind, f"as-of {kind.lower()} {amount_c / 100:.2f} {src} -> {dst}",
                         [dr(src, "3100", "USD", amount_c), cr(src, "1010", "USD", amount_c), dr(dst, "1010", "USD", amount_c), cr(dst, "3100", "USD", amount_c)],
                         idem, ref_type="transfer", ref_id=tid, business_date=bd))
    ctx.conn.execute("INSERT INTO transfers(transfer_id, command_id, ts, business_date, kind, from_account, to_account, currency, amount_c, linked_type, linked_id,"
                     " shortfall_c, entry_id, idem_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (tid, ctx.command_id, ctx.ts, bd, kind, src, dst, "USD", amount_c, linked_type, linked_id, shortfall_c, eid, idem))
    return tid


def h_asof_buy(ctx, p):
    """An as-of listed BUY: the fill dated ``trade_date`` (entry Dr 1100 gross / Dr 5000 fee / Cr 2100 gross + fee), a Treasury sweep-in of
    gross + fee dated the trade date (linked to the fill), and its settlement dated ``settle_date`` (Dr 2100 / Cr 1010)."""
    aid, iid, qty, px, fee = p["account_id"], p["instrument_id"], int(p["qty"]), Decimal(p["price"]), int(p["fee_c"])
    inst = ctx.services.instruments.get(iid, ctx.conn)
    gross = inst.contract_value_c(px, qty)
    fid = next_id(ctx.conn, "fill")
    td, sd = p["trade_date"], p["settle_date"]
    eid = ctx.post(Entry("FILL", f"as-of BUY {qty} {iid} @ {px} (batch {p['batch_id']})",
                         [dr(aid, "1100", "USD", gross, instrument_id=iid), dr(aid, "5000", "USD", fee, instrument_id=iid), cr(aid, "2100", "USD", gross + fee)],
                         f"fill:{fid}", ref_type="fill", ref_id=fid, business_date=td))
    ctx.conn.execute("INSERT INTO fills(fill_id, order_id, account_id, instrument_id, side, qty, price, gross_c, fee_c, currency, fx_rate, ts, trade_date, settle_date,"
                     " origin, price_basis, quote_json, entry_id, idem_key) VALUES (?,?,?,?,'BUY',?,?,?,?,'USD','1',?,?,?,'ORDER','asof','{}',?,?)",
                     (fid, None, aid, iid, qty, str(px), gross, fee, int(p["exec_ts"]), td, sd, eid, f"test:{fid}"))
    open_lot(ctx.tx, aid, iid, "LONG", fid, qty, px, gross, "USD")
    sid = next_id(ctx.conn, "settlement")
    ctx.conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status) VALUES (?,?,?,?,?,?,?,?,?,'PENDING')",
                     (sid, fid, None, aid, "CASH", "USD", -(gross + fee), "2100", sd))
    tid = _xfer(ctx, "SWEEP_IN", f"{T1}:TREASURY", aid, gross + fee, td, "fill", fid, gross + fee)
    seid = ctx.post(Entry("SETTLE", f"settlement {sid} of fill {fid}", [dr(aid, "2100", "USD", gross + fee), cr(aid, "1010", "USD", gross + fee)],
                          f"settle:{sid}", ref_type="settlement", ref_id=sid, business_date=sd))
    ctx.conn.execute("UPDATE settlements SET status='SETTLED', settled_ts=?, entry_id=? WHERE settle_id=?", (ctx.ts, seid, sid))
    ctx.touched_instruments.add((aid, iid))
    return {"fill_id": fid, "settle_id": sid, "transfer_id": tid, "gross_c": gross}


def h_asof_transfer(ctx, p):
    return {"transfer_id": _xfer(ctx, p["kind"], p["from_account"], p["to_account"], int(p["amount_c"]), p["business_date"])}


def _close(rt, iid, d, px):
    with rt.db.writer() as tx:
        tx.conn.execute("INSERT OR REPLACE INTO marks(instrument_id, mark_date, kind, price, currency, quality, source, method, quote_time, fetched_at, note)"
                        " VALUES (?,?,'CLOSE',?,'USD','close','test official close','direct',?,?,NULL)", (iid, d, str(px), epoch_of(date.fromisoformat(d), "16:00"),
                                                                                               epoch_of(date.fromisoformat(d), "16:20")))


def _snap_all(rt, d):
    with rt.db.reader() as conn:
        accts = [r[0] for r in conn.execute("SELECT account_id FROM accounts WHERE lifetime_id='T1' ORDER BY sort_order, account_id").fetchall()]
    for a in accts:
        snapshot(rt, a, date.fromisoformat(d))
    snapshot(rt, "T1:ALL", date.fromisoformat(d))


def _restate(rt, groups, through=D3, request_id="imp:restate-0001", batch=BATCH):
    return execute(rt, "nav.restate", {"lifetime_id": T1, "from_date": D1, "through_date": through, "reason": "as-of booking (test)", "batch_id": batch,
                                       "groups": groups}, request_id, actor="import")


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-asof-")
        self.rt = make_runtime(self.tmp, at=f"{D1}T09:00:00-04:00")
        navhistory.bind(self.rt)
        navhistory.register(self.rt.bus, self.rt)
        self.rt.bus.register("test.asof_buy", h_asof_buy)
        self.rt.bus.register("test.asof_transfer", h_asof_transfer)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class RestatementSplit(_Base):
    """P3 acceptance 1 (a): restatement with its split, per account and for the lifetime, idempotent; (g) the seam."""

    def _scenario(self):
        rt = self.rt
        # D1 Thu 10:00: fund US Exchange 1,000,000.00 from Treasury; BUY 200 AAPL @ 20.00, fee 0.00 (cost 4,000.00, payable until D2)
        rt.clock.set(f"{D1}T10:00:00-04:00")
        self.assertTrue(fund(rt, US_EXCHANGE, 100000000).ok)
        set_mark(rt, "AAPL", "20.00")
        set_mark(rt, "MSFT", "10.00")
        self.assertTrue(buy(rt, US_EXCHANGE, "AAPL", 200, "20.00", fee_c=0).ok)
        rt.clock.set(f"{D1}T17:45:00-04:00")
        _snap_all(rt, D1)                                         # originals at the live marks: AAPL 20.00 -> NAV unchanged
        # account B (PB) opened at 19:44 ET on D1 - after D1's snapshot
        rt.clock.set(f"{D1}T19:44:00-04:00")
        with rt.db.writer() as tx:
            tx.conn.execute("INSERT INTO accounts(account_id, book_code, lifetime_id, role, name, kind, broker, opened_ts, sort_order) VALUES (?,?,?,?,?,?,?,?,?)",
                            (PB, "T", T1, "PB_GS", "Goldman (PB)", "PB", "GS", rt.clock.epoch(), 90))
        for d in (D2, D3):
            rt.clock.set(f"{d}T17:45:00-04:00")
            _snap_all(rt, d)                                      # AAPL still 20.00 live at both evening snapshots
        # dated marks: AAPL CLOSE D1 20.00, D2 20.02 (Sat carries Fri); MSFT CLOSE D1 10.50, D2 11.00
        _close(rt, "AAPL", D1, "20.00")
        _close(rt, "AAPL", D2, "20.02")
        _close(rt, "MSFT", D1, "10.50")
        _close(rt, "MSFT", D2, "11.00")
        # D4 Sun 10:00: the batch - an as-of BUY 100 MSFT @ 10.00 fee 1.00 for D1 (funded by its own as-of sweep, settled D2), an as-of transfer
        # Treasury -> B 10,000.00 dated D1, and a separate as-of sweep returning 500.00 of US Exchange's cash to Treasury dated D2
        rt.clock.set(f"{D4}T10:00:00-04:00")
        r = execute(rt, "test.asof_buy", {"batch_id": BATCH, "account_id": US_EXCHANGE, "instrument_id": "MSFT", "qty": 100, "price": "10.00", "fee_c": 100,
                                          "trade_date": D1, "settle_date": D2, "exec_ts": epoch_of(date.fromisoformat(D1), "16:00")}, actor="import")
        self.assertTrue(r.ok, r.body)
        self.buy = r.body
        r = execute(rt, "test.asof_transfer", {"batch_id": BATCH, "kind": "TRANSFER", "from_account": TREASURY, "to_account": PB, "amount_c": 1000000,
                                               "business_date": D1}, actor="import")
        self.assertTrue(r.ok, r.body)
        self.t_pb = r.body["transfer_id"]
        r = execute(rt, "test.asof_transfer", {"batch_id": BATCH, "kind": "SWEEP_OUT", "from_account": US_EXCHANGE, "to_account": TREASURY, "amount_c": 50000,
                                               "business_date": D2}, actor="import")
        self.assertTrue(r.ok, r.body)
        self.t_fund = r.body["transfer_id"]
        rt.clock.set(f"{D4}T12:00:00-04:00")
        return [{"key": "line-MSFT", "label": "MSFT 100 @ 10.00", "refs": {"fill_ids": [self.buy["fill_id"]]}},
                {"key": "line-PB", "label": "transfer to B", "refs": {"transfer_ids": [self.t_pb]}},
                {"key": "funding", "label": "as-of sweeps", "refs": {"transfer_ids": [self.t_fund]}}]

    def _versions(self):
        with self.rt.db.reader() as conn:
            return {(r["account_id"], r["snap_date"]): dict(r) for r in conn.execute("SELECT * FROM nav_snapshot_versions ORDER BY account_id, snap_date, version").fetchall()}

    def test_split_per_account_and_lifetime(self):
        rt = self.rt
        groups = self._scenario()
        with rt.db.reader() as conn:
            originals = [dict(r) for r in conn.execute("SELECT * FROM nav_snapshots ORDER BY account_id, snap_date").fetchall()]
        res = _restate(rt, groups)
        self.assertTrue(res.ok, res.body)
        v = self._versions()
        eff = lambda key, d: {e["key"]: e["amount_c"] for e in json.loads(v[(key, d)]["import_effect_json"])}   # noqa: E731
        # lifetime: D1 import MSFT 100 x 10.50 - 1,001.00 = +49.00 (the sweep is internal); mark policy 0.00 (AAPL close 20.00 = the original's mark)
        self.assertEqual({"line-MSFT": 4900, "line-PB": 0, "funding": 0}, eff("T1:ALL", D1))
        self.assertEqual((1_000_000_000, 0, 1_000_004_900), (v[("T1:ALL", D1)]["original_nav_c"], v[("T1:ALL", D1)]["mark_policy_c"], v[("T1:ALL", D1)]["nav_c"]))
        # D2: import 100 x 11.00 - 1,001.00 = +99.00; mark policy 200 x (20.02 - 20.00) = +4.00; restated = original + 99.00 + 4.00
        self.assertEqual({"line-MSFT": 9900, "line-PB": 0, "funding": 0}, eff("T1:ALL", D2))
        self.assertEqual((400, 1_000_000_000 + 9900 + 400), (v[("T1:ALL", D2)]["mark_policy_c"], v[("T1:ALL", D2)]["nav_c"]))
        mp = json.loads(v[("T1:ALL", D2)]["mark_policy_json"])
        self.assertEqual([("AAPL", Decimal("20.00"), Decimal("20.02"), 400)], [(m["instrument_id"], Decimal(m["original_mark"]), Decimal(m["dated_mark"]), m["amount_c"])
                                                                              for m in mp])
        # D3 (Saturday): Friday's closes carried -> import +99.00, mark policy +4.00, pnl_day 0.00
        self.assertEqual({"line-MSFT": 9900, "line-PB": 0, "funding": 0}, eff("T1:ALL", D3))
        self.assertEqual((400, 1_000_010_300, 0), (v[("T1:ALL", D3)]["mark_policy_c"], v[("T1:ALL", D3)]["nav_c"], v[("T1:ALL", D3)]["pnl_day_c"]))
        # US Exchange: D1 MSFT 1,050.00 - payable 1,001.00 + its own sweep-in 1,001.00 = +1,050.00 -> 1,000,000.00 + 1,050.00
        self.assertEqual(({"line-MSFT": 105000, "line-PB": 0, "funding": 0}, 100_105_000), (eff(US_EXCHANGE, D1), v[(US_EXCHANGE, D1)]["nav_c"]))
        # D2: MSFT 1,100.00 (the settlement paid 1,001.00 from the swept cash) -> +1,100.00; funding -500.00; mark policy +4.00 -> 1,000,604.00
        self.assertEqual({"line-MSFT": 110000, "line-PB": 0, "funding": -50000}, eff(US_EXCHANGE, D2))
        self.assertEqual((400, 100_060_400), (v[(US_EXCHANGE, D2)]["mark_policy_c"], v[(US_EXCHANGE, D2)]["nav_c"]))
        # Treasury: D1 -1,001.00 (the line's sweep) - 10,000.00 (to B) -> 8,988,999.00; D2 + 500.00 (funding) -> 8,989,499.00
        self.assertEqual(({"line-MSFT": -100100, "line-PB": -1000000, "funding": 0}, 898_899_900), (eff(TREASURY, D1), v[(TREASURY, D1)]["nav_c"]))
        self.assertEqual(({"line-MSFT": -100100, "line-PB": -1000000, "funding": 50000}, 898_949_900), (eff(TREASURY, D2), v[(TREASURY, D2)]["nav_c"]))
        # B opened after D1's snapshot: a D1 version row with original 0 ("added by restatement"); D2 / D3 had an original 0.00 row
        self.assertEqual((0, 1, 1000000), (v[(PB, D1)]["original_nav_c"], v[(PB, D1)]["added"], v[(PB, D1)]["nav_c"]))
        self.assertEqual((0, 0, 1000000), (v[(PB, D2)]["original_nav_c"], v[(PB, D2)]["added"], v[(PB, D2)]["nav_c"]))
        # the funding group nets to 0 for the lifetime: US Exchange -500.00 + Treasury +500.00
        self.assertEqual(0, eff(US_EXCHANGE, D2)["funding"] + eff(TREASURY, D2)["funding"])
        # residual 0 on every row; the lifetime row = the sum of its accounts
        self.assertEqual([], [(k, r["residual_c"]) for k, r in v.items() if r["residual_c"] != 0])
        for d in (D1, D2, D3):
            self.assertEqual(v[("T1:ALL", d)]["nav_c"], sum(r["nav_c"] for (k, dd), r in v.items() if dd == d and k != "T1:ALL"))
        # the originals are unchanged
        with rt.db.reader() as conn:
            self.assertEqual(originals, [dict(r) for r in conn.execute("SELECT * FROM nav_snapshots ORDER BY account_id, snap_date").fetchall()])
        # the TODAY baseline on D4 is the restated D3, labelled
        p = rt.accounting.pnl("T1:ALL", "TODAY")
        self.assertEqual((D3, 1_000_010_300), (p.baseline["snap_date"], p.baseline["nav_c"]))
        self.assertEqual(f"restated on {D4} (batch {BATCH})", p.baseline["restated"]["text"])
        # running the same restatement again writes no new version
        res2 = _restate(rt, groups, request_id="imp:restate-0002")
        self.assertTrue(res2.ok, res2.body)
        self.assertEqual((0, res.body["written"]), (res2.body["written"], res2.body["unchanged"]))
        self.assertEqual(len(v), len(self._versions()))

    def test_seam_and_m4_on_the_latest_chain(self):
        """(g): after restating D1..D3, the D4 snapshot's pnl_day = NAV D4 - restated NAV D3 - flows and M4 raises nothing; on the
        original chain alone the step D3 -> D4 would not tie by +103.00."""
        rt = self.rt
        groups = self._scenario()
        self.assertTrue(_restate(rt, groups).ok)
        set_mark(rt, "AAPL", "20.02")
        set_mark(rt, "MSFT", "11.00")
        rt.clock.set(f"{D4}T17:45:00-04:00")
        row = snapshot(rt, "T1:ALL", date.fromisoformat(D4))
        # NAV D4 = 10,000,000.00 + MSFT 1,100.00 - 1,001.00 + AAPL 200 x 0.02 = 10,000,103.00 = restated D3 -> pnl_day 0.00, flows 0.00
        self.assertEqual((1_000_010_300, 0, 0), (row["nav_c"], row["pnl_day_c"], row["flows_day_c"]))
        with rt.db.reader() as conn:
            orig_d3 = conn.execute("SELECT nav_c FROM nav_snapshots WHERE account_id='T1:ALL' AND snap_date=?", (D3,)).fetchone()[0]
        self.assertEqual(10300, row["nav_c"] - orig_d3 - row["flows_day_c"] - row["pnl_day_c"])     # the original chain would break by 103.00
        rep = rt.accounting.integrity("full", lifetime_id=T1)
        self.assertEqual([], [p.to_dict() for p in rep.problems if p.id in ("M4", "M5")])

    def test_unassigned_batch_entry_refuses(self):
        """An entry the batch posted that no group names leaves a residual: 409 RESTATE_RESIDUAL and nothing is written."""
        groups = self._scenario()
        res = _restate(self.rt, [g for g in groups if g["key"] != "line-PB"])
        self.assertEqual((409, "RESTATE_RESIDUAL"), (res.http, res.code))
        self.assertEqual({}, self._versions())

    def test_ui_may_not_restate(self):
        res = execute(self.rt, "nav.restate", {"lifetime_id": T1, "from_date": D1, "through_date": D1, "reason": "x", "batch_id": "B", "groups": []})
        self.assertEqual((403, "RESTATE_FORBIDDEN"), (res.http, res.code))

    def test_open_day_is_refused(self):
        res = execute(self.rt, "nav.restate", {"lifetime_id": T1, "from_date": D1, "through_date": D1, "reason": "x", "batch_id": "B", "groups": []}, actor="import")
        self.assertEqual((409, "RESTATE_RANGE"), (res.http, res.code))          # the clock is on D1: its close is not final


class BusinessDatePositions(_Base):
    def test_fill_after_the_cutoff_counts_from_the_next_business_date(self):
        """(h): a fill at 19:58 ET on D carries business date D+1 (the 17:00 ET accounting-day cut-off): it counts in nav_as_of(D+1) and
        not in nav_as_of(D)."""
        rt = self.rt
        rt.clock.set(f"{D1}T10:00:00-04:00")
        self.assertTrue(fund(rt, US_EXCHANGE, 10000000).ok)                  # 100,000.00
        rt.clock.set(f"{D1}T19:58:00-04:00")
        set_mark(rt, "AAPL", "20.00")
        r = buy(rt, US_EXCHANGE, "AAPL", 100, "20.00", fee_c=100)            # Dr 1100 2,000.00 / Dr 5000 1.00 / Cr 2100 2,001.00
        self.assertTrue(r.ok, r.body)
        with rt.db.reader() as conn:
            bd = conn.execute("SELECT business_date FROM journal_entries WHERE entry_id=?", (r.body["entry_id"],)).fetchone()[0]
        self.assertEqual(D2, bd)
        _close(rt, "AAPL", D1, "20.00")
        _close(rt, "AAPL", D2, "21.00")
        a = rt.accounting.nav_as_of(US_EXCHANGE, date.fromisoformat(D1))
        b = rt.accounting.nav_as_of(US_EXCHANGE, date.fromisoformat(D2))
        self.assertEqual(({}, 10000000), (json.loads(a["detail_json"])["positions"], a["nav_c"]))
        # as of D2: cash 100,000.00 - payable 2,001.00 + 100 x 21.00 = 100,099.00
        self.assertEqual(10009900, b["nav_c"])
        pos = json.loads(b["detail_json"])["positions"]["AAPL"]
        self.assertEqual((100, "21.00", "CLOSE", D2), (pos["qty"], pos["mark"], pos["mark_kind"], pos["mark_date"]))


class DatedMarkRule(_Base):
    def test_weekend_carries_friday_a_missing_trading_day_close_falls_back(self):
        """PORTFOLIO-14: Saturday D3 carries Friday's recorded close; Monday 10-19 (a trading day) whose close was never recorded does not:
        the evening snapshot's mark set falls back to the live mark, labelled stale, never Friday's close passed off as Monday's."""
        rt = self.rt
        _close(rt, "AAPL", D2, "20.02")
        set_mark(rt, "AAPL", "21.00")
        snap = rt.marks.snapshot()
        with rt.db.reader() as conn:
            inst = rt.instruments.get("AAPL")
            sat = acc.dated_mark_view(conn, inst, D3, rt.sessions)
            mon = acc.dated_mark_view(conn, inst, "2026-10-19", rt.sessions)
            dm = acc.DatedMarks(conn, rt.instruments, date(2026, 10, 19), snap, rt.sessions).mark("AAPL")
        self.assertEqual((Decimal("20.02"), "close", D2), (sat.mark, sat.quality, sat.inputs["mark_date"]))
        self.assertIsNone(mon)
        self.assertEqual((Decimal("21.00"), "stale"), (dm.mark, dm.quality))
        self.assertIn("no CLOSE recorded for 2026-10-19", dm.note)


class AccountingHooks(_Base):
    def test_reserved_for_order_is_the_per_row_body(self):
        """C9a: LIMIT BUY 100 @ 300.00 -> 30,000.00 + fee 1.00 = 30,001.00; MARKET BUY 100 at last 339.50 x 1.02 -> 34,629.00 + 1.00 = 34,630.00."""
        rt = self.rt
        self.assertTrue(fund(rt, US_EXCHANGE, 100000000).ok)
        set_mark(rt, "AAPL", "339.50")
        now = rt.clock.epoch()
        with rt.db.writer() as tx:
            common = (US_EXCHANGE, "AAPL", 100, "DAY", "WORKING", "US_EQUITY", now, now, now + 3600, "{}")
            tx.x("INSERT INTO orders(order_id, request_id, account_id, instrument_id, side, qty, order_type, limit_px, tif, status, session_group, created_ts, updated_ts,"
                 " expires_ts, preview_json) VALUES ('O-000001','ui:o1',?,?,'BUY',?,'LIMIT','300.00',?,?,?,?,?,?,?)", common)
            tx.x("INSERT INTO orders(order_id, request_id, account_id, instrument_id, side, qty, order_type, tif, status, session_group, created_ts, updated_ts,"
                 " expires_ts, preview_json) VALUES ('O-000002','ui:o2',?,?,'BUY',?,'MARKET',?,?,?,?,?,?,?)", common)
        snap = rt.marks.snapshot()
        with rt.db.reader() as conn:
            rows = conn.execute("SELECT * FROM orders ORDER BY order_id").fetchall()
            per = [acc.reserved_for_order(conn, r, snap, rt.instruments) for r in rows]
            total = acc.reserved_c(conn, US_EXCHANGE, "USD", snap, rt.instruments)
        self.assertEqual([3000100, 3463000], per)
        self.assertEqual(sum(per), total)

    def test_cash_adjuster_restricts_available_spendable_immediate_and_buying_power(self):
        """C9b: an adjuster restricting 250,000.00 of US Exchange's 1,000,000.00 -> available 750,000.00, immediate 750,000.00, buying power
        750,000.00 + Treasury 9,000,000.00; listed in CashView.adjustments; the total for SWEEP_OUT."""
        rt = self.rt
        self.assertTrue(fund(rt, US_EXCHANGE, 100000000).ok)
        acc.add_cash_adjuster("restricted_test", "restricted (test)", lambda conn, aid, ccy, snap, master: 25000000 if aid == US_EXCHANGE and ccy == "USD" else 0)
        try:
            c = rt.accounting.cash(US_EXCHANGE)["USD"]
            self.assertEqual((100000000, 75000000, 25000000), (c.settled_c, c.available_c, c.adjustments_total_c))
            self.assertEqual([{"key": "restricted_test", "label": "restricted (test)", "amount_c": 25000000}], [a for a in c.adjustments if a["key"] == "restricted_test"])
            snap = rt.marks.snapshot()
            with rt.db.reader() as conn:
                imm = acc.immediate_available_c(conn, US_EXCHANGE, "USD", None, snap, rt.instruments, rt.clock.now())
                own = acc.immediate_available_c(conn, US_EXCHANGE, "USD", None, snap, rt.instruments, rt.clock.now(), skip_adjusters=("restricted_test",))
                total = acc.adjustments_c(conn, US_EXCHANGE, "USD", snap, rt.instruments)
                tre = acc.adjustments_c(conn, TREASURY, "USD", snap, rt.instruments)
            self.assertEqual((75000000, 100000000, 25000000, 0), (imm, own, total, tre))
            bp = rt.accounting.buying_power(US_EXCHANGE, "USD")
            self.assertEqual((75000000, 900000000, 975000000), (bp.available_c, bp.treasury_available_c, bp.total_c))
            av = rt.accounting.available_view(US_EXCHANGE, "USD")
            self.assertEqual((100000000, 25000000, 75000000), (av.settled_c, av.adjustments_total_c, av.available_c))
        finally:
            acc.remove_cash_adjuster("restricted_test")
        self.assertEqual(100000000, rt.accounting.cash(US_EXCHANGE)["USD"].available_c)

    def test_cash_view_at_reads_business_dates(self):
        """C9h: a transfer entered at 19:41 ET on D1 carries business date D2: Treasury reads 10,000,000.00 at the close of D1 and
        9,000,000.00 at the close of D2, while an as-of transfer dated D1 counts on D1."""
        rt = self.rt
        rt.clock.set(f"{D1}T19:41:00-04:00")
        self.assertTrue(fund(rt, US_EXCHANGE, 100000000).ok)                      # 1,000,000.00, business date D2
        rt.clock.set(f"{D2}T10:00:00-04:00")
        self.assertTrue(execute(rt, "test.asof_transfer", {"batch_id": "X", "kind": "TRANSFER", "from_account": TREASURY, "to_account": US_EXCHANGE,
                                                           "amount_c": 5000, "business_date": D1}).ok)
        with rt.db.reader() as conn:
            t1 = acc.cash_view_at(conn, TREASURY, "USD", D1)
            t2 = acc.cash_view_at(conn, TREASURY, "USD", date.fromisoformat(D2))
            u1 = acc.cash_view_at(conn, US_EXCHANGE, "USD", D1)
        self.assertEqual({"settled_c": 1_000_000_000 - 5000, "receivable_c": 0, "payable_c": 0}, t1)
        self.assertEqual(1_000_000_000 - 5000 - 100000000, t2["settled_c"])
        self.assertEqual(5000, u1["settled_c"])

    def test_otc_cost_basis_and_pl_buckets(self):
        """C9c / PORTFOLIO-08: a CDX upfront received 48,492.57 (GL 2345) -> cost -48,492.57; mark -49,176.13 -> unrealised -683.56.  4451 / 4453
        are financing (a debit is a cost), 4452 income; 1335 / 2345 stay out of the NAV asset / liability sets."""
        gl = {"2345": (4849257, 4849257)}                                         # natural (credit-positive) balance of the liability
        self.assertEqual(-4849257, acc.otc_cost_c(gl))
        self.assertEqual(-68356, -4917613 - acc.otc_cost_c(gl))
        self.assertEqual(("financing", "financing", "income", "realised"), tuple(acc.pl_bucket(g) for g in ("4451", "4453", "4452", "4450")))
        self.assertTrue(set(acc.OTC_COST_GLS).isdisjoint(acc.NAV_ASSET_GLS + acc.NAV_LIAB_GLS))
        if "4451" in CHART:                         # P4's ledger: a 4451 DEBIT of 455.31 is a financing cost of +455.31, a 4453 CREDIT of 25.83 one of -25.83
            self.assertEqual(45531, acc.pl_amount("4451", acc.natural("4451", 45531)))
            self.assertEqual(-2583, acc.pl_amount("4453", acc.natural("4453", -2583)))
            self.assertEqual(1000, acc.pl_amount("4452", acc.natural("4452", -1000)))       # dividend equivalents credited: income +10.00

    def test_failed_settlement_is_not_an_integrity_failure(self):
        """C9g: a PENDING settlement more than 5 business days overdue raises L21 - unless its fail_state is FAILED (shown as FAILED)."""
        rt = self.rt
        rt.clock.set(f"{D1}T10:00:00-04:00")
        self.assertTrue(fund(rt, US_EXCHANGE, 100000000).ok)
        set_mark(rt, "AAPL", "20.00")
        self.assertTrue(buy(rt, US_EXCHANGE, "AAPL", 10, "20.00", fee_c=0).ok)   # settles D2; nothing settles it in this runtime
        rt.clock.set("2026-10-27T10:00:00-04:00")                                  # 7 business days later
        codes = lambda: [p.id for p in rt.accounting.integrity("ledger").problems]   # noqa: E731
        self.assertIn("L21", codes())
        rt.db.ensure_schema_ext("test", ["ALTER TABLE settlements ADD COLUMN fail_state TEXT"])
        with rt.db.writer() as tx:
            tx.conn.execute("UPDATE settlements SET fail_state='FAILED'")
        self.assertNotIn("L21", codes())


@unittest.skipUnless("2345" in CHART and "4453" in CHART, "needs P4's ledger codes 2345 / 4453 (P4 task 7)")
class OtcCostOfDealing(_Base):
    """(d): a contract whose upfront RECEIVED 48,492.57 sits in GL 2345 and whose mark is -49,176.13: unrealised -683.56, realised 0.00, and NAV
    moves by -683.56 at booking (the initial payment 51,075.90 received in cash less the accrued premium 2,583.33 less MV 49,176.13)."""

    def test_cdx_upfront_received_gives_the_cost_of_dealing(self):
        rt = self.rt
        nav0 = rt.accounting.nav("T1:ALL").nav_c
        cid = "SWP-000001"
        with rt.db.writer() as tx:
            tx.conn.execute("INSERT INTO accounts(account_id, book_code, lifetime_id, role, name, kind, broker, opened_ts, sort_order) VALUES (?,?,?,?,?,?,?,?,?)",
                            (PB, "T", T1, "PB_GS", "Goldman (PB)", "PB", "GS", rt.clock.epoch(), 90))
        # the booking through P4's real code is P4's own test; here: the contract row, its 2345 upfront and its receivable, as P4 task 7 books them
        res = execute(rt, "test.post", {"kind": "OTC_UPFRONT", "memo": "CDX upfront received", "idem_key": f"upfront:{cid}",
                                         "lines": [[PB, "1010", "USD", 5107590], [PB, "2340", "USD", -258333, cid], [PB, "2345", "USD", -4849257, cid]]})
        self.assertTrue(res.ok, res.body)
        with rt.db.writer() as tx:
            tx.conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date,"
                            " margin_mode, im_c, status, mark_c, mark_ts, vm_balance_c) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',?,?,0)",
                            (cid, 1, PB, "GS", "CDX", json.dumps({"mark0_c": -4917613}), 300000000, "USD", D1, "2031-12-20", "CTM", 0, -4917613, rt.clock.epoch()))
        p = next(x for x in rt.accounting.positions(PB) if x.instrument_id == cid)
        self.assertEqual((-4849257, -4917613, -68356, 0), (p.cost_c, p.mv_c, p.unrealised_c, p.realised_itd_c))
        nav = rt.accounting.nav("T1:ALL")
        self.assertEqual(-68356, nav.nav_c - nav0)
        bs = rt.accounting.balance_sheet("T1:ALL")
        self.assertEqual(0, bs.rounding_c)                                         # A - L = NAV with the contract at MV


if __name__ == "__main__":
    unittest.main()
