"""Display-units regression tests (2026-10-21 fix pass): money on the wire, rate units, the liquidity line, the clock.

Four defects seen on the live app:
(a) GET /api/treasury sent ``cash_by_ccy`` / ``capital_by_ccy`` as integer MINOR units next to ``capital`` in major units
    ("USD 100,000,000.00" on screen for a 1,000,000.00 Treasury); every money field must be a decimal string in major units.
(b) The Treasury page showed "SOFR 387.00 %": a percent (FRED quoting, 3.87) formatted as a fraction.  The units are
    defined once (``quotes/rates.py`` UNITS); every accrual divides the percent by 100 exactly once.
(c) A 1-share market buy resting with 0 cash showed "avail -347.29": available is now the own spendable cash floored at
    zero, with "reserved (Treasury sweep covers N)" beside it; the preview's funding block and the fill's sweep use the
    same figure.  Second pass (checker #2 regression): the floor must not reach a NEW order's fundability.  What the
    account's working orders already need beyond its own cash ("committed") comes off the Treasury / PB part of buying
    power (``accounting.buying_power_split``), so a second order that the Treasury cannot also pay for is never accepted
    WORKING and SUSPENDED at the open: since the FUNDS_WAIT hotfix (2026-10-21) it is queued SUSPENDED at submit,
    "waiting for funds", reserving nothing, and resumes when cash frees up; each fill still sweeps exactly its own
    shortfall.
(d) The header clock showed '--:--:--' until the first poll (checked statically: the JS cannot run here).

The world: the three real books (P 1,000,000.00 / F 25,000,000.00 / I 1,000,000,000.00) built by ``server.build_runtime``
offline (FakeProvider, FakeClock, a temp home, no network) on Tue 2026-10-20 16:30 ET (US equities closed; accounting day
10-20); SOFR 3.87 % observed 2026-10-19; AAPL last 339.50 observed two minutes earlier.  Every expected number is worked
by hand in the comment next to it.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import _Log
from finsim.terminal import config, server as _server
from finsim.terminal.clock import FakeClock
from finsim.terminal.quotes.providers import FakeProvider
from finsim.terminal.accounting import buying_power_split
from finsim.terminal.quotes.rates import RateStore, pct_to_fraction, treasury_cash_rate

D = Decimal
AT = "2026-10-20T16:30:00-04:00"            # Tuesday after the 16:00 close: a market order rests until Wed 09:30
SOFR_DATE = date(2026, 10, 19)
HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(os.path.dirname(HERE), "finsim", "terminal", "static")

# keys whose integer value would be a minor-unit leak: *_c (a codec miss), a currency / lifetime / account key of a cents
# dict, or a money field by name
MONEY_KEYS = {"nav", "available", "capital", "advances", "retained", "reserve", "encumbrance", "accrued", "settled", "pending", "receivable",
              "payable", "reserved", "spendable", "needs", "sweep_covers", "unfunded", "overdrawn", "held_at_lender", "amount", "gross", "fee", "net",
              "cost", "mv", "mv_base", "unrealised", "realised", "income", "financing", "fees", "native", "usd", "base", "debit", "credit", "balance",
              "treasury_available", "pb_headroom", "excess_liquidity", "accounts_available", "maintenance_shortfall", "assets", "liabilities",
              "cash", "transfers", "fx_result", "principal", "collateral", "available_usd", "reserved_usd", "needs_usd", "sweep_covers_usd",
              "unfunded_usd", "overdrawn_usd", "starting_capital", "pnl", "explained", "residual", "flows", "nav_0", "rounding", "shortfall"}
ID_KEY = re.compile(r"^([A-Z]{3}|[A-Z]\d+|[A-Z]\d+:[A-Z_]+)$")


def int_money_leaves(x, path: str = "", key: str = ""):
    """``[(path, int)]`` of every integer leaf that is money in minor units by the rules above."""
    out = []
    if isinstance(x, dict):
        for k, v in x.items():
            out += int_money_leaves(v, f"{path}.{k}", str(k))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            out += int_money_leaves(v, f"{path}[{i}]", key)
    elif isinstance(x, int) and not isinstance(x, bool):
        if key.endswith("_c") or key in MONEY_KEYS or ID_KEY.match(key):
            out.append((path, x))
    return out


def build(tmp: str, at: str = AT):
    os.environ[config.HOME_ENV] = tmp
    os.environ[config.DB_ENV] = os.path.join(tmp, "terminal.db")
    clock = FakeClock(at)
    fp = FakeProvider(clock)
    return _server.build_runtime(tmp, os.path.join(tmp, "terminal.db"), clock=clock, offline=True, fake_provider=fp, log=_Log(), books=None,
                                 fetch_threads=0, news_enabled=False)


def quote(rt, sym: str, last: str, age: int = 120, vol: int = 61900) -> None:
    """A Yahoo-style print in the FakeProvider, loaded into the cache by the inline worker (fetch_threads=0)."""
    now = rt.clock.epoch()
    qt = now - age
    rt.fp.set(sym, last=last, quote_time=qt, bar=(qt - qt % 60 - 60, vol))
    rt.cache.subscribe([sym], 300, "PAGE")
    rt.worker.tick()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-units-")
        self.rt = build(self.tmp)
        self.rt.rates.put("SOFR", [(SOFR_DATE, D("3.87"))], persist=False)
        quote(self.rt, "AAPL", "339.50")

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, method: str, path: str, body=None):
        status, out, headers = self.rt.router.handle(method, path, None, body, "127.0.0.1", None)
        return status, out, headers

    def ok(self, method: str, path: str, body=None):
        status, out, _ = self.call(method, path, body)
        self.assertEqual(200, status, f"{method} {path} -> {status} {out}")
        return out

    def assert_no_minor_units(self, path: str, body) -> None:
        leaks = int_money_leaves(body)
        self.assertEqual([], leaks, f"{path} carries integer minor units: {leaks[:8]}")


# ===== (a) money on the wire ===========================================================================================================
class MoneyOnTheWire(Base):
    def test_treasury_money_fields_are_major_unit_strings(self):
        t = self.ok("GET", "/api/treasury?book=P")
        tv = t["treasury"]
        # Personal Book: initial capital USD 1,000,000.00 = 100,000,000 cents -> "1000000.00" (the page showed 100,000,000.00)
        self.assertEqual({"USD": "1000000.00"}, tv["cash_by_ccy"])
        self.assertEqual({"USD": "1000000.00"}, tv["capital_by_ccy"])
        self.assertEqual(("1000000.00", "1000000.00", "1000000.00", "0.00"), (tv["capital"], tv["nav"], tv["available"], tv["advances"]))
        self.assertEqual({}, tv["advances_by_account"])
        self.assertNotIn("cash_by_ccy_c", tv)
        self.assert_no_minor_units("/api/treasury?book=P", t)
        # Fund Book: 25,000,000.00 (the page showed 2,500,000,000.00)
        f = self.ok("GET", "/api/treasury?book=F")
        self.assertEqual({"USD": "25000000.00"}, f["treasury"]["cash_by_ccy"])
        self.assertEqual({"USD": "25000000.00"}, f["treasury"]["capital_by_ccy"])
        self.assert_no_minor_units("/api/treasury?book=F", f)

    def test_transfer_advances_liquidity_and_every_view_in_major_units(self):
        out = self.ok("POST", "/api/transfers", {"from_account": "P1:TREASURY", "to_account": "P1:DEBT", "currency": "USD", "amount": "250000.00",
                                                 "request_id": "units:xfer-debt-0001"})
        self.assert_no_minor_units("POST /api/transfers", out)
        t = self.ok("GET", "/api/treasury?book=P")
        tv = t["treasury"]
        # Treasury 1,000,000.00 - 250,000.00 = 750,000.00 settled; advanced to P1:DEBT 250,000.00
        self.assertEqual({"USD": "750000.00"}, tv["cash_by_ccy"])
        self.assertEqual({"P1:DEBT": "250000.00"}, tv["advances_by_account"])
        self.assertEqual("250000.00", tv["advances"])
        self.assertEqual([{"account_id": "P1:DEBT", "name": t["advances"][0]["name"], "amount": "250000.00"}], t["advances"])
        # liquidity detail: Treasury available 750,000.00; P1:DEBT available 250,000.00 (no orders, no payables)
        self.assertEqual("750000.00", t["liquidity"]["detail"]["treasury"]["P1"])
        self.assertEqual("250000.00", t["liquidity"]["detail"]["accounts"]["P1:DEBT"])
        # excess liquidity = 750,000.00 + 250,000.00 + 0 (other accounts) - 0 = 1,000,000.00
        self.assertEqual("1000000.00", t["liquidity"]["excess_liquidity"])
        dep = self.ok("POST", "/api/treasury/deposit", {"amount": "1000.00", "currency": "USD", "book": "P", "request_id": "units:deposit-0001"})
        # 750,000.00 + 1,000.00 = 751,000.00 settled; capital 1,001,000.00
        self.assertEqual(({"USD": "751000.00"}, {"USD": "1001000.00"}), (dep["treasury"]["cash_by_ccy"], dep["treasury"]["capital_by_ccy"]))
        self.assert_no_minor_units("POST /api/treasury/deposit", dep)
        for path in ("/api/accounts?book=P", "/api/accounts/P1:DEBT", "/api/accounts/P1:DEBT/cash", "/api/accounts/P1:TREASURY", "/api/portfolio?scope=ALL&book=P",
                     "/api/portfolio?scope=ALL_BOOKS", "/api/accounting/balance-sheet?scope=ALL&book=P", "/api/accounting/journal?scope=ALL&book=P",
                     "/api/accounts/P1:DEBT/transactions", "/api/accounting/pnl?scope=ALL&book=P&period=ITD", "/api/books", "/api/state?book=P",
                     "/api/history/P1/portfolio", "/api/history/P1/accounting/balance-sheet", "/api/history/P1/accounting/journal"):
            self.assert_no_minor_units(path, self.ok("GET", path))
        det = self.ok("GET", "/api/accounts/P1:DEBT")
        # NAV by account: P1:DEBT 250,000.00
        self.assertEqual("250000.00", det["nav"]["by_account"]["P1:DEBT"])
        pf = self.ok("GET", "/api/portfolio?scope=ALL&book=P")
        # 751,000.00 Treasury + 250,000.00 Debt Instruments = 1,001,000.00
        self.assertEqual(("751000.00", "250000.00", "1001000.00"), (pf["nav"]["by_account"]["P1:TREASURY"], pf["nav"]["by_account"]["P1:DEBT"], pf["nav"]["nav"]))
        # transactions: net cash per currency as strings, no cents copy
        tx = self.ok("GET", "/api/accounts/P1:DEBT/transactions")
        self.assertEqual([{"USD": "250000.00"}], [r["net_cash"] for r in tx["rows"]])
        self.assertTrue(all("net_cash_c" not in r for r in tx["rows"]))

    def test_balance_sheet_rows_and_activity_text(self):
        bs = self.ok("GET", "/api/accounting/balance-sheet?scope=ALL&book=P")
        cash = [r for r in bs["rows"] if r["gl"] == "1010"]
        # one cash line in USD: 1,000,000.00 native = 1,000,000.00 USD
        self.assertEqual([("assets", "USD", "1000000.00", "1000000.00")], [(r["section"], r["currency"], r["native"], r["usd"]) for r in cash])
        self.assertTrue(all(isinstance(r["native"], (str, type(None))) and isinstance(r["usd"], (str, type(None))) for r in bs["rows"]))
        # memo: NAV 1,000,000.00 = capital 1,000,000.00 + advances 0.00 + retained 0.00 -> ties
        self.assertEqual(("1000000.00", "0.00", True), (bs["memo"]["capital"], bs["memo"]["fx_clearing"], bs["memo"]["ties"]))
        act = self.ok("GET", "/api/activity?limit=200")
        texts = [r["summary"] for r in act["rows"]]
        self.assertFalse([s for s in texts if "amount_c" in s], "activity text shows minor units")
        self.assert_no_minor_units("/api/activity", act)
        # the P1 initial capital: ONE row (P03 BM-25: the CAPITAL_DEPOSITED event is folded into its transfer row), the amount
        # 100,000,000 cents shown as "USD 1,000,000.00" in the text and in the detail table
        cap = [r for r in act["rows"] if r["type"] == "transfer" and "CAPITAL_DEPOSITED" in (r.get("events") or [])]
        self.assertEqual(1, len(cap), texts[:6])
        self.assertIn("USD 1,000,000.00", cap[0]["summary"])
        self.assertEqual("1,000,000.00", cap[0]["detail"]["amount"])
        self.assertFalse([r for r in act["rows"] if r["type"] == "event" and r.get("ref_id") == cap[0]["ref_id"]])   # no second row for it


# ===== (b) rate units ===================================================================================================================
class RateUnits(Base):
    def test_one_conversion_and_the_rate_helpers(self):
        self.assertEqual(D("0.0387"), pct_to_fraction(D("3.87")))                    # 3.87 / 100 = 0.0387
        self.assertEqual(D("0.0337"), treasury_cash_rate(D("3.87")))                 # 0.0387 - 0.0050 = 0.0337
        self.assertEqual(D("0"), treasury_cash_rate(D("0.25")))                      # 0.0025 - 0.0050 = -0.0025 -> floor 0
        with self.assertRaises(TypeError):
            pct_to_fraction(3.87)                                                    # floats never reach a rate
        rs = RateStore()
        rs.put("SOFR", [(SOFR_DATE, D("3.87"))], persist=False)
        self.assertEqual((SOFR_DATE, D("3.87")), rs.sofr())                          # published percent
        self.assertEqual((SOFR_DATE, D("0.0387")), rs.sofr_fraction())               # divided once: 3.87 / 100
        v = rs.view()
        self.assertEqual(("3.87", "percent"), (v["SOFR"]["value"], v["SOFR"]["unit"]))
        # every accrual-rate helper divides its index percent by 100 exactly once (hand: index / 100 + spread)
        from finsim.terminal import credit, lending_accruals, prime
        from finsim.terminal.products import private_credit
        self.assertEqual(D("0.0512"), prime.financing_rate(D("3.87"), "0.0125"))     # 0.0387 + 0.0125
        self.assertEqual(D("0.0312"), prime.credit_rate(D("3.87"), "0.0075"))        # 0.0387 - 0.0075
        self.assertEqual(D("0.0337"), lending_accruals.rebate_rate(D("3.87"), "GC"))  # max(0, 0.0387 - 0.0050)
        self.assertEqual(D("0.0275"), credit.loan_rate(D("2.00"), D("0.0075")))      # 0.0200 + 0.0075
        self.assertEqual(D("0.0987"), private_credit.coupon_rate(D("3.87"), "0.06"))  # 0.0387 + 0.0600

    def test_treasury_interest_block_and_rates_route(self):
        i = self.ok("GET", "/api/treasury?book=P")["interest"]
        # SOFR as published 3.87 (a percent); rate = max(0, 3.87 / 100 - 0.0050) = 0.0337 (a fraction) = 3.37 %
        self.assertEqual(("SOFR", "3.87", "2026-10-19", "0.0337", "3.37", "-0.0050", "0", 360, "fraction", "ok"),
                         (i["index"], i["index_pct"], i["as_of"], i["rate"], i["rate_pct"], i["spread"], i["floor"], i["basis"], i["unit"], i["status"]))
        r = self.ok("GET", "/api/rates")
        self.assertEqual(("3.87", "2026-10-19", "percent"), (r["SOFR"]["value"], r["SOFR"]["as_of"], r["SOFR"]["unit"]))

    def test_treasury_interest_without_a_print_is_never_defaulted(self):
        self.rt.rates = RateStore()                                                  # FRED not loaded yet
        i = self.ok("GET", "/api/treasury?book=P")["interest"]
        self.assertEqual((None, None, None), (i["index_pct"], i["rate"], i["as_of"]))
        # V104R2-BW-02 (e): the reason in trader words, no convention code (was "(RATE_LAG; never defaulted)"); still never a default rate
        self.assertIn("no rate is shown and no night accrues until it is", i["status"])
        self.assertIn("a rate is never assumed", i["status"])
        self.assertNotIn("RATE_LAG", i["status"])

    def test_93_61_one_night_of_treasury_cash_interest(self):
        rt = self.rt
        self.assertIsNotNone(rt.lifecycle, "the served runtime has no lifecycle scheduler")
        # Wed 00:35 ET: the night of Tue 10-20 accrues on the settled USD at the 17:00 close, SOFR print of 10-19:
        #   P1 1,000,000.00 x (0.0387 - 0.0050) / 360 = 1,000,000 x 0.0337 / 360 = 93.6111... -> 93.61
        #   F1 25,000,000.00 x 0.0337 / 360 = 2,340.2777... -> 2,340.28
        #   I1 1,000,000,000.00 x 0.0337 / 360 = 93,611.1111... -> 93,611.11
        rt.lifecycle.tick(at="2026-10-21T00:35:00-04:00", only="ACCRUE_DAILY")
        with rt.db.reader() as conn:
            got = {r["idem_key"]: (r["gl"], int(r["amount_c"])) for r in conn.execute(
                "SELECT e.idem_key, l.gl, l.amount_c FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.entry_id AND l.line_no=1"
                " WHERE e.idem_key LIKE 'accr:cashint:%'").fetchall()}
            memo = conn.execute("SELECT memo FROM journal_entries WHERE idem_key='accr:cashint:P1:USD:2026-10-20'").fetchone()["memo"]
            ev = conn.execute("SELECT payload_json FROM events WHERE type='ACCRUED' AND ref_id='P1'").fetchone()["payload_json"]
        self.assertEqual({"accr:cashint:P1:USD:2026-10-20": ("1230", 9361), "accr:cashint:F1:USD:2026-10-20": ("1230", 234028),
                          "accr:cashint:I1:USD:2026-10-20": ("1230", 9361111)}, got)
        self.assertIn("SOFR 3.87 % (2026-10-19) - 0.50 % = 3.37 %", memo)
        # the rate the Treasury page shows is the rate that accrued
        import json
        self.assertEqual("0.0337", json.loads(ev)["rate"])
        t = self.ok("GET", "/api/treasury?book=P")
        self.assertEqual(("0.0337", "93.61", "93.61"), (t["interest"]["rate"], t["interest"]["accrued"], t["treasury"]["accrued"]))
        self.assertTrue(rt.accounting.integrity("ledger").ok)


# ===== (c) available >= 0 with the reserved figure =======================================================================================
class AvailableFloor(Base):
    BUY = {"account_id": "P1:US_EXCHANGE", "instrument_id": "AAPL", "side": "BUY", "qty": 1, "order_type": "MARKET", "tif": "DAY"}

    def test_resting_market_buy_with_zero_cash(self):
        out = self.ok("POST", "/api/orders", dict(self.BUY, request_id="units:aapl-rest-0001"))
        self.assertEqual("WORKING", out["order"]["status"])                         # the session is closed: it rests until Wed 09:30
        # reserved = 1 x (339.50 x 1.02 = 346.29) + fee max(1 x 0.005, 1.00) = 1.00 -> 347.29; own cash 0.00
        row = next(r for r in self.ok("GET", "/api/accounts?book=P") if r["account_id"] == "P1:US_EXCHANGE")
        self.assertEqual(("0.00", "347.29", "347.29", "347.29", "0.00", "0.00"),
                         (row["available_usd"], row["reserved_usd"], row["needs_usd"], row["sweep_covers_usd"], row["unfunded_usd"], row["overdrawn_usd"]))
        for r in self.ok("GET", "/api/accounts?book=P"):
            self.assertGreaterEqual(D(r["available_usd"]), 0, r["account_id"])
        det = self.ok("GET", "/api/accounts/P1:US_EXCHANGE")
        c = det["cash"]["USD"]
        # spendable = 0 + 0 - 0 - 347.29 = -347.29 (signed, shown only split); available = max(0, -347.29) = 0.00
        self.assertEqual(("0.00", "0.00", "347.29", "-347.29", "347.29", "347.29", "0.00", "0.00"),
                         (c["settled"], c["available"], c["reserved"], c["spendable"], c["needs"], c["sweep_covers"], c["unfunded"], c["overdrawn"]))
        self.assertIn("never shown below zero", c["definition"])
        bp = det["buying_power"]["USD"]
        # buying power = what a NEW order can draw: the resting order's 347.29 beyond own cash is committed to the Treasury sweep:
        #   available max(0, -347.29) = 0.00; committed 347.29; Treasury 1,000,000.00 - 347.29 = 999,652.71 left; PB 0
        #   total = 0.00 + 999,652.71 + 0.00 = 999,652.71 = max(0, -347.29 + 1,000,000.00 + 0)
        self.assertEqual(("0.00", "347.29", "1000000.00", "999652.71", "0.00", "999652.71"),
                         (bp["available"], bp["committed"], bp["treasury_gross"], bp["treasury_available"], bp["pb_headroom"], bp["total"]))
        self.assertIn("committed", bp["formula"])
        self.assertEqual("0.00", det["account"]["available_usd"])
        self.assertIn("never shown below zero", det["available_definition"])
        # the ticket preview's funding block uses the same figures: available 0.00, need 339.50 + 1.00 = 340.50 = shortfall = sweep-in
        # (what this order's fill will move); Treasury available for it 999,652.71 (after the first order's 347.29) -> ok
        pv = self.ok("POST", "/api/orders/preview", dict(self.BUY))
        f = pv["funding"]
        self.assertEqual(("0.00", "0.00", "347.29", "340.50", "340.50", "340.50", "999652.71", True),
                         (f["settled"], f["available"], f["reserved"], f["need"], f["shortfall"], f["sweep_in"], f["treasury_available"], f["ok"]))

    def test_each_fill_sweeps_exactly_its_own_shortfall(self):
        rt = self.rt
        o1 = self.ok("POST", "/api/orders", dict(self.BUY, request_id="units:aapl-rest-0001"))["order"]["order_id"]
        pv = self.ok("POST", "/api/orders/preview", dict(self.BUY))
        self.assertEqual("340.50", pv["funding"]["sweep_in"])
        o2 = self.ok("POST", "/api/orders", dict(self.BUY, request_id="units:aapl-rest-0002"))["order"]["order_id"]
        # Wed 09:35 ET: a fresh 339.50 print (09:34:30, inside the session); the matcher fills both market orders at 339.50
        rt.clock.set("2026-10-21T09:35:00-04:00")
        quote(rt, "AAPL", "339.50", age=30)
        rt.matcher.sweep()
        with rt.db.reader() as conn:
            st = {r["order_id"]: r["status"] for r in conn.execute("SELECT order_id, status FROM orders").fetchall()}
            sweeps = [(r["linked_type"], int(r["amount_c"])) for r in conn.execute(
                "SELECT linked_type, amount_c FROM transfers WHERE kind='SWEEP_IN' AND to_account='P1:US_EXCHANGE' ORDER BY transfer_id").fetchall()]
        self.assertEqual({o1: "FILLED", o2: "FILLED"}, st)
        # each fill needs 339.50 + 1.00 = 340.50 against available max(0, 0 - the other order's 347.29) = 0.00 -> one sweep of
        # exactly 340.50 per fill, as previewed (no pre-funding of the other order's reservation: 2 x 340.50 = 681.00 in all)
        self.assertEqual([("fill", 34050), ("fill", 34050)], sweeps)
        c = self.ok("GET", "/api/accounts/P1:US_EXCHANGE")["cash"]["USD"]
        # settled 681.00, payable 681.00 (T+1), nothing reserved -> available 0.00, nothing beyond own cash
        self.assertEqual(("681.00", "681.00", "0.00", "0.00", "0.00"), (c["settled"], c["payable"], c["reserved"], c["available"], c["needs"]))
        self.assertTrue(rt.accounting.integrity("ledger").ok)


# ===== (c, second pass) a NEW order counts what the account already committed beyond its own cash ======================================
class NewOrderFundability(Base):
    """Checker #2 reproduced the floor leaking into the pre-trade check: with ``available_c`` floored, a second order was
    checked against the whole Treasury although the first order's reservation would sweep part of it, was accepted, and
    was SUSPENDED at the open.  The rule now (``buying_power_split``): fundable iff max(0, need - available) <= Treasury
    left + PB left, where left = after the account's commitments (= need - signed spendable <= Treasury + PB); an order
    that is not fundable is queued SUSPENDED (FUNDS_WAIT) and reserves nothing."""
    MKT = {"account_id": "P1:US_EXCHANGE", "instrument_id": "AAPL", "side": "BUY", "order_type": "MARKET", "tif": "DAY", "confirm_large": True}
    LMT = {"account_id": "P1:US_EXCHANGE", "instrument_id": "AAPL", "side": "BUY", "qty": 1, "order_type": "LIMIT", "tif": "DAY"}

    def submit(self, body: dict, rid: str):
        return self.call("POST", "/api/orders", dict(body, request_id=rid))

    def statuses(self):
        with self.rt.db.reader() as conn:
            st = {r["order_id"]: r["status"] for r in conn.execute("SELECT order_id, status FROM orders ORDER BY order_id").fetchall()}
            sweeps = [int(r["amount_c"]) for r in conn.execute("SELECT amount_c FROM transfers WHERE kind='SWEEP_IN' AND to_account='P1:US_EXCHANGE'"
                                                                " ORDER BY transfer_id").fetchall()]
            suspended = conn.execute("SELECT COUNT(*) FROM attention WHERE code='ORDER_SUSPENDED'").fetchone()[0]
        return st, sweeps, int(suspended)

    def test_second_order_beyond_treasury_after_commitments_queues_without_reserving(self):
        a = self.submit(dict(self.MKT, qty=1700), "units:big-a-0001")
        self.assertEqual((200, "WORKING"), (a[0], a[1]["order"]["status"]))       # closed session: rests until Wed 09:30
        bp = self.ok("GET", "/api/accounts/P1:US_EXCHANGE")["buying_power"]["USD"]
        # reserved = 1,700 x (339.50 x 1.02 = 346.29) = 588,693.00 + fee 1,700 x 0.005 = 8.50 -> 588,701.50, all beyond own cash 0.00
        # Treasury left = 1,000,000.00 - 588,701.50 = 411,298.50 = buying power (available 0.00, PB 0.00)
        self.assertEqual(("0.00", "588701.50", "1000000.00", "411298.50", "411298.50"),
                         (bp["available"], bp["committed"], bp["treasury_gross"], bp["treasury_available"], bp["total"]))
        # B: 1,300 x 339.50 = 441,350.00 + fee 6.50 = 441,356.50 > 411,298.50 -> not fundable (the floored check said ok and sweep 441,356.50)
        f = self.ok("POST", "/api/orders/preview", dict(self.MKT, qty=1300))["funding"]
        self.assertEqual(("0.00", "588701.50", "441356.50", "441356.50", "411298.50", "411298.50", False),
                         (f["available"], f["reserved"], f["need"], f["shortfall"], f["sweep_in"], f["treasury_available"], f["ok"]))
        # FUNDS_WAIT (hotfix 2026-10-21): B is not refused - it is accepted SUSPENDED, waiting for funds, and reserves nothing, so the
        # buying power is still 411,298.50 (it is never accepted WORKING and suspended at the open, the regression this test guards)
        st, out, _h = self.submit(dict(self.MKT, qty=1300), "units:big-b-0001")
        self.assertEqual((200, "SUSPENDED", "waiting for funds: needs USD 441,356.50, available USD 411,298.50"), (st, out["order"]["status"], out["order"]["reason"]))
        bp = self.ok("GET", "/api/accounts/P1:US_EXCHANGE")["buying_power"]["USD"]
        self.assertEqual(("588701.50", "411298.50", "411298.50"), (bp["committed"], bp["treasury_available"], bp["total"]))
        # B': 1,100 x 339.50 = 373,450.00 + fee 5.50 = 373,455.50 <= 411,298.50 -> accepted; its reservation 1,100 x 346.29 = 380,919.00
        # + 5.50 = 380,924.50 -> committed 588,701.50 + 380,924.50 = 969,626.00 -> buying power 1,000,000.00 - 969,626.00 = 30,374.00
        f = self.ok("POST", "/api/orders/preview", dict(self.MKT, qty=1100))["funding"]
        self.assertEqual(("373455.50", "373455.50", "411298.50", True), (f["need"], f["sweep_in"], f["treasury_available"], f["ok"]))
        b = self.submit(dict(self.MKT, qty=1100), "units:big-b2-0001")
        self.assertEqual((200, "WORKING"), (b[0], b[1]["order"]["status"]))
        bp = self.ok("GET", "/api/accounts/P1:US_EXCHANGE")["buying_power"]["USD"]
        self.assertEqual(("969626.00", "30374.00", "30374.00"), (bp["committed"], bp["treasury_available"], bp["total"]))
        # Wed 09:35 ET, a fresh 339.50 print: A and B' fill and each sweeps exactly its own need -- A 1,700 x 339.50 + 8.50 = 577,158.50,
        # B' 373,455.50 -- nothing working is suspended; Treasury 1,000,000.00 - 577,158.50 - 373,455.50 = 49,386.00; the queued B
        # (441,356.50) still does not fit and keeps waiting
        self.rt.clock.set("2026-10-21T09:35:00-04:00")
        quote(self.rt, "AAPL", "339.50", age=30)
        self.rt.matcher.sweep()
        st, sweeps, suspended = self.statuses()
        self.assertEqual(({"O-000001": "FILLED", "O-000002": "SUSPENDED", "O-000003": "FILLED"}, [57715850, 37345550], 0), (st, sweeps, suspended))
        self.assertEqual({"USD": "49386.00"}, self.ok("GET", "/api/treasury?book=P")["treasury"]["cash_by_ccy"])
        self.assertTrue(self.rt.accounting.integrity("ledger").ok)

    def test_the_boundary_is_exact_to_the_cent(self):
        # Treasury 1,000,000.00 - 999,338.00 moved to P1:DEBT = 662.00
        self.ok("POST", "/api/transfers", {"from_account": "P1:TREASURY", "to_account": "P1:DEBT", "currency": "USD", "amount": "999338.00",
                                           "request_id": "units:xfer-boundary-0001"})
        a = self.submit(dict(self.LMT, limit_px="330.00"), "units:lmt-a-0001")
        self.assertEqual((200, "WORKING"), (a[0], a[1]["order"]["status"]))
        # LIMIT reservation = 1 x 330.00 + fee max(0.005, 1.00) = 331.00 (own cash 0.00) -> committed 331.00; Treasury left 662.00 - 331.00 = 331.00
        bp = self.ok("GET", "/api/accounts/P1:US_EXCHANGE")["buying_power"]["USD"]
        self.assertEqual(("331.00", "662.00", "331.00", "331.00"), (bp["committed"], bp["treasury_gross"], bp["treasury_available"], bp["total"]))
        # one cent over: 330.01 + 1.00 = 331.01 > 331.00 -> not fundable: queued SUSPENDED (FUNDS_WAIT), reserving nothing
        f = self.ok("POST", "/api/orders/preview", dict(self.LMT, limit_px="330.01"))["funding"]
        self.assertEqual(("331.01", "331.00", False, True), (f["need"], f["treasury_available"], f["ok"], f["queued"]))
        q = self.submit(dict(self.LMT, limit_px="330.01"), "units:lmt-b-0001")
        self.assertEqual((200, "SUSPENDED", "waiting for funds: needs USD 331.01, available USD 331.00"), (q[0], q[1]["order"]["status"], q[1]["order"]["reason"]))
        # exactly at it: 330.00 + 1.00 = 331.00 <= 331.00 -> accepted (shortfall = sweep-in = 331.00)
        f = self.ok("POST", "/api/orders/preview", dict(self.LMT, limit_px="330.00"))["funding"]
        self.assertEqual(("331.00", "331.00", "331.00", True), (f["need"], f["shortfall"], f["sweep_in"], f["ok"]))
        b = self.submit(dict(self.LMT, limit_px="330.00"), "units:lmt-b-0002")
        self.assertEqual((200, "WORKING"), (b[0], b[1]["order"]["status"]))
        # now committed 331.00 x 2 = 662.00 = Treasury -> buying power 0.00; the sidebar: available 0.00, reserved 662.00, all covered
        bp = self.ok("GET", "/api/accounts/P1:US_EXCHANGE")["buying_power"]["USD"]
        self.assertEqual(("662.00", "0.00", "0.00"), (bp["committed"], bp["treasury_available"], bp["total"]))
        row = next(r for r in self.ok("GET", "/api/accounts?book=P") if r["account_id"] == "P1:US_EXCHANGE")
        self.assertEqual(("0.00", "662.00", "662.00", "0.00"), (row["available_usd"], row["reserved_usd"], row["sweep_covers_usd"], row["unfunded_usd"]))
        q = self.submit(dict(self.LMT, limit_px="330.00"), "units:lmt-c-0001")
        self.assertEqual((200, "SUSPENDED", "waiting for funds: needs USD 331.00, available USD 0.00"), (q[0], q[1]["order"]["status"], q[1]["order"]["reason"]))
        # Wed 09:35 ET, AAPL 330.00: both working limits fill at 330.00; each sweeps its own 330.00 + 1.00 = 331.00; Treasury 662.00 - 662.00 = 0.00;
        # the two queued limits (each 331.00 at the executable 330.00) find nothing left and keep waiting
        self.rt.clock.set("2026-10-21T09:35:00-04:00")
        quote(self.rt, "AAPL", "330.00", age=30)
        self.rt.matcher.sweep()
        st, sweeps, suspended = self.statuses()
        self.assertEqual((["FILLED", "FILLED", "SUSPENDED", "SUSPENDED"], [33100, 33100], 0), (sorted(st.values()), sweeps, suspended))
        self.assertEqual({"USD": "0.00"}, self.ok("GET", "/api/treasury?book=P")["treasury"]["cash_by_ccy"])
        self.assertTrue(self.rt.accounting.integrity("ledger").ok)


class ActivityInWords(Base):
    """1.0.4 leftovers V104R2-OTC-03 / BW-01 / BW-03 (P3): the Activity rows the owner's lifecycle writes - futures VM, the nightly accruals
    and re-accruals, NAV snapshots, a skipped job - read in words (accounts by name, keys split, money as text), never the raw ids
    'F1:US_EXCHANGE:ZNZ26:2026-10-27' or a payload of cents.  The events are the owner's own shapes (owner copy 2026-10-28 10:04)."""

    EVENTS = [
        ("FUTURES_VM", "F1:US_EXCHANGE", "ZNZ26", "vm", "F1:US_EXCHANGE:ZNZ26:2026-10-27",
         {"date": "2026-10-27", "entry_id": 738, "funded": True, "method": "observed print at/before the settle time", "qty_after": -19, "qty_prev": -19,
          "settle_px": "106.078125", "vm_c": 89062}),
        ("ACCRUED", "F1:TREASURY", None, "treasury", "F1",
         {"amount_c": 47151, "base_c": 494877057, "entry_id": 757, "kind": "cashint", "night": "2026-10-27", "obs_date": "2026-10-26", "rate": "0.0343", "sofr": "3.93"}),
        ("NAV_SNAPSHOT", "F1:US_EXCHANGE", None, "snapshot", "F1:US_EXCHANGE:2026-10-26",
         {"flows_day_c": 0, "late": False, "nav_c": 0, "partial": 0, "pnl_day_c": 0, "scope": "F1:US_EXCHANGE", "snap_date": "2026-10-26"}),
        ("REACCRUED", "F1:US_EXCHANGE", None, "account", "F1:US_EXCHANGE",
         {"base_c": 1000000, "batch_id": "IMP-F1-20261027-A", "business_date": "2026-10-28", "correction_c": 88, "entry_id": 863, "kind": "pbcredit",
          "night": "2026-10-27", "posted_c": 0, "rate": "0.0318", "restated_c": 88}),
        ("JOB_SKIPPED", "F1:US_EXCHANGE", "GCZ26", "job", "GCZ26:2026-10-26",
         {"code": "CLOSE_PENDING", "job": "FUTURES_VM", "message": "GCZ26: no settle price for 2026-10-26 yet; the variation margin waits for it"}),
    ]

    def test_lifecycle_rows_read_in_words(self):
        import json
        from finsim.terminal.api_views import activity_rows
        rt = self.rt
        with rt.db.writer() as tx:
            cid = tx.conn.execute("SELECT MIN(id) FROM commands").fetchone()[0]
            for i, (typ, acct, iid, rtype, rid, pl) in enumerate(self.EVENTS):
                tx.conn.execute("INSERT INTO events(command_id, ts, type, account_id, instrument_id, ref_type, ref_id, payload_json) VALUES (?,?,?,?,?,?,?,?)",
                                (cid, rt.clock.epoch() + i, typ, acct, iid, rtype, rid, json.dumps(pl)))
        rows = {r["event"]: r for r in activity_rows(rt, types={"financing", "other", "system"}, lifetime_ids=["F1"]) if r.get("type") == "event"
                and r["event"] in {e[0] for e in self.EVENTS}}
        self.assertEqual({e[0] for e in self.EVENTS}, set(rows))
        vm, acc, snap, reacc, skip = (rows[k] for k in ("FUTURES_VM", "ACCRUED", "NAV_SNAPSHOT", "REACCRUED", "JOB_SKIPPED"))
        # the reference in words: the row's own account left out, the key split ('vm F1:US_EXCHANGE:ZNZ26:2026-10-27' before)
        self.assertEqual(("futures VM - variation margin ZNZ26 2026-10-27", "variation margin ZNZ26 2026-10-27"), (vm["summary"], vm["ref_text"]))
        self.assertEqual(("financing", "890.62"), (vm["category"], vm["detail"]["vm"]))          # vm_c 89,062 cents -> 890.62
        # Treasury cash interest: 4,948,770.57 x 0.0343 / 360 = 471.5079 -> 471.51 (amount_c 47,151); the rate a percent, never 0.0343
        self.assertEqual("accrued - Treasury: Treasury cash interest, night 2026-10-27, amount 471.51", acc["summary"])
        self.assertEqual(("4,948,770.57", "3.43 %", "Treasury cash interest"), (acc["detail"]["base"], acc["detail"]["rate"], acc["detail"]["kind"]))
        self.assertEqual("PB credit interest", reacc["detail"]["kind"])                       # 'pbcredit' in words in the row dialog too
        self.assertEqual(("NAV snapshot - snapshot 2026-10-26", "US Exchange"), (snap["summary"], snap["detail"]["scope"]))
        # a re-accrual of the restatement: the correction 0.88 (88 cents), its night, the kind in words
        self.assertEqual("re-accrued: PB credit interest, night 2026-10-27, correction 0.88", reacc["summary"])
        # a skipped job: which job, on what and why, under "system"
        self.assertEqual(("system", "futures VM skipped for GCZ26 2026-10-26: no settle price for 2026-10-26 yet; the variation margin waits for it"),
                         (skip["category"], skip["summary"]))
        for r in rows.values():
            text = " ".join([r["summary"], r.get("ref_text") or ""] + [str(v) for v in r["detail"].values()])
            self.assertNotIn("F1:", text, r["summary"])
            self.assertNotRegex(text, r"\b[A-Z]+_[A-Z_]+\b", r["summary"])                   # no internal code (US_EXCHANGE, CLOSE_PENDING)
            self.assertFalse([k for k in r["detail"] if k.endswith("_c") or "_" in k], r["detail"])  # keys in words, no cents keys

    def test_attention_row_dialog_in_words(self):
        """V104R2-BW-03 remnant (the integrator, 1.0.6): an attention item's row dialog said "code CLOSE_PENDING" and showed the stored message
        with raw account ids; the code and the message now read as the row's summary does (the stored item is unchanged)."""
        from finsim.terminal.api_views import activity_rows
        rt = self.rt
        msg = "CDX-000001: the variation margin of 2026-10-27 waits for that day's OAS; F1:TREASURY sweeps it when it posts"
        with rt.db.writer() as tx:
            tx.conn.execute("INSERT INTO attention(ts, severity, source, code, message, details_json, account_id, ref_type, ref_id, resolved_ts, auto_resolve)"
                            " VALUES (?,?,?,?,?,?,?,?,?,NULL,1)", (rt.clock.epoch(), "WARN", "swaps", "CLOSE_PENDING", msg, "{}", "F1:US_EXCHANGE", "otc", "k1"))
        rows = [r for r in activity_rows(rt, types={"attention"}, lifetime_ids=["F1"]) if r.get("type") == "attention" and r["detail"].get("source") == "swaps"]
        self.assertEqual(1, len(rows))
        d = rows[0]["detail"]
        self.assertEqual(("close pending", "CDX-000001: the variation margin of 2026-10-27 waits for that day's OAS; Treasury sweeps it when it posts"),
                         (d["code"], d["message"]))
        self.assertEqual(f"close pending: {d['message']}", rows[0]["summary"])
        with rt.db.reader() as conn:                                                   # the stored item keeps its code and text
            self.assertEqual(("CLOSE_PENDING", msg), tuple(conn.execute("SELECT code, message FROM attention WHERE ref_id='k1'").fetchone()))

    def test_ledger_memos_in_words(self):
        """V104R2-BW-02 (a): the Accounting Transactions / Ledger memos the owner's entries carry (owner copy 2026-10-28), shown in
        words; the stored memo is unchanged (it stays the tooltip).  A group with no convention key - a dealer code '(GS)' - stays."""
        from finsim.terminal.api_views import readable
        self.assertEqual("SELL 17 ZNZ26 @ 105.3125, fee 38.25; initial margin +44,200.00 (2 -> 19 contracts x 2,600.00; see Futures margin)",
                         readable("SELL 17 ZNZ26 @ 105.3125, fee 38.25; initial margin +44,200.00 (2 -> 19 contracts x 2,600.00; FUT_MARGIN)"))
        self.assertEqual("period 2026-10-21..2027-10-21 (see Accrual, Rate lag, compounded in arrears)",
                         readable("period 2026-10-21..2027-10-21 (ACCRUAL, RATE_LAG, COMPOUND_ARREARS)"))
        self.assertEqual("initial margin +44,200.00 (2 -> 19 contracts x 2,600.00; see Futures margin) for fill F-000006",
                         readable("initial margin +44,200.00 (2 -> 19 contracts x 2,600.00; FUT_MARGIN) for fill F-000006"))   # the fill's margin row
        self.assertEqual("initial margin posted (static table; see Futures margin)", readable("initial margin posted (static table; FUT_MARGIN)"))   # a line memo
        self.assertEqual("sell short 8,500 DLR @ 175.205", readable("SELL_SHORT 8,500 DLR @ 175.205"))
        # the import's re-accrual memo: the accrual kind and the account in words (the owner's entry 863)
        self.assertEqual("re-accrual PB credit interest Goldman Sachs (PB) night of 2026-10-27",
                         readable("re-accrual pbcredit F1:PB_GS night of 2026-10-27", {"F1:PB_GS": "Goldman Sachs (PB)"}))
        self.assertEqual("IRS pay fixed 4.8100 % 7,500,000.00 USD (GS)", readable("IRS pay fixed 4.8100 % 7,500,000.00 USD (GS)"))


