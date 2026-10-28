"""PORTFOLIO-20 (FINSIM1_PLAN P3 task 14): the contract between ``GET /api/portfolio`` and ``static/pages/portfolio.js``.

The live 1.0.3.1 Portfolio grid broke because ``by_class`` arrived as a dict while the page read a list.  This test builds a real
runtime (``server.build_runtime``, offline, FakeProvider, FakeClock, a temp home) holding a priced equity, an unpriced one and an
OTC contract carried at its mark with a contract cost (C9c), and asserts the answer has every field ``portfolio.js`` reads: the
NAV and P&L tiles, ``by_class`` as a LIST of class rows, the footer keys (POSTCLOSE-07: gross long / short tie to the NAV block's
long / short market value, OTC at market beside them), each position row's keys and the ``by_account`` rows.  Money is a decimal
string in major units on the wire.  Nothing touches the network or ~/.finsim."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from decimal import Decimal

from tests import terminal_helpers as th
from finsim.terminal import config, server as _server
from finsim.terminal.clock import FakeClock
from finsim.terminal.quotes.providers import FakeProvider

AT = "2026-10-20T16:30:00-04:00"
D = Decimal

#: what portfolio.js reads (static/pages/portfolio.js: columns(), classRows(), the footer, the tiles and the by-account table)
POSITION_KEYS = {"account_id", "account_name", "book_code", "book_name", "instrument_id", "symbol", "name", "kind", "klass", "currency", "qty", "avg_px", "mark",
                 "mv_base", "notional", "day_pnl", "unrealised_base", "realised_itd", "income_itd", "financing_itd", "weight", "days_held", "extra"}
CLASS_KEYS = {"klass", "label", "count", "mv", "unrealised", "day_pnl", "cost", "weight", "missing", "day_pnl_missing"}
FOOTER_KEYS = {"gross_long", "gross_short", "net", "futures_notional_long", "futures_notional_short", "otc_mv_positive", "otc_mv_negative", "unpriced",
               "fx_missing", "count", "note"}
NAV_KEYS = {"nav", "assets", "liabilities", "unrealised", "fx_result", "quality", "partial", "missing", "mv_long", "mv_short"}
ACCOUNT_KEYS = {"account_id", "name", "account_name", "nav", "weight", "positions", "pnl_today", "book_code", "book_name"}


def build(tmp: str):
    os.environ[config.HOME_ENV] = tmp
    os.environ[config.DB_ENV] = os.path.join(tmp, "terminal.db")
    clock = FakeClock(AT)
    return _server.build_runtime(tmp, os.path.join(tmp, "terminal.db"), clock=clock, offline=True, fake_provider=FakeProvider(clock), log=th._Log(), books=None,
                                 fetch_threads=0)


class PortfolioContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-pfcontract-")
        self.rt = build(self.tmp)
        rt = self.rt
        rt.bus.register("test.buy", th._h_test_buy)
        rt.bus.register("test.post", th._h_test_post)
        now = rt.clock.epoch()
        rt.fp.set("AAPL", last="339.50", quote_time=now - 120, bar=(now - now % 60 - 60, 61900))
        rt.cache.subscribe(["AAPL"], 300, "PAGE")
        rt.worker.tick()

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def get(self, path):
        status, body, _h = self.rt.router.handle("GET", path, None, None, "127.0.0.1", None)
        self.assertEqual(200, status, f"GET {path} -> {status} {body}")
        return json.loads(json.dumps(body, default=str))

    def test_portfolio_answer_has_the_shape_the_page_reads(self):
        rt = self.rt
        usx, pb = "F1:US_EXCHANGE", "F1:PB_GS"
        self.assertTrue(th.execute(rt, "treasury.transfer", {"from_account": "F1:TREASURY", "to_account": usx, "currency": "USD", "amount_c": 100000000}).ok)
        # BUY 100 AAPL @ 339.50, fee 1.00 (priced at the cached 339.50) and BUY 10 of a name with no quote (carried at cost, counted unpriced)
        self.assertTrue(th.execute(rt, "test.buy", {"account_id": usx, "instrument_id": "AAPL", "qty": 100, "price": "339.50", "fee_c": 100}).ok)
        self.assertTrue(th.execute(rt, "test.buy", {"account_id": usx, "instrument_id": "MSFT", "qty": 10, "price": "500.00", "fee_c": 0}).ok)
        # an OTC contract: upfront received 48,492.57 in GL 2345 (C9c cost basis), carried at its VM mark -49,176.13 -> unrealised -683.56
        with rt.db.writer() as tx:
            tx.conn.execute("INSERT INTO accounts(account_id, book_code, lifetime_id, role, name, kind, broker, opened_ts, sort_order) VALUES (?,?,?,?,?,?,?,?,?)",
                            (pb, "F", "F1", "PB_GS", "Goldman (PB)", "PB", "GS", rt.clock.epoch(), 90))
        cid = "SWP-000001"
        r = th.execute(rt, "test.post", {"kind": "OTC_UPFRONT", "memo": "upfront received", "idem_key": f"upfront:{cid}",
                                         "lines": [[pb, "1010", "USD", 4849257], [pb, "2345", "USD", -4849257, cid]]})
        self.assertTrue(r.ok, r.body)
        with rt.db.writer() as tx:
            tx.conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date,"
                            " margin_mode, im_c, status, mark_c, mark_ts, vm_balance_c) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',?,?,0)",
                            (cid, 1, pb, "GS", "CDX", json.dumps({"mark0_c": -4917613}), 300000000, "USD", "2026-10-20", "2031-12-20", "CTM", 0, -4917613,
                             rt.clock.epoch()))
        out = self.get("/api/portfolio?book=F")
        # the tiles: NAV and the four P&L periods with their baseline labels
        self.assertTrue(NAV_KEYS <= set(out["nav"]), sorted(NAV_KEYS - set(out["nav"])))
        for period in ("TODAY", "MTD", "YTD", "ITD"):
            self.assertIn("pnl", out["pnl"][period])
            self.assertIn("label", out["pnl"][period]["baseline"])
        # by_class is a LIST of class rows (the 1.0.3.1 defect: a dict)
        self.assertIsInstance(out["by_class"], list)
        for c in out["by_class"]:
            self.assertTrue(CLASS_KEYS <= set(c), sorted(CLASS_KEYS - set(c)))
        self.assertEqual({"securities", "OTC derivatives"}, {c["klass"] for c in out["by_class"]})
        # the footer: gross long / short are the NAV block's long / short market value (POSTCLOSE-07); the contract is beside them at market
        f = out["footer"]
        self.assertTrue(FOOTER_KEYS <= set(f), sorted(FOOTER_KEYS - set(f)))
        self.assertEqual((D(out["nav"]["mv_long"]), D(out["nav"]["mv_short"])), (D(f["gross_long"]), D(f["gross_short"])))
        self.assertEqual((D("33950.00"), D("0.00"), D("0.00"), D("49176.13"), 1), (D(f["gross_long"]), D(f["gross_short"]), D(f["otc_mv_positive"]),
                                                                                  D(f["otc_mv_negative"]), f["unpriced"]))
        # every position row has what the grid reads
        rows = {p["instrument_id"]: p for p in out["positions"]}
        self.assertEqual({"AAPL", "MSFT", cid}, set(rows))
        for p in rows.values():
            self.assertTrue(POSITION_KEYS <= set(p), (p["instrument_id"], sorted(POSITION_KEYS - set(p))))
            self.assertIn("quality", p["mark"])
        # AAPL: 100 x 339.50 = 33,950.00 at market, cost 33,950.00 -> unrealised 0.00; avg cost 339.50
        self.assertEqual((100, D("339.5"), D("33950.00"), D("0.00")), (rows["AAPL"]["qty"], D(rows["AAPL"]["avg_px"]), D(rows["AAPL"]["mv_base"]),
                                                                        D(rows["AAPL"]["unrealised_base"])))
        # MSFT unpriced: no market value, carried at cost (5,000.00) and said so
        self.assertEqual((None, True), (rows["MSFT"]["mv_base"], rows["MSFT"]["extra"].get("at_cost")))
        # the contract: cost -48,492.57 (upfront received), MV -49,176.13, unrealised -683.56 (C9c)
        self.assertEqual((D("-48492.57"), D("-49176.13"), D("-683.56")), (D(rows[cid]["cost"]), D(rows[cid]["mv_base"]), D(rows[cid]["unrealised_base"])))
        # P8L-02: what the qty cell reads for an OTC contract (core.js T.contractQty): kind SWAP / class 'OTC derivatives', the signed qty
        # (the notional in whole dollars) and its currency and notional (a major-unit string on the wire), so a contract without its
        # units / unit_label still reads "3,000,000 USD notional", never a bare quantity
        self.assertEqual(("SWAP", "OTC derivatives", 3000000, "USD", D("3000000.00")),
                         (rows[cid]["kind"], rows[cid]["klass"], rows[cid]["qty"], rows[cid]["extra"]["qty_unit"], D(rows[cid]["extra"]["notional"])))
        # A - L = NAV with the contract at market value (1335 / 2345 out of the NAV asset / liability sets)
        self.assertEqual(D(out["nav"]["nav"]), D(out["nav"]["assets"]) - D(out["nav"]["liabilities"]))
        # the by-account table
        for a in out["by_account"]:
            self.assertTrue(ACCOUNT_KEYS <= set(a), (a.get("account_id"), sorted(ACCOUNT_KEYS - set(a))))
        # the PB header counts its contract as a position (POSTCLOSE-04)
        self.assertEqual(1, self.get(f"/api/accounts/{pb}")["positions_count"])


if __name__ == "__main__":
    unittest.main()
