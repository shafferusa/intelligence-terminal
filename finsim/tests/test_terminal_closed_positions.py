"""P3 phase 2 (FINSIM1_PLAN P3 tasks 4-6; PORTFOLIO-03, -04, -05): closed positions, the position rows' total P&L / asset class /
counterparty, and the position drill-down (every fill with its order and settlement state, the entries paged with net cash and their
groups, closed lots and reliefs, the dividend notes of the holding period).

Every money figure is worked by hand in the comment beside it (section 3 rule 3).  Nothing touches the network or ~/.finsim."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from tests import terminal_helpers as th
from tests.terminal_helpers import (TREASURY, US_EXCHANGE, buy, execute, fund, make_runtime, post_entry, sell, set_mark)
from finsim.terminal import config, server as _server
from finsim.terminal.clock import FakeClock, epoch_of
from finsim.terminal.quotes.providers import FakeProvider

D = Decimal
PB = "T1:PB_GS"


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-closed-")
        self.rt = make_runtime(self.tmp, at="2026-10-20T14:00:00-04:00")

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class ClosedPositions(_Base):
    def test_round_trip_realised_and_fees_apart(self):
        """P3 acceptance 1 (b): BUY 10 SPY @ 769.47 then SELL 10 @ 769.67 -> realised 2.00, the fees shown separately."""
        rt = self.rt
        self.assertTrue(fund(rt, US_EXCHANGE, 2_000_000_00).ok)
        b = buy(rt, US_EXCHANGE, "SPY", 10, "769.47", fee_c=100)                      # gross 7,694.70, fee 1.00
        self.assertTrue(b.ok, b.body)
        rt.clock.set("2026-10-21T10:00:00-04:00")
        s = sell(rt, US_EXCHANGE, "SPY", 10, "769.67", fee_c=100)                     # gross 7,696.70, fee 1.00
        self.assertTrue(s.ok, s.body)
        rows = rt.accounting.closed_positions("T1:ALL")
        self.assertEqual(1, len(rows))
        c = rows[0]
        # realised = proceeds - cost relieved = 7,696.70 - 7,694.70 = 2.00; fees = 1.00 + 1.00 = 2.00; total = 2.00 - 2.00 = 0.00
        self.assertEqual((200, 200, 0, 0, 0), (c.realised_c, c.fees_c, c.income_c, c.financing_c, c.total_pnl_c))
        self.assertEqual(("SPY", "ETF", "LONG", 10, D("769.47"), D("769.67")), (c.instrument_id, c.asset_class, c.side, c.qty, D(str(c.avg_open_px)), D(str(c.avg_close_px))))
        self.assertEqual(("2026-10-20", "2026-10-21", 1, "sold", "fills"), (c.open_date, c.close_date, c.days_held, c.how, c.source))
        self.assertEqual(([b.body["fill_id"]], [s.body["fill_id"]]), (c.open_fill_ids, c.closing_fill_ids))
        self.assertEqual("exchange-cleared (NSCC)", c.counterparty)
        # a new position in the same name is open, not closed: the closed list keeps its one row
        rt.clock.set("2026-10-21T11:00:00-04:00")
        self.assertTrue(buy(rt, US_EXCHANGE, "SPY", 5, "770.00", fee_c=0).ok)
        self.assertEqual(1, len(rt.accounting.closed_positions("T1:ALL")))
        # the date filter reads the close's New York date
        self.assertEqual(1, len(rt.accounting.closed_positions("T1:ALL", "2026-10-21", "2026-10-21")))
        self.assertEqual([], rt.accounting.closed_positions("T1:ALL", "2026-10-22", None))
        # the closed lot and its relief (7,694.70 cost relieved by 10 at 769.67 -> proceeds 7,696.70, realised 2.00)
        lots, reliefs = rt.accounting.closed_lots(US_EXCHANGE, "SPY")
        self.assertEqual([(10, 0, 0, "769.47")], [(l["qty_open"], l["qty_remaining"], l["cost_c"], l["unit_cost"]) for l in lots])   # cost_c = the cost left: 0
        self.assertEqual([(s.body["fill_id"], 10, 769470, 769670, 200)], [(r["close_fill_id"], r["qty"], r["cost_relieved_c"], r["proceeds_c"], r["realised_c"]) for r in reliefs])

    def test_otc_contract_terminated_and_a_returned_borrow(self):
        rt = self.rt
        with rt.db.writer() as tx:
            tx.conn.execute("INSERT INTO accounts(account_id, book_code, lifetime_id, role, name, kind, broker, opened_ts, sort_order) VALUES (?,?,?,?,?,?,?,?,?)",
                            (PB, "T", "T1", "PB_GS", "Goldman Sachs (PB)", "PB", "GS", rt.clock.epoch(), 90))
            tx.conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date,"
                            " margin_mode, im_c, status, mark_c, mark_ts, vm_balance_c, closed_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'TERMINATED',?,?,0,?)",
                            ("SWP-000009", 1, PB, "GS", "TRS", json.dumps({"units": 1000, "receiver": False, "underlying_id": "AAPL"}), 33950000, "USD", "2026-10-13",
                             "2027-10-13", "CTM", 0, 0, rt.clock.epoch(), epoch_of(date(2026, 10, 19), "15:00")))
        # the contract's lines: return leg / termination realised +1,000.00 (Cr 4450), TRS financing 100.00 (Dr 4451), a fee 10.00 (Dr 5000)
        cid = "SWP-000009"
        self.assertTrue(post_entry(rt, [[PB, "1010", "USD", 100000], [PB, "4450", "USD", -100000, cid]], "test:swp9:result", kind="SWAP").ok)
        self.assertTrue(post_entry(rt, [[PB, "4451", "USD", 10000, cid], [PB, "1010", "USD", -10000]], "test:swp9:fin", kind="ACCRUAL").ok)
        self.assertTrue(post_entry(rt, [[PB, "5000", "USD", 1000, cid], [PB, "1010", "USD", -1000]], "test:swp9:fee", kind="FEE").ok)
        # a returned stock borrow of DLR: fee 3.00 (Dr 5300) and rebate 1.00 (Cr 4350) dated inside its dates (US Exchange funded first: no overdraft)
        self.assertTrue(fund(rt, US_EXCHANGE, 100000).ok)
        with rt.db.writer() as tx:
            tx.conn.execute("INSERT INTO borrows(borrow_id, command_id, account_id, instrument_id, qty, qty_returned, fee_rate, rebate_rate, tier, lender, collateral_c,"
                            " currency, opened_ts, opened_date, status, closed_ts) VALUES ('B-000009', 1, ?, 'DLR', 100, 100, '0.003', '0.03', 'GC',"
                            " 'street lender (modelled)', 0, 'USD', ?, '2026-10-14', 'CLOSED', ?)",
                            (US_EXCHANGE, epoch_of(date(2026, 10, 14), "10:00"), epoch_of(date(2026, 10, 20), "10:00")))
        self.assertTrue(post_entry(rt, [[US_EXCHANGE, "5300", "USD", 300, "DLR"], [US_EXCHANGE, "1010", "USD", -300]], "test:b9:fee", kind="ACCRUAL").ok)
        self.assertTrue(post_entry(rt, [[US_EXCHANGE, "1010", "USD", 100], [US_EXCHANGE, "4350", "USD", -100, "DLR"]], "test:b9:rebate", kind="ACCRUAL").ok)
        rows = {r.instrument_id: r for r in rt.accounting.closed_positions("T1:ALL")}
        o = rows["SWP-000009"]
        # realised 1,000.00; financing 100.00; fees 10.00 -> total 1,000.00 - 100.00 - 10.00 = 890.00
        self.assertEqual((100000, 10000, 1000, 0, 89000), (o.realised_c, o.financing_c, o.fees_c, o.income_c, o.total_pnl_c))
        self.assertEqual(("OTC TRS", "terminated", "otc", 1000, "2026-10-13", "2026-10-19", 6, "pay"), (o.asset_class, o.how, o.source, o.qty, o.open_date, o.close_date, o.days_held, o.side))
        self.assertEqual("Goldman Sachs (bilateral OTC, ISDA / CSA)", o.counterparty)
        b = rows["DLR"]
        # the borrow: financing 3.00, income (rebate) 1.00 -> -2.00; the lender worded, the stored row untouched
        self.assertEqual((300, 100, -200, "borrow", "street lender (assumed terms)"), (b.financing_c, b.income_c, b.total_pnl_c, b.source, b.counterparty))
        with rt.db.reader() as conn:
            self.assertEqual("street lender (modelled)", conn.execute("SELECT lender FROM borrows WHERE borrow_id='B-000009'").fetchone()[0])
        # the API view: a borrow is never added to the totals (the short's financing already holds it)
        from finsim.terminal.api_views import closed_csv, closed_view
        v = closed_view(rt, "T1:ALL")
        self.assertEqual((89000, 100000, 1000, 10000, 0), (v["totals"]["total_pnl_c"], v["totals"]["realised_c"], v["totals"]["fees_c"], v["totals"]["financing_c"],
                                                            v["totals"]["income_c"]))
        lines = closed_csv(v).strip().split("\n")
        self.assertEqual(3, len(lines))
        self.assertTrue(lines[0].startswith("account_id,instrument_id,symbol,asset_class,side,qty,open_date,close_date"))


class PositionFields(_Base):
    def test_total_pnl_is_unrealised_realised_income_financing_and_fees(self):
        """P3 acceptance 1 (c): total P&L = unrealised + realised + income + financing + fees, on a position with all five."""
        rt = self.rt
        self.assertTrue(fund(rt, US_EXCHANGE, 1_000_000_00).ok)
        self.assertTrue(buy(rt, US_EXCHANGE, "AAPL", 100, "300.00", fee_c=100).ok)       # cost 30,000.00, fee 1.00
        rt.clock.set("2026-10-20T15:00:00-04:00")
        self.assertTrue(sell(rt, US_EXCHANGE, "AAPL", 40, "310.00", fee_c=100).ok)       # relieves 12,000.00, proceeds 12,400.00 -> realised 400.00
        # income 5.00 (a dividend on the name, Cr 4200) and financing 2.00 (a borrow-type charge, Dr 5300) on the instrument
        self.assertTrue(post_entry(rt, [[US_EXCHANGE, "1010", "USD", 500], [US_EXCHANGE, "4200", "USD", -500, "AAPL"]], "test:aapl:div", kind="DIVIDEND").ok)
        self.assertTrue(post_entry(rt, [[US_EXCHANGE, "5300", "USD", 200, "AAPL"], [US_EXCHANGE, "1010", "USD", -200]], "test:aapl:fin", kind="ACCRUAL").ok)
        set_mark(rt, "AAPL", "339.50")
        p = rt.accounting.position(US_EXCHANGE, "AAPL")
        # unrealised = 60 x 339.50 - 60 x 300.00 = 20,370.00 - 18,000.00 = 2,370.00
        self.assertEqual((60, 237000, 40000, 500, -200, -200), (p.qty, p.unrealised_base_c, p.realised_itd_c, p.income_itd_c, p.financing_itd_c, p.fees_itd_c))
        # total = 2,370.00 + 400.00 + 5.00 - 2.00 - 2.00 = 2,771.00
        self.assertEqual(277100, p.total_pnl_c)
        self.assertEqual(("Equity", "exchange-cleared (NSCC)", "Information Technology", "master"), (p.asset_class, p.counterparty, p.sector, p.sector_source))
        # an unpriced row has no total (carried at cost: never a guessed P&L)
        self.assertTrue(buy(rt, US_EXCHANGE, "MSFT", 10, "500.00", fee_c=0).ok)
        m = rt.accounting.position(US_EXCHANGE, "MSFT")
        self.assertEqual((None, None), (m.unrealised_base_c, m.total_pnl_c))


def _build(tmp: str, at: str = "2026-10-20T15:30:00-04:00"):
    os.environ[config.HOME_ENV] = tmp
    os.environ[config.DB_ENV] = os.path.join(tmp, "terminal.db")
    clock = FakeClock(at)
    return _server.build_runtime(tmp, os.path.join(tmp, "terminal.db"), clock=clock, offline=True, fake_provider=FakeProvider(clock), log=th._Log(), books=None,
                                 fetch_threads=0)


class DrillDown(unittest.TestCase):
    """PORTFOLIO-05: GET /api/accounts/{id}/positions/{instrument} on a real runtime."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-drill-")
        self.rt = _build(self.tmp)
        self.rt.bus.register("test.buy", th._h_test_buy)
        self.rt.bus.register("test.sell", th._h_test_sell)
        self.rt.bus.register("test.post", th._h_test_post)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def get(self, path, query=None):
        status, body, _h = self.rt.router.handle("GET", path, query, None, "127.0.0.1", None)
        self.assertEqual(200, status, f"GET {path} -> {status} {body}")
        from finsim.terminal.api import encode
        return json.loads(json.dumps(encode(body), default=str))

    def test_fills_orders_settlements_entries_paged_with_net_cash(self):
        rt = self.rt
        usx = "F1:US_EXCHANGE"
        self.assertTrue(th.execute(rt, "treasury.transfer", {"from_account": "F1:TREASURY", "to_account": usx, "currency": "USD", "amount_c": 100000000}).ok)
        b = th.execute(rt, "test.buy", {"account_id": usx, "instrument_id": "AAPL", "qty": 10, "price": "339.50", "fee_c": 100})      # gross 3,395.00
        self.assertTrue(b.ok, b.body)
        s = th.execute(rt, "test.sell", {"account_id": usx, "instrument_id": "AAPL", "qty": 4, "price": "340.00", "fee_c": 100})      # realised 4 x 0.50 = 2.00
        self.assertTrue(s.ok, s.body)
        # a dividend received on the name: Dr 1010 5.00 / Cr 4200 5.00 (AAPL) - an entry that moves the account's cash
        self.assertTrue(th.execute(rt, "test.post", {"kind": "DIVIDEND", "memo": "dividend AAPL", "idem_key": "test:div:aapl",
                                                     "lines": [[usx, "1010", "USD", 500], [usx, "4200", "USD", -500, "AAPL"]]}).ok)
        # a dividend whose ex-date (10-01) is before the position was first held (10-20): "not held on the ex-date"
        with rt.db.writer() as tx:
            tx.conn.execute("INSERT INTO dividends(instrument_id, ex_date, amount, pay_date, source, fetched_ts) VALUES ('AAPL', '2026-10-01', '0.26', NULL, 'test', 0)")
        d = self.get(f"/api/accounts/{usx}/positions/AAPL", {"entries_limit": "1"})
        # every fill, each with its settlement state (both settle T+1: pending)
        self.assertEqual((2, 2), (len(d["fills"]), d["fills_total"]))
        st = {f["fill_id"]: f["settlement"] for f in d["fills"]}
        self.assertEqual({"settling"}, {str(x["stage"]).lower() for x in st.values()})          # C15 (P6's settlement_state when bound)
        self.assertTrue(all("settl" in str(x["label"]).lower() for x in st.values()), st)
        # entries paged: 3 on the instrument (the two fills and the dividend), 1 per page; net cash per entry in the account's settled cash
        self.assertEqual((1, 3, 0, 1), (len(d["entries"]), d["entries_total"], d["entries_offset"], d["entries_limit"]))
        first = d["entries"][0]                                   # newest first: the dividend
        self.assertEqual(("dividends", {"USD": "5.00"}), (first["group"], first["net_cash"]))
        d2 = self.get(f"/api/accounts/{usx}/positions/AAPL", {"entries_limit": "5", "entries_offset": "1"})
        self.assertEqual([("fills", {}), ("fills", {})], [(e["group"], e["net_cash"]) for e in d2["entries"]])   # a fill moves no cash until it settles
        self.assertEqual({"fills": 2, "dividends": 1}, {g["group"]: g["count"] for g in d["entry_groups"]})
        # the relief of the partly closed lot: 4 of 10 at 340.00 -> proceeds 1,360.00, cost relieved 4 x 339.50 = 1,358.00, realised 2.00
        self.assertEqual([], d["closed_lots"])
        self.assertEqual([(4, "1358.00", "1360.00", "2.00")], [(r["qty"], r["cost_relieved"], r["proceeds"], r["realised"]) for r in d["reliefs"]])
        # the dividend note of the holding period
        self.assertTrue(any("not held on the ex-date" in n for n in d["dividend_notes"]), d["dividend_notes"])
        # the position row's fields
        p = d["position"]
        self.assertEqual(("Equity", "exchange-cleared (NSCC)", "Information Technology"), (p["asset_class"], p["counterparty"], p["sector_label"]))
        self.assertIn("margin", p)


if __name__ == "__main__":
    unittest.main()