class BuyingPowerSplit(unittest.TestCase):
    """``accounting.buying_power_split(spendable, Treasury available, PB headroom)`` in cents, worked by hand."""

    def test_hand_worked_cases(self):
        keys = ("available_c", "committed_c", "treasury_gross_c", "treasury_available_c", "pb_headroom_gross_c", "pb_headroom_c", "total_c")
        # cash 1,000.00, a working order reserving 600.00 -> spendable +400.00: nothing committed; 400.00 + Treasury 1,000.00 = 1,400.00
        self.assertEqual((40000, 0, 100000, 100000, 0, 0, 140000), tuple(buying_power_split(40000, 100000, 0)[k] for k in keys))
        # spendable -347.29 (the resting 1-share buy), Treasury 1,000,000.00: committed 347.29 -> left 999,652.71 = total
        self.assertEqual((0, 34729, 100000000, 99965271, 0, 0, 99965271), tuple(buying_power_split(-34729, 100000000, 0)[k] for k in keys))
        # a PB account with financing on: committed 50,000.00 takes Treasury's 30,000.00 first, then 20,000.00 of the 169,750.00 headroom
        #   -> Treasury left 0.00, PB left 149,750.00, total 149,750.00 = -50,000 + 30,000 + 169,750
        self.assertEqual((0, 5000000, 3000000, 0, 16975000, 14975000, 14975000), tuple(buying_power_split(-5000000, 3000000, 16975000)[k] for k in keys))
        # committed 500.00 beyond Treasury 100.00 + PB 200.00 -> nothing left: total max(0, -500 + 100 + 200) = 0.00
        self.assertEqual((0, 50000, 10000, 0, 20000, 0, 0), tuple(buying_power_split(-50000, 10000, 20000)[k] for k in keys))
        # a reserve above Treasury's cash (Treasury available -50.00) is shown as such and counts as 0.00
        self.assertEqual((0, 0, -5000, 0, 0, 0, 0), tuple(buying_power_split(0, -5000, 0)[k] for k in keys))

    def test_the_preview_rule_equals_the_signed_rule(self):
        # orders_preview: shortfall = max(0, N - available); sweep = min(shortfall, Treasury left); pb = min(rest, PB left); ok iff nothing
        # is left over.  For every need N > 0 it must equal "N - spendable <= max(0, Treasury) + max(0, PB)" (the check before the floor),
        # and the total must be max(0, spendable + max(0, Treasury) + max(0, PB)), for every combination below (cents).  N = 0 (a sale) is
        # always fundable: it needs no cash (the signed check refused a SELL whenever the commitments exceeded Treasury + PB).
        for s in range(-300, 301, 7):
            for t in (-50, 0, 120, 400):
                for p in (0, 90, 250):
                    b = buying_power_split(s, t, p)
                    self.assertEqual(max(0, s + max(0, t) + max(0, p)), b["total_c"], (s, t, p))
                    self.assertEqual(0, max(0, 0 - b["available_c"]), (s, t, p))
                    for n in range(1, 700, 13):
                        shortfall = max(0, n - b["available_c"])
                        sweep = min(shortfall, b["treasury_available_c"])
                        pbf = min(shortfall - sweep, b["pb_headroom_c"])
                        self.assertEqual(n - s <= max(0, t) + max(0, p), shortfall - sweep - pbf <= 0, (s, t, p, n))


