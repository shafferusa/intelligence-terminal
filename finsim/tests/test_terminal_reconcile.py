"""WP-15 reconciliation suite and the acceptance tests D1-D12 of BUILD_PROMPT_2026-10-20.md (TERMINAL_DESIGN.md §19.12
"Reconciliation", §19.24; TERMINAL_WORKPLAN.md WP-15).

Two parts:

1. ``DMapTests`` — the D1-D12 map: every acceptance test names the test methods that prove it (hand-worked numbers in each),
   and this test fails if one of them disappears or is renamed, so the map in docs/TERMINAL_CHECKLIST.md cannot rot.
2. ``ReconcileTests`` — ONE database per test, the REAL runtime (``server.build_runtime``: every module bound exactly as
   ``serve`` binds it, the Router, the lifecycle scheduler) driven through the HTTP API the UI uses, with a FakeClock and a
   FakeProvider (no network).  Four books: the Test Book T1 (10,000,000.00), Personal P1 (1,000,000.00), Fund F1
   (25,000,000.00), Institutional I1 (1,000,000,000.00).  After EVERY step ``reconcile()`` asserts, per open lifetime:
   integrity full has no ERROR (L1-L25); balance sheet assets - liabilities == NAV with rounding 0; the NAV identity
   ``NAV = capital + advances + realised + income - financing - fees + unrealised + fx_result`` to the cent (swap overlays
   included), for the lifetime and for each of its accounts; Σ accounts' NAV == the lifetime's NAV; advances (3100) net to 0
   over the lifetime and Treasury's advances == Σ accounts' 3100; the trial balance is 0 per currency natively and 0 in base;
   ``transactions()`` lists every journal entry of every account exactly once; ``pnl(ITD)`` residual 0.00 and equal to
   NAV - capital; and ``ALL_BOOKS`` == Σ lifetimes.

Prices (FakeProvider, Tue 2026-10-20 14:00 ET, observed 120 s earlier on a completed 1-minute bar of 61,900): AAPL 339.50,
SPY 769.32, MSFT 500.00, EURUSD 1.1238.  Curve: the §19.15 FixedCurve extended to five years, flat 4.00 % semi-annual:
df(k) = 1 / 1.02^k to 8 dp = 0.98039216, 0.96116878, 0.94232233, 0.92384543, 0.90573081, 0.88797138, 0.87056018,
0.85349037, 0.83675527, 0.82034830; Σ = 8.98258501; annuity A = 4.491292505; par = 2 × (1 - 0.82034830) / 8.98258501 =
0.0399999999554… -> 0.04000.  Day 2: flat 4.10 %, df(k) = 1 / 1.0205^k: 0.97991181, 0.96022715, 0.94093792, 0.92203618,
0.90351414, 0.88536418, 0.86757881, 0.85015072, 0.83307273, 0.81633781; Σ = 8.95913145; A = 4.479565725; par =
2 × (1 - 0.81633781) / 8.95913145 = 0.0409999989… -> 0.04100.  SOFR 3.87 % and ECBDFR 2.00 % observed 2026-10-19.
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import os
import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import _Log

# A release leaves the swap modules out until swaps are cleared for deployment: the D10 swap leg skips there.
HAVE_SWAPS = importlib.util.find_spec("finsim.terminal.products.swaps") is not None
from finsim.terminal import config, server as _server
from finsim.terminal.clock import FakeClock
from finsim.terminal.instruments import otc_reference_ids
from finsim.terminal.quotes.providers import FakeProvider

D = Decimal
AT = "2026-10-20T14:00:00-04:00"                     # Tuesday 14:00 ET: US equities, FX and US bonds open
BOOKS = [config.TEST_BOOK] + list(config.BOOKS)
DFS_400 = {"0.5": "0.98039216", "1.0": "0.96116878", "1.5": "0.94232233", "2.0": "0.92384543", "2.5": "0.90573081", "3.0": "0.88797138",
           "3.5": "0.87056018", "4.0": "0.85349037", "4.5": "0.83675527", "5.0": "0.82034830"}
DFS_410 = {"0.5": "0.97991181", "1.0": "0.96022715", "1.5": "0.94093792", "2.0": "0.92203618", "2.5": "0.90351414", "3.0": "0.88536418",
           "3.5": "0.86757881", "4.0": "0.85015072", "4.5": "0.83307273", "5.0": "0.81633781"}
IRS_5Y = {"notional": "10000000.00", "currency": "USD", "pay_fixed": True, "tenor_months": 60, "fixed_rate": None, "fixed_basis": "30/360", "fixed_months": 6,
          "float_index": "SOFR", "float_basis": "ACT/360", "float_months": 3, "fixing": "TERM"}

# ---- the D1-D12 map (docs/TERMINAL_CHECKLIST.md "Acceptance tests D1-D13") --------------------------------------------------------------
D_MAP = {
    "D1 Treasury 10,000,000 -> add 2,500,000 -> 12,500,000, P&L 0": [
        ("test_terminal_treasury", "test_d1_initial_capital_and_deposit"), ("test_terminal_books", "test_first_run_three_books"),
        ("test_terminal_reconcile", "test_d1_d2_d12_through_the_api_in_one_db")],
    "D2 buy 100 AAPL with 0 cash: sweep = 100 x price + commission, linked, T+1, NAV - commission": [
        ("test_terminal_sweeps", "test_d2_buy_100_aapl_zero_cash"), ("test_terminal_api", "test_preview_submit_order_fills_transactions"),
        ("test_terminal_reconcile", "test_d1_d2_d12_through_the_api_in_one_db")],
    "D3 restart between steps; a second server refuses": [
        ("test_terminal_persistence", "test_restart_between_every_step"), ("test_terminal_server", "test_duplicate_server_exit_3"),
        ("test_terminal_reconcile", "test_d3_restart_keeps_everything_and_the_lock_refuses_a_second_server")],
    "D4 short 1,000 with borrow; +10 % call 1.02 x 1,000 x dP; -5 % excess back; weekend nights": [
        ("test_terminal_shorts", "test_d4_collateral_marks_both_ways")],
    "D5 no borrow -> rejected; partial borrow -> scaled": [("test_terminal_shorts", "test_d5_no_borrow_and_partial")],
    "D6 outside hours queues; buy limit only at or below the limit; DAY expires; cancelled / expired stay": [
        ("test_terminal_orders", "test_d6_session_limit_day_expiry"), ("test_terminal_api", "test_orders_history_keeps_cancelled_expired")],
    "D7 stock + protective put together; a partial fill resizes the hedge": [("test_terminal_orders", "test_d7_hedge_package_resize")],
    "D8 expiry on the real date: ITM call exercises, OTM lapses, short ITM assigned": [("test_terminal_options", "test_d8_expiry_exercise_lapse_assignment")],
    "D9 borrow 1,000,000 EUR, convert the same day, T+2, repaying needs EUR": [("test_terminal_fx", "test_d9_eur_loan_and_spot")],
    "D10 Goldman account: its commission, DVP, margin and exposure per PB": [
        ("test_terminal_prime", "test_d10_goldman_buy"), ("test_terminal_risk", "test_exposure_per_pb"),
        ("test_terminal_reconcile", "test_d10_goldman_and_pb_swaps_with_daily_vm_through_the_api")],
    "D11 Rule 201 / 13D-G / self-trading notices, none blocks": [("test_terminal_risk", "test_d11_compliance_notices")],
    "D12 integrity after every step; a corrupted entry fails and shows under Requires your attention": [
        ("test_terminal_bus", "test_d12_corrupted_balance_rolls_back"), ("test_terminal_news", "test_attention_shows_integrity_failure"),
        ("test_terminal_reconcile", "test_d1_d2_d12_through_the_api_in_one_db")],
}


def _has_test(module: str, method: str) -> bool:
    mod = importlib.import_module(f"tests.{module}")
    return any(inspect.isclass(c) and issubclass(c, unittest.TestCase) and callable(getattr(c, method, None)) for c in vars(mod).values())


class DMapTests(unittest.TestCase):
    def test_every_acceptance_test_names_existing_test_methods(self):
        self.assertEqual(12, len(D_MAP))
        missing = [(d, m, t) for d, refs in D_MAP.items() for m, t in refs if not _has_test(m, t)]
        self.assertEqual([], missing)


# ---- the one-database reconciliation through the API ------------------------------------------------------------------------------------
def build(tmp: str, at: str = AT):
    os.environ[config.HOME_ENV] = tmp
    os.environ[config.DB_ENV] = os.path.join(tmp, "terminal.db")
    clock = FakeClock(at)
    fp = FakeProvider(clock)
    rt = _server.build_runtime(tmp, os.path.join(tmp, "terminal.db"), clock=clock, offline=True, fake_provider=fp, log=_Log(), books=BOOKS,
                               fetch_threads=0, news_enabled=False)
    return rt


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-reconcile-")
        self.rt = build(self.tmp)
        self.market()

    def tearDown(self):
        if self.rt is not None:
            self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- the market (FakeProvider -> the inline worker -> the quote cache, the curve and the rate stores) -------------------------------
    def market(self, dfs=DFS_400, curve_as_of="2026-10-19"):
        rt = self.rt
        now = rt.clock.epoch()
        qt = now - 120
        for iid, sym, last in (("AAPL", "AAPL", "339.50"), ("SPY", "SPY", "769.32"), ("MSFT", "MSFT", "500.00"), ("EURUSD", "EURUSD=X", "1.1238")):
            rt.fp.set(sym, last=last, quote_time=qt, bar=(qt - qt % 60 - 60, 61900))
            rt.cache.subscribe([iid], 3600, "PAGE")
        rt.fp.set_curve_dfs(dfs, curve_as_of)
        rt.fp.set_rate("SOFR", [("2026-10-19", "3.87")])
        rt.fp.set_rate("ECBDFR", [("2026-10-19", "2.00")])
        rt.worker.tick()

    # ---- HTTP helpers ------------------------------------------------------------------------------------------------------------------
    def call(self, method, path, body=None, ip="127.0.0.1"):
        return self.rt.router.handle(method, path, None, body, ip, None)

    def ok(self, method, path, body=None):
        status, out, headers = self.call(method, path, body)
        self.assertEqual(200, status, f"{method} {path} -> {status} {out}")
        return out

    def nav(self, scope: str) -> int:
        return self.rt.accounting.nav(scope).nav_c

    # ---- §19.12 reconciliation, after every step ----------------------------------------------------------------------------------
    def reconcile(self, where: str) -> dict:
        rt = self.rt
        acc = rt.accounting
        snap = rt.marks.snapshot()
        rep = acc.integrity("full", snapshot=snap)
        errors = [p.to_dict() for p in rep.problems if p.severity == "ERROR"]
        self.assertEqual([], errors, f"{where}: integrity")
        self.assertTrue(rep.ok, where)
        with rt.db.reader() as conn:
            lids = [r[0] for r in conn.execute("SELECT lifetime_id FROM lifetimes WHERE status='OPEN' ORDER BY lifetime_id").fetchall()]
            accounts = {lid: [r[0] for r in conn.execute("SELECT account_id FROM accounts WHERE lifetime_id=? ORDER BY account_id", (lid,)).fetchall()] for lid in lids}
            tb = {lid: conn.execute("SELECT g.currency, SUM(g.balance_c) AS n, SUM(g.base_c) AS b FROM gl_balances g JOIN accounts a ON a.account_id=g.account_id"
                                    " WHERE a.lifetime_id=? GROUP BY g.currency", (lid,)).fetchall() for lid in lids}
            entries = {aid: sorted(r[0] for r in conn.execute("SELECT DISTINCT entry_id FROM journal_lines WHERE account_id=?", (aid,)).fetchall())
                       for lid in lids for aid in accounts[lid]}
            adv = {lid: {aid: int(conn.execute("SELECT COALESCE(SUM(balance_c), 0) FROM gl_balances WHERE account_id=? AND gl='3100'", (aid,)).fetchone()[0])
                         for aid in accounts[lid]} for lid in lids}
        navs = {}
        for lid in lids:
            scope = f"{lid}:ALL"
            n = acc.nav(scope, snapshot=snap)
            navs[lid] = n.nav_c
            bs = acc.balance_sheet(scope, snapshot=snap)
            self.assertEqual((n.nav_c, 0), (bs.assets_c - bs.liabilities_c, bs.rounding_c), f"{where} {scope}: assets - liabilities = NAV")
            for v, label in [(n, scope)] + [(acc.nav(aid, snapshot=snap), aid) for aid in accounts[lid]]:
                ident = v.capital_c + v.transfers_c + v.realised_c + v.income_c - v.financing_c - v.fees_c + v.unrealised_c + v.fx_result_c
                self.assertEqual(v.nav_c, ident, f"{where} {label}: NAV = capital + advances + realised + income - financing - fees + unrealised + fx")
            self.assertEqual(n.nav_c, sum(acc.nav(aid, snapshot=snap).nav_c for aid in accounts[lid]), f"{where} {scope}: sum of the accounts")
            self.assertEqual(0, n.transfers_c, f"{where} {scope}: advances net to zero over the lifetime (L5)")
            t = acc.treasury(lid, snapshot=snap)
            self.assertTrue(t.identity_ok, f"{where} {lid}: Treasury NAV = capital + retained - advances")
            self.assertEqual(sum(v for a, v in adv[lid].items() if not a.endswith(":TREASURY")), -adv[lid][f"{lid}:TREASURY"], f"{where} {lid}: 3100")
            for r in tb[lid]:
                self.assertEqual(0, int(r["n"] or 0), f"{where} {lid}: trial balance {r['currency']} (debits = credits)")
            self.assertEqual(0, sum(int(r["b"] or 0) for r in tb[lid]), f"{where} {lid}: trial balance in base")
            for aid in accounts[lid]:
                rows = acc.transactions(aid, limit=100000)
                ids = [r.entry_id for r in rows]
                self.assertEqual(len(ids), len(set(ids)), f"{where} {aid}: an entry listed twice")
                self.assertEqual(entries[aid], sorted(ids), f"{where} {aid}: transactions() lists every entry once")
            p = acc.pnl(scope, "ITD", snapshot=snap)
            self.assertEqual((0, n.nav_c - n.capital_c), (p.residual_c, p.pnl_c), f"{where} {scope}: P&L ITD")
        self.assertEqual(sum(navs.values()), acc.nav("ALL_BOOKS", snapshot=snap).nav_c, f"{where}: ALL_BOOKS = the sum of the lifetimes")
        return navs

    # ==== D1, D2, D12 in T1 ========================================================================================================
    def test_d1_d2_d12_through_the_api_in_one_db(self):
        navs = self.reconcile("start")
        # the four first-run lifetimes: 10,000,000 + 1,000,000 + 25,000,000 + 1,000,000,000 = 1,036,000,000.00
        self.assertEqual({"T1": 1000000000, "P1": 100000000, "F1": 2500000000, "I1": 100000000000}, navs)
        # D1: Add funds 2,500,000.00 -> Treasury 12,500,000.00; a capital flow: NAV +2,500,000.00, P&L ITD = 12,500,000 - 12,500,000 = 0.00
        out = self.ok("POST", "/api/treasury/deposit", {"request_id": "ui:recon-d1-deposit-0001", "lifetime_id": "T1", "amount": "2500000.00"})
        self.assertEqual(("12500000.00", "12500000.00"), (out["treasury"]["capital"], out["treasury"]["nav"]))
        self.assertEqual(1250000000, self.reconcile("D1 deposit")["T1"])
        self.assertEqual(0, self.rt.accounting.pnl("T1:ALL", "ITD").pnl_c)
        # D2: BUY 100 AAPL MARKET in T1:US_EXCHANGE (0 cash): gross 100 x 339.50 = 33,950.00; fee max(100 x 0.005, 1.00) = 1.00; the sweep moves
        # exactly 33,951.00 from T1:TREASURY, linked to the fill; the payable settles T+1 (Wed 10-21); NAV 12,500,000.00 - 1.00 = 12,499,999.00
        req = {"account_id": "T1:US_EXCHANGE", "instrument_id": "AAPL", "side": "BUY", "qty": 100, "order_type": "MARKET", "tif": "DAY"}
        pv = self.ok("POST", "/api/orders/preview", dict(req))
        self.assertTrue(pv["ok"], pv.get("blockers"))
        out = self.ok("POST", "/api/orders", dict(req, request_id="ui:recon-d2-aapl-0001", preview_id=pv["preview_id"]))
        fill = out["fills"][0]
        sweep = out["sweeps"][0]
        self.assertEqual(("FILLED", "33950.00", "1.00", "2026-10-21"), (out["order"]["status"], fill["gross"], fill["fee"], fill["settle_date"]))
        self.assertEqual(("33951.00", "T1:TREASURY", "fill", fill["fill_id"]), (sweep["amount"], sweep["from_account"], sweep["linked_type"], sweep["linked_id"]))
        self.assertEqual(1249999900, self.reconcile("D2 fill")["T1"])
        self.assertEqual(-100, self.rt.accounting.pnl("T1:ALL", "ITD").pnl_c)                    # the commission, nothing else
        # Wed 00:35: SETTLE_DUE pays the payable: US_EXCHANGE settled 33,951.00 - 33,951.00 = 0.00, payable 0; NAV unchanged
        out = self.rt.lifecycle.tick(at="2026-10-21T00:35:00-04:00", only="SETTLE_DUE")
        self.assertEqual(["DONE"], [i["status"] for i in out["ran"]])
        cash = self.ok("GET", "/api/accounts/T1:US_EXCHANGE")["cash"]["USD"]
        self.assertEqual(("0.00", "0.00"), (cash["settled"], cash["payable"]))
        self.assertEqual(1249999900, self.reconcile("D2 settled")["T1"])
        # D12: one corrupted ledger balance (+0.01 on T1:US_EXCHANGE 1100 AAPL) -> integrity fails (L3), the run records an ERROR under
        # "Requires your attention" and holds T1's new orders; restoring it clears both
        with self.rt.db.writer() as tx:
            tx.conn.execute("UPDATE gl_balances SET balance_c = balance_c + 1 WHERE account_id='T1:US_EXCHANGE' AND gl='1100' AND instrument_id='AAPL'")
        rep = self.rt.accounting.integrity("full")
        self.assertFalse(rep.ok)
        self.assertIn("L3", rep.failing)
        att = self.ok("GET", "/api/attention")
        rows = att if isinstance(att, list) else (att.get("items") or att.get("rows") or [])
        self.assertTrue(any(r.get("code") == "INTEGRITY" and r.get("severity") == "ERROR" and r.get("ref_id") == "T1" for r in rows), rows)
        status, body, _ = self.call("POST", "/api/orders/preview", dict(req))
        codes = [body["error"]["code"]] if status != 200 else [b.get("code") for b in body.get("blockers") or []]
        self.assertIn("INTEGRITY_HOLD", codes, (status, body))                                    # T1 is held; the other books are not
        self.assertTrue(self.rt.accounting.is_held("T1") and not self.rt.accounting.is_held("F1"))
        with self.rt.db.writer() as tx:
            tx.conn.execute("UPDATE gl_balances SET balance_c = balance_c - 1 WHERE account_id='T1:US_EXCHANGE' AND gl='1100' AND instrument_id='AAPL'")
        self.assertTrue(self.rt.accounting.integrity("full").ok)
        self.assertFalse(self.rt.accounting.is_held("T1"))
        self.reconcile("D12 restored")

    # ==== D3: a restart keeps everything; the lock refuses a second server ==========================================================
    def test_d3_restart_keeps_everything_and_the_lock_refuses_a_second_server(self):
        self.ok("POST", "/api/treasury/deposit", {"request_id": "ui:recon-d3-deposit-0001", "lifetime_id": "T1", "amount": "2500000.00"})
        req = {"account_id": "T1:US_EXCHANGE", "instrument_id": "AAPL", "side": "BUY", "qty": 100, "order_type": "MARKET", "tif": "DAY",
               "request_id": "ui:recon-d3-aapl-0001"}
        self.ok("POST", "/api/orders", dict(req))
        before = self.state()
        navs = self.reconcile("D3 before restart")
        self.rt.close()
        self.rt = build(self.tmp)                                                                # the same home and database, a new process state
        self.assertEqual(before, self.state())
        self.assertEqual(navs, self.reconcile("D3 after restart"))
        # the first-run capital was never posted again: one init command per lifetime
        with self.rt.db.reader() as conn:
            self.assertEqual(4, conn.execute("SELECT COUNT(*) FROM commands WHERE request_id LIKE 'init:capital:%'").fetchone()[0])
        # the same request id after the restart answers the stored order (no second fill)
        status, out, headers = self.call("POST", "/api/orders", dict(req))
        self.assertEqual((200, "1"), (status, headers.get("X-Replayed")))
        self.assertEqual(before["fills"], self.state()["fills"])
        # a second server on the same home: the byte lock is refused while the first holds it (serve exits 3 on that)
        path = os.path.join(self.tmp, "terminal.lock")
        first, second = _server.InstanceLock(path), _server.InstanceLock(path)
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
        finally:
            first.release()
        self.assertTrue(second.acquire())
        second.release()

    def state(self) -> dict:
        with self.rt.db.reader() as conn:
            def rows(sql):
                return [tuple(r) for r in conn.execute(sql).fetchall()]
            return {"orders": rows("SELECT order_id, account_id, instrument_id, side, qty, filled_qty, status FROM orders ORDER BY order_id"),
                    "fills": rows("SELECT fill_id, account_id, instrument_id, qty, price, gross_c, fee_c FROM fills ORDER BY fill_id"),
                    "lots": rows("SELECT lot_id, account_id, instrument_id, qty_remaining, cost_c FROM lots ORDER BY lot_id"),
                    "transfers": rows("SELECT transfer_id, from_account, to_account, amount_c, kind FROM transfers ORDER BY transfer_id"),
                    "gl": rows("SELECT account_id, gl, currency, instrument_id, balance_c, base_c FROM gl_balances ORDER BY 1, 2, 3, 4"),
                    "entries": rows("SELECT COUNT(*) FROM journal_entries"), "settlements": rows("SELECT settle_id, status, due_date FROM settlements ORDER BY settle_id")}

    # ==== D10 + PB swaps with daily variation margin, through the API =================================================================
    @unittest.skipUnless(HAVE_SWAPS, "the swap modules are not in this build (left out of the release)")
    def test_d10_goldman_and_pb_swaps_with_daily_vm_through_the_api(self):
        rt = self.rt
        # onboarding the Fund Book with Goldman Sachs records the ISDA Master, the CSA and the PBA and opens F1:PB_GS
        out = self.ok("POST", "/api/books/F/onboard", {"request_id": "ui:recon-onboard-f-gs", "broker": "GS"})
        self.assertEqual(("F1:PB_GS", ["CSA", "ISDA_MASTER", "PBA"]), (out["account_id"], sorted(a["kind"] for a in out["agreements"])))
        self.reconcile("onboard F / GS")
        # D10: BUY 1,000 AAPL through Goldman: GS commission 1,000 x 0.0030 = 3.00 (above the 1.00 minimum); DVP: the payable and its T+1 settlement
        # row are in F1:PB_GS; the 339,500.00 + 3.00 = 339,503.00 shortfall is swept from F1:TREASURY; NAV(F1) = 25,000,000.00 - 3.00 = 24,999,997.00
        out = self.ok("POST", "/api/orders", {"request_id": "ui:recon-d10-aapl-0001", "account_id": "F1:PB_GS", "instrument_id": "AAPL", "side": "BUY", "qty": 1000,
                                              "order_type": "MARKET", "tif": "DAY"})
        self.assertEqual(("FILLED", "339500.00", "3.00", "339503.00"), (out["order"]["status"], out["fills"][0]["gross"], out["fills"][0]["fee"], out["sweeps"][0]["amount"]))
        with rt.db.reader() as conn:
            st = conn.execute("SELECT account_id, amount_c, due_date, status FROM settlements WHERE fill_id=?", (out["fills"][0]["fill_id"],)).fetchone()
        self.assertEqual(("F1:PB_GS", -33950300, "2026-10-21", "PENDING"), tuple(st))
        self.assertEqual(2499999700, self.reconcile("D10 Goldman buy")["F1"])
        risk = self.ok("GET", "/api/risk?scope=F1:ALL")
        pb = {r["account_id"]: r for r in risk["financing_liquidity"]["pb"]}
        self.assertEqual(("GS", "339500.00", "0.00"), (pb["F1:PB_GS"]["broker"], pb["F1:PB_GS"]["long_mv"], pb["F1:PB_GS"]["headroom"]))   # financing off -> no headroom
        # IRS 5y pay fixed 10,000,000 through F1:PB_GS (curve flat 4.00 %, module docstring): par 0.04000, dealt 0.04000 + 0.00010 = 0.04010;
        # mark0 = 10,000,000 x (0.04000 - 0.04010) x 4.491292505 = -4,491.292505 -> -4,491.29; DV01 +4,491.29; IM 4,491.29 x 50 = 224,564.50.
        # NAV(F1 is 24,999,997.00, below the 25,000,000.00 threshold) still books: the onboarding agreements keep the relationship open.
        p = self.ok("POST", "/api/otc/preview", {"account_id": "F1:PB_GS", "kind": "IRS", "terms": IRS_5Y})
        self.assertEqual(("0.04000", "0.04010", "-4491.29", "4491.29", "224564.50", "4.4912925050"), (p["par_rate"], p["dealt_rate"], p["mark0"], p["dv01"], p["im"], p["annuity"]))
        out = self.ok("POST", "/api/otc/contracts", {"request_id": "ui:recon-irs-0001", "account_id": "F1:PB_GS", "kind": "IRS", "terms": IRS_5Y})
        cid = out["contract_id"]
        self.assertEqual(("SWP-000001", "224564.50", "PAY_FIXED"), (cid, out["im_transfer"]["amount"], out["contract"]["side"]))
        self.assertEqual(2499550571, self.reconcile("IRS booked")["F1"])                        # 24,999,997.00 - 4,491.29 (the overlay)
        # TRS on MSFT (held nowhere) and a cross-currency swap (pay EUR 1,000,000.00 / receive USD) in the Institutional Book with JPMorgan
        self.ok("POST", "/api/books/I/onboard", {"request_id": "ui:recon-onboard-i-jpm", "broker": "JPM"})
        out = self.ok("POST", "/api/otc/contracts", {"request_id": "ui:recon-trs-0001", "account_id": "I1:PB_JPM", "kind": "TRS",
                                                     "terms": {"underlying_id": "MSFT", "units": 1000, "receiver": True, "reset_months": 1, "tenor_months": 12}})
        # notional 1,000 x 500.00 = 500,000.00; IM 10 % = 50,000.00; mark 0 at the reset price
        self.assertEqual(("SWP-000002", "500000.00", "50000.00", "RECEIVE_TOTAL_RETURN"), (out["contract_id"], out["preview"]["notional"], out["preview"]["im"],
                                                                                          out["contract"]["side"]))
        out = self.ok("POST", "/api/otc/contracts", {"request_id": "ui:recon-xccy-0001", "account_id": "I1:PB_JPM", "kind": "XCCY",
                                                     "terms": {"pay_ccy": "EUR", "recv_ccy": "USD", "pay_notional": "1000000.00", "recv_notional": None, "pay_rate": None,
                                                               "recv_rate": None, "pay_basis": "30/360", "recv_basis": "30/360", "months": 24, "mtm_reset": False}})
        # the USD notional at the deal rate: 1,000,000 x 1.1238 = 1,123,800.00; IM 2 % = 22,476.00; legs named by their coupons
        self.assertEqual(("SWP-000003", "1123800.00", "22476.00", "RECEIVE_USD_PAY_EUR"), (out["contract_id"], out["preview"]["usd_notional"], out["preview"]["im"],
                                                                                          out["contract"]["side"]))
        self.reconcile("TRS + XCCY booked")
        # the names those contracts observe without holding them: pinned at HELD by the quote worker, recorded by MARKS_RECORD
        with rt.db.reader() as conn:
            self.assertEqual({"MSFT": ["I1:PB_JPM"], "EURUSD": ["I1:PB_JPM"]}, otc_reference_ids(conn))
        rt.worker._scan_pins(rt.clock.epoch())
        self.assertTrue({"AAPL", "MSFT", "EURUSD"} <= set(rt.cache.pinned("HELD")), rt.cache.pinned("HELD"))
        # 17:10 ET: SWAP_VM through the jobs API (one job).  The IRS valued as of 10-20 on the 10-19 curve = mark0 -> we post 4,491.29 of VM collateral
        # (Dr 1320 / Cr 1010, a sweep of exactly that from F1:TREASURY); NAV unchanged: VM is collateral, the P&L is the overlay
        rt.clock.set("2026-10-20T17:10:00-04:00")
        out = self.ok("POST", "/api/jobs/run", {"request_id": "ui:recon-jobs-vm-1", "job": "SWAP_VM"})
        ran = {i["key"]: i["status"] for i in out["ran"]}
        self.assertEqual({"SWAP_VM"}, {i["job"] for i in out["ran"]})
        self.assertEqual("DONE", ran[f"{cid}:2026-10-20"])
        vm = self.ok("GET", f"/api/otc/contracts?account_id=F1:PB_GS&detail=1")[0]
        self.assertEqual(([("2026-10-20", "-4491.29", "-4491.29", "CTM")], "-4491.29"), ([(r["vm_date"], r["mark"], r["delta"], r["mode"]) for r in vm["history"]["vm"]],
                                                                                          vm["vm_balance"]))
        with rt.db.reader() as conn:
            lines = [(r["gl"], r["amount_c"]) for r in conn.execute("SELECT l.gl, l.amount_c FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id"
                                                                     " WHERE e.idem_key=? ORDER BY l.line_no", (f"swapvm:{cid}:2026-10-20",)).fetchall()]
        self.assertEqual([("1320", 449129), ("1010", -449129)], lines)
        self.assertEqual(2499550571, self.reconcile("VM day 1")["F1"])
        # Wed 10-21 17:10, the 10-20 curve flat 4.10 %: mark = 10,000,000 x (0.04100 - 0.04010) x 4.479565725 = 40,316.091525 -> 40,316.09;
        # delta 40,316.09 - (-4,491.29) = 44,807.38: one entry releases the 4,491.29 posted and receives 40,316.09 (a zero crossing);
        # NAV(F1) = 25,000,000.00 - 3.00 + 40,316.09 = 25,040,313.09
        rt.clock.set("2026-10-21T17:10:00-04:00")
        rt.curve.set_fixed_dfs(DFS_410, date(2026, 10, 20))
        out = self.ok("POST", "/api/jobs/run", {"request_id": "ui:recon-jobs-vm-2", "job": "SWAP_VM"})
        self.assertEqual("DONE", {i["key"]: i["status"] for i in out["ran"]}[f"{cid}:2026-10-21"])
        with rt.db.reader() as conn:
            lines = [(r["gl"], r["amount_c"]) for r in conn.execute("SELECT l.gl, l.amount_c FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id"
                                                                     " WHERE e.idem_key=? ORDER BY l.line_no", (f"swapvm:{cid}:2026-10-21",)).fetchall()]
        self.assertEqual([("1010", 449129), ("1320", -449129), ("1010", 4031609), ("2330", -4031609)], lines)
        self.assertEqual(2504031309, self.reconcile("VM day 2")["F1"])
        # the position row shows the overlay NAV carries (the mark at the last VM) as its mark, and the live model mark beside it: back on the 4.00 %
        # curve without a VM the live mark is -4,491.29 while the row (and NAV) keep 40,316.09 until the next VM
        rt.curve.set_fixed_dfs(DFS_400, date(2026, 10, 20))
        pos = rt.accounting.position("F1:PB_GS", cid)
        self.assertEqual((D("40316.09"), 4031609, D("-4491.29")), (pos.mark.mark, pos.mv_c, D(str(pos.extra["model_mark"]["value"]))))
        self.reconcile("curve moved back, no VM")

    # ==== Private Markets (owner: "spacex is in private markets for some reason") and the API wiring =================================
    def test_private_markets_lists_no_listed_company_and_the_api_wiring(self):
        # SpaceX (PUBLIC as SPCX from 2026-06-12), Cerebras (CBRS), xAI and Anysphere (acquired by SpaceX) are not private names any more
        gone = {"PRV-SPACEX", "PRV-CEREBRAS", "PRV-XAI", "PRV-ANYSPHERE"}
        out = self.ok("GET", "/api/private/companies?account_id=I1:PRIVATE")
        names = {c["instrument_id"] for c in out["companies"]}
        listed = {c["instrument_id"]: c["listing"]["message"] for c in out["listed"]}
        self.assertEqual((set(), gone), (names & gone, set(listed)))
        self.assertEqual("Listed 2026-06-12: SPCX on Nasdaq - trade it in US Exchange", listed["PRV-SPACEX"])
        self.assertEqual(21, len(names))                                                          # 25 researched companies - 4
        mk = self.ok("GET", "/api/accounts/I1:PRIVATE/markets")
        self.assertEqual((names, gone), ({r["instrument_id"] for r in mk["rows"]}, {r["instrument_id"] for r in mk["listed"]}))
        # jobs/run runs the one job named, or answers 400 for an unknown one
        out = self.ok("POST", "/api/jobs/run", {"request_id": "ui:recon-jobs-0001", "job": "SWAP_VM"})
        self.assertEqual(set(), {i["job"] for i in out["ran"]} - {"SWAP_VM"})
        status, body, _ = self.call("POST", "/api/jobs/run", {"request_id": "ui:recon-jobs-0002", "job": "NOPE"})
        self.assertEqual((400, "BAD_REQUEST"), (status, body["error"]["code"]))
        # bonds (WP-10, Build 2): the order ticket refuses a bond with the model-price sentence; POST /api/bonds/trade answers 400 UNSUPPORTED while the
        # build2_bonds setting is off and validates normally once it is on
        bond = sorted(i.instrument_id for i in self.rt.instruments.all() if i.kind == "BOND")[0]
        status, body, _ = self.call("POST", "/api/orders/preview", {"account_id": "T1:DEBT", "instrument_id": bond, "side": "BUY", "qty": 1000, "order_type": "MARKET", "tif": "DAY"})
        blockers = body.get("blockers") if status == 200 else [body.get("error")]
        self.assertTrue(any((b or {}).get("code") == "UNSUPPORTED" and "model price, not executable in this build" in (b or {}).get("message", "") for b in blockers), (status, body))
        trade = {"account_id": "T1:NOPE", "instrument_id": bond, "side": "BUY", "qty": 1000}
        status, body, _ = self.call("POST", "/api/bonds/trade", dict(trade, request_id="ui:recon-bond-0001"))
        self.assertEqual((400, "UNSUPPORTED"), (status, body["error"]["code"]))
        self.ok("PATCH", "/api/settings", {"request_id": "ui:recon-settings-0001", "build2_bonds": True})
        self.assertIs(True, self.ok("GET", "/api/settings")["build2_bonds"])
        status, body, _ = self.call("POST", "/api/bonds/trade", dict(trade, request_id="ui:recon-bond-0002"))
        self.assertEqual(400, status)
        self.assertNotEqual("UNSUPPORTED", body["error"]["code"])                                 # past the flag: the unknown account is the reason now
        self.reconcile("private + wiring")


if __name__ == "__main__":
    unittest.main()