# ===== (d) the header clock (static: the JS cannot run here) =============================================================================
class ClockAndFormatters(unittest.TestCase):
    def read(self, rel: str) -> str:
        with open(os.path.join(STATIC, rel), "r", encoding="utf-8") as f:
            return f.read()

    def test_clock_renders_at_once_and_sync_parses_iso(self):
        hdr = self.read(os.path.join("components", "header.js"))
        tail = hdr[hdr.rindex("T.components.header"):]
        self.assertIn("startClock();", tail)                                         # called when the file loads, not only at boot
        self.assertIn("Date.now() / 1000", hdr[hdr.index("function tickClock"):hdr.index("function startClock")])   # browser fallback
        core = self.read("core.js")
        sync = core[core.index("sync: function"):core.index("now: function", core.index("sync: function"))]
        self.assertIn("Date.parse", sync)                                            # clock.utc is an ISO string
        self.assertIn("isFinite(s)", sync)                                           # never a NaN offset

    def test_rate_formatters_are_explicit(self):
        core = self.read("core.js")
        self.assertIn("ratePct: function", core)
        self.assertIn("liquidity: function", core)
        trs = self.read(os.path.join("pages", "treasury.js"))
        self.assertIn("T.fmt.ratePct(i.index_pct)", trs)                             # SOFR as published is a percent

    def test_buying_power_cell_names_the_commitment(self):
        core = self.read("core.js")
        bp = core[core.index("buyingPower: function"):core.index("BUYING_POWER_DEF:")]
        for field in ("'committed'", "'treasury_available'", "'treasury_gross'", "'total'"):
            self.assertIn(field, bp)                                                 # every figure is the server's
        hdr = self.read(os.path.join("components", "header.js"))
        self.assertIn("T.fmt.buyingPower(bp)", hdr)


if __name__ == "__main__":
    unittest.main()
