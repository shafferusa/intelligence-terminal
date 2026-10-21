"""WP-05 FX spot tests (TERMINAL_DESIGN.md §13.5, §13.6, §19.6; D9).  Every expected number is worked by hand in a comment;
nothing here is pasted from the code's output.  No network: FixedMarks, FakeClock, a temp database.

The loan accruals use a LOCAL COPY of WP-08's ``lifecycle.accrue_segment`` (§11.3, same signature) injected as
``rt.lifecycle`` so the package runs before WP-08 is merged (TERMINAL_WORKPLAN.md WP-05 "Consumes")."""
from __future__ import annotations

import json
import shutil
import tempfile
import types
import unittest
from datetime import date
from decimal import Decimal

from tests.terminal_helpers import CREDIT, FOREIGN_FX, T1, TREASURY, US_EXCHANGE, assert_integrity, bal, deposit, execute, fund, make_runtime
from finsim.terminal import credit, fx
from finsim.terminal.quotes.rates import RateStore
from finsim.terminal.units import qprice, to_c


# ---- the local accrue_segment (WP-08's §11.3 helper, same signature) ------------------------------------------------------------
def accrue_segment(ctx, kind, ref, base_c, rate, basis, night) -> int:
    """n = night − segment_start + 1; cum_new = to_c(base × rate × n / basis); posted = cum_new − cum; the segment
    restarts (cum 0) when base, rate or basis differ from the stored row."""
    night = night if isinstance(night, date) else date.fromisoformat(str(night))
    conn = ctx.conn
    rate_s = str(qprice(rate))
    row = conn.execute("SELECT * FROM accrual_state WHERE kind=? AND ref_id=?", (kind, ref)).fetchone()
    restart = row is None or int(row["base_c"]) != int(base_c) or row["rate"] != rate_s or int(row["day_basis"]) != int(basis)
    if restart:
        seg_start, cum = night, 0
    else:
        seg_start, cum = date.fromisoformat(row["segment_start"]), int(row["cum_c"])
        if row["accrued_through"] and night <= date.fromisoformat(row["accrued_through"]):
            return 0
    n = (night - seg_start).days + 1
    cum_new = to_c(Decimal(int(base_c)) / Decimal(100) * Decimal(str(rate)) * Decimal(n) / Decimal(int(basis)))
    conn.execute("INSERT INTO accrual_state(kind, ref_id, segment_start, base_c, rate, day_basis, accrued_through, cum_c) VALUES (?,?,?,?,?,?,?,?)"
                 " ON CONFLICT(kind, ref_id) DO UPDATE SET segment_start=excluded.segment_start, base_c=excluded.base_c, rate=excluded.rate,"
                 " day_basis=excluded.day_basis, accrued_through=excluded.accrued_through, cum_c=excluded.cum_c",
                 (kind, ref, seg_start.isoformat(), int(base_c), rate_s, int(basis), night.isoformat(), cum_new))
    return cum_new - cum


# ---- fixture ------------------------------------------------------------------------------------------------------------------------
def runtime(tmp, at="2026-10-20T11:00:00-04:00", marks=None, ecb="2.00", sofr="3.87"):
    """make_runtime + the fx / credit handlers, a RateStore with ECBDFR / SOFR observed 2026-10-19, the local accrue_segment."""
    rt = make_runtime(tmp, at=at, marks=marks)
    fx.register(rt.bus)
    credit.register(rt.bus)
    rt.fx, rt.credit = fx, credit
    rt.rates = RateStore()
    if ecb is not None:
        rt.rates.put("ECBDFR", [(date(2026, 10, 19), Decimal(ecb))], persist=False)
    if sofr is not None:
        rt.rates.put("SOFR", [(date(2026, 10, 19), Decimal(sofr))], persist=False)
    rt.lifecycle = types.SimpleNamespace(accrue_segment=accrue_segment)
    rt.bus.register("test.fund_ccy", _h_fund_ccy)
    return rt


def _h_fund_ccy(ctx, payload):
    res = fx.fund_ccy(ctx, payload["account_id"], payload["currency"], int(payload["need_c"]), (payload.get("link_type", "test"), payload.get("link_id", "x")))
    return res.to_dict()


def q(rt, sql, params=()):
    with rt.db.reader() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(rt, sql, params=()):
    rows = q(rt, sql, params)
    return rows[0] if rows else None


def lines_of(rt, entry_id):
    return [(r["gl"], r["currency"], r["amount_c"], r["base_c"], r["fx_rate"]) for r in q(rt, "SELECT gl, currency, amount_c, base_c, fx_rate FROM journal_lines WHERE entry_id=? ORDER BY line_no", (entry_id,))]


def spot(rt, account, buy, sell, amount_c, amount_ccy, request_id=None):
    return execute(rt, "fx.spot", {"account_id": account, "buy_ccy": buy, "sell_ccy": sell, "amount_c": int(amount_c), "amount_ccy": amount_ccy}, request_id)


def accrue(rt, loan_id, night):
    return execute(rt, "credit.loan_accrue", {"loan_id": loan_id, "night": night}, f"job:ACCRUE_DAILY:loan:{loan_id}:{night}", actor="lifecycle")


def assert_fx_entries_balance(tc, rt):
    """L1 on every FX / SETTLE / LOAN entry: Σ amount per currency = 0 and Σ base = 0, by SQL."""
    bad = q(rt, "SELECT e.entry_id, l.currency, SUM(l.amount_c) s FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id"
               " WHERE e.kind IN ('FX','SETTLE','LOAN','ACCRUAL') GROUP BY 1,2 HAVING SUM(l.amount_c) <> 0")
    tc.assertEqual([], bad)
    bad = q(rt, "SELECT e.entry_id, SUM(l.base_c) s FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id"
               " WHERE e.kind IN ('FX','SETTLE','LOAN','ACCRUAL') GROUP BY 1 HAVING SUM(l.base_c) <> 0")
    tc.assertEqual([], bad)
    tc.assertEqual(0, one(rt, "SELECT COALESCE(SUM(base_c),0) s FROM gl_balances WHERE gl='3900'")["s"])     # L5: FX clearing nets to zero in base


class FxSpotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-fx-")
        self.rt = runtime(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- §19.6 / D9 ----------------------------------------------------------------------------------------------------------------
    def test_d9_eur_loan_and_spot(self):
        rt = self.rt
        # ---- Tue 2026-10-20 11:00 ET: credit.loan_draw EUR 1,000,000.00 at ECBDFR 2.00 (obs 10-19) + 0.75 = 2.75 %, fx 1.1238
        r = execute(rt, "credit.loan_draw", {"account_id": CREDIT, "currency": "EUR", "amount_c": 100000000, "term_days": None})
        self.assertTrue(r.ok, r.body)
        loan_id = r.body["loan_id"]
        self.assertEqual(("L-000001", "0.0275", "ECBDFR", "2", "2026-10-19", 360, "2026-10-20", None, "Money market (paper)"),
                         (loan_id, r.body["loan"]["rate"], r.body["loan"]["index"]["series"], r.body["loan"]["index"]["value"], r.body["loan"]["index"]["obs_date"],
                          r.body["loan"]["day_basis"], r.body["loan"]["opened_date"], r.body["loan"]["maturity_date"], r.body["loan"]["lender"]))
        # Dr 1010:EUR 1,000,000.00 / Cr 2750:EUR 1,000,000.00; base at 1.1238 = ±1,123,800.00 (plug on the first line of the pair)
        self.assertEqual([("1010", "EUR", 100000000, 112380000, "1.1238"), ("2750", "EUR", -100000000, -112380000, "1.1238")], lines_of(rt, r.body["entry_id"]))
        self.assertEqual((100000000, 100000000), (bal(rt, CREDIT, "1010", "EUR"), bal(rt, CREDIT, "2750", "EUR")))
        cash = rt.accounting.cash(CREDIT)["EUR"]
        self.assertEqual((100000000, 0, 0, 100000000), (cash.settled_c, cash.receivable_c, cash.payable_c, cash.available_c))     # settled at once
        self.assertEqual(0, rt.accounting.nav(CREDIT).nav_c)                        # S_EUR = 1010 − 2750 = 0
        assert_integrity(self, rt, "full")                                           # L14: Σ OPEN principal 1,000,000 = balance(2750)

        # ---- fx.spot sell EUR 1,000,000 buy USD: rate 1.1238 − 0.00002 = 1.12378; USD to_c(1,000,000 × 1.12378) = 1,123,780.00; spread 20.00; value Thu 10-22
        pv = fx.spot_preview(rt, CREDIT, "USD", "EUR", 100000000, "EUR")
        self.assertEqual((True, "EURUSD", "1.1238", "1.12378", "1.12378", 112378000, 100000000, 2000, "2026-10-20", "2026-10-22", 0),
                         (pv["ok"], pv["pair"], pv["mid"], pv["rate_quoted"], pv["rate"], pv["buy_c"], pv["sell_c"], pv["spread_cost_c"], pv["trade_date"], pv["value_date"],
                          pv["usd_adjusted_c"]))
        # legs: EUR payable 1,000,000 against settled 1,000,000 -> available at the value date 0 after, no shortfall; USD receivable 1,123,780
        self.assertEqual((100000000, 0, -100000000, 0, 0), (pv["legs"]["EUR"]["settled_before_c"], pv["legs"]["EUR"]["pending_before_c"], pv["legs"]["EUR"]["pending_after_c"],
                                                             pv["legs"]["EUR"]["available_at_value_date_after_c"], pv["legs"]["EUR"]["settlement_shortfall_c"]))
        self.assertEqual((0, 112378000, "receivable"), (pv["legs"]["USD"]["settled_before_c"], pv["legs"]["USD"]["pending_after_c"], pv["legs"]["USD"]["effect"]))
        r = spot(rt, CREDIT, "USD", "EUR", 100000000, "EUR")
        self.assertTrue(r.ok, r.body)
        d1 = r.body["deal"]
        self.assertEqual(("D-000001", "USD", "EUR", 112378000, 100000000, "1.12378", "1.12378", "1.1238", 2000, "2026-10-22", "PENDING"),
                         (d1["deal_id"], d1["buy_ccy"], d1["sell_ccy"], d1["buy_c"], d1["sell_c"], d1["rate"], d1["rate_quoted"], d1["mid"], d1["spread_cost_c"], d1["value_date"], d1["status"]))
        # USD leg Dr 1250 / Cr 3900 1,123,780.00; EUR leg Dr 3900 / Cr 2350 1,000,000.00 at fx 1.12378 (base ±1,123,780.00): 3900 nets to 0 in base
        self.assertEqual([("1250", "USD", 112378000, 112378000, "1"), ("3900", "USD", -112378000, -112378000, "1"),
                          ("3900", "EUR", 100000000, 112378000, "1.12378"), ("2350", "EUR", -100000000, -112378000, "1.12378")], lines_of(rt, d1["entry_id"]))
        st = q(rt, "SELECT * FROM settlements WHERE fx_deal_id=? ORDER BY settle_id", (d1["deal_id"],))
        self.assertEqual([("S-000001", "BUY_CCY", "USD", 112378000, "1250", "2026-10-22", "PENDING"), ("S-000002", "SELL_CCY", "EUR", -100000000, "2350", "2026-10-22", "PENDING")],
                         [(s["settle_id"], s["leg"], s["currency"], s["amount_c"], s["gl"], s["due_date"], s["status"]) for s in st])
        row = one(rt, "SELECT * FROM fx_deals WHERE deal_id=?", (d1["deal_id"],))
        self.assertEqual(("SPOT", "1.12378", "1.1238", 2000, "2026-10-20", "2026-10-22", "PENDING"), (row["kind"], row["rate"], row["mid"], row["spread_cost_c"], row["trade_date"], row["value_date"], row["status"]))
        # reads Tue (fx 1.1238): EUR settled 1,000,000, payable 1,000,000, available 0; USD receivable 1,123,780; loan 1,000,000
        eur, usd = rt.accounting.cash(CREDIT)["EUR"], rt.accounting.cash(CREDIT)["USD"]
        self.assertEqual((100000000, 100000000, 0, 0), (eur.settled_c, eur.payable_c, eur.available_c, eur.receivable_c))
        self.assertEqual((0, 112378000, 0), (usd.settled_c, usd.receivable_c, usd.available_c))                     # receivable due 10-22 does not count today
        self.assertEqual(100000000, bal(rt, CREDIT, "2750", "EUR"))
        # NAV = 1,123,800 + 1,123,780 − 1,123,800 − 1,123,800 = −20.00 (the spread); fx_result = Σ amount −1,000,000 × 1.1238 − Σ base −1,123,780 = −20.00; residual 0
        nav = rt.accounting.nav(CREDIT)
        self.assertEqual((-2000, -2000, 0, 0, 0), (nav.nav_c, nav.fx_result_c, nav.unrealised_c, nav.financing_c, nav.fees_c))
        self.assertEqual(1000000000 - 2000, rt.accounting.nav("T1:ALL").nav_c)
        pnl = rt.accounting.pnl("T1:ALL", "ITD")
        self.assertEqual((-2000, -2000, 0), (pnl.pnl_c, pnl.explained_c, pnl.residual_c))
        assert_fx_entries_balance(self, rt)
        assert_integrity(self, rt, "full")

        # ---- Wed 10-21 00:35 (fx 1.1300): accrual night Tue = to_c(1,000,000 × 0.0275 × 1/360) = to_c(76.388889) = 76.39; base at 1.13 = to_c(86.3207) = 86.32
        rt.clock.set("2026-10-21T00:35:00-04:00")
        rt.marks.set("EURUSD", "1.13")
        a = accrue(rt, loan_id, "2026-10-20")
        self.assertTrue(a.ok, a.body)
        self.assertEqual((7639, "0.0275", "2026-10-19", 7639, "2026-10-20"), (a.body["amount_c"], a.body["rate"], a.body["index"]["obs_date"], a.body["cum_c"], a.body["segment_start"]))
        self.assertEqual([("5150", "EUR", 7639, 8632, "1.13"), ("2755", "EUR", -7639, -8632, "1.13")], lines_of(rt, a.body["entry_id"]))
        memo = one(rt, "SELECT memo FROM journal_entries WHERE entry_id=?", (a.body["entry_id"],))["memo"]
        self.assertIn("ECBDFR 2.00 % (2026-10-19) + 0.75 % = 2.75 %, ACT/360, night of 2026-10-20", memo)
        self.assertEqual(7639, bal(rt, CREDIT, "2755", "EUR"))
        # the same night again (job retry) is skipped, nothing double-posted
        a2 = execute(rt, "credit.loan_accrue", {"loan_id": loan_id, "night": "2026-10-20"}, actor="lifecycle")
        self.assertEqual((True, 0), (a2.body["skipped"], a2.body["amount_c"]))
        self.assertEqual(7639, bal(rt, CREDIT, "2755", "EUR"))
        # NAV at 1.13 = 1,130,000 + 1,123,780 − 1,130,000 − 1,130,000 − 86.32 = −6,306.32; financing −86.32; fx_result −6,220.00; residual 0
        nav = rt.accounting.nav(CREDIT)
        self.assertEqual((-630632, -622000, 8632, 0), (nav.nav_c, nav.fx_result_c, nav.financing_c, nav.unrealised_c))
        pnl = rt.accounting.pnl("T1:ALL", "ITD")
        self.assertEqual((-630632, -630632, 0), (pnl.pnl_c, pnl.explained_c, pnl.residual_c))
        self.assertEqual({"realised_c": 0, "income_c": 0, "financing_c": 8632, "fees_c": 0, "unrealised_c": 0, "fx_result_c": -622000}, pnl.components)
        assert_integrity(self, rt, "full")

        # ---- Thu 10-22 00:35 (fx 1.13): settlement of both legs; accrual night Wed cum to_c(152.777778) = 152.78 -> posts 76.39
        rt.clock.set("2026-10-22T00:35:00-04:00")
        s = execute(rt, "fx.settle", {"deal_id": d1["deal_id"]}, f"job:SETTLE_DUE:{d1['deal_id']}", actor="lifecycle")
        self.assertTrue(s.ok, s.body)
        self.assertEqual(("SETTLED", 2, []), (s.body["status"], len(s.body["settled"]), s.body["unfunded"]))
        buy_leg, sell_leg = s.body["settled"]
        # Dr 1010:USD 1,123,780.00 / Cr 1250:USD; Dr 2350:EUR 1,000,000.00 / Cr 1010:EUR at 1.13 (base ±1,130,000.00); EUR own cash covers (no sweep)
        self.assertEqual([("1010", "USD", 112378000, 112378000, "1"), ("1250", "USD", -112378000, -112378000, "1")], lines_of(rt, buy_leg["entry_id"]))
        self.assertEqual([("2350", "EUR", 100000000, 113000000, "1.13"), ("1010", "EUR", -100000000, -113000000, "1.13")], lines_of(rt, sell_leg["entry_id"]))
        self.assertEqual(("own", 0, None), (sell_leg["funding"]["source"], sell_leg["funding"]["shortfall_c"], sell_leg["funding"]["sweep_id"]))
        self.assertEqual("SETTLED", one(rt, "SELECT status FROM fx_deals WHERE deal_id=?", (d1["deal_id"],))["status"])
        self.assertEqual(["SETTLED", "SETTLED"], [x["status"] for x in q(rt, "SELECT status FROM settlements WHERE fx_deal_id=?", (d1["deal_id"],))])
        a = accrue(rt, loan_id, "2026-10-21")
        self.assertEqual((7639, 15278), (a.body["amount_c"], a.body["cum_c"]))
        # USD settled 1,123,780.00; EUR settled 0.00; 2755 152.78
        self.assertEqual((112378000, 0, 15278, 0, 0), (bal(rt, CREDIT, "1010", "USD"), bal(rt, CREDIT, "1010", "EUR"), bal(rt, CREDIT, "2755", "EUR"),
                                                       bal(rt, CREDIT, "1250", "USD"), bal(rt, CREDIT, "2350", "EUR")))
        # NAV = 1,123,780 − 1,130,000 − to_c(152.78 × 1.13) = 172.64 -> −6,392.64; fx_result still −6,220.00; financing −172.64
        nav = rt.accounting.nav(CREDIT)
        self.assertEqual((-639264, -622000, 17264), (nav.nav_c, nav.fx_result_c, nav.financing_c))
        self.assertEqual(0, rt.accounting.pnl("T1:ALL", "ITD").residual_c)
        # a replay of the settlement command with the same request id returns the stored result; a new one is a no-op
        s2 = execute(rt, "fx.settle", {"deal_id": d1["deal_id"]}, f"job:SETTLE_DUE:{d1['deal_id']}", actor="lifecycle")
        self.assertEqual((True, True), (s2.ok, s2.replayed))
        s3 = execute(rt, "fx.settle", {"deal_id": d1["deal_id"]}, actor="lifecycle")
        self.assertEqual((True, "SETTLED"), (s3.body["skipped"], s3.body["status"]))
        assert_integrity(self, rt, "full")

        # ---- credit.loan_repay 1,000,152.78 with EUR settled 0 -> 409 INSUFFICIENT_CASH_CCY "buy EUR 1,000,152.78 with Convert"
        r = execute(rt, "credit.loan_repay", {"loan_id": loan_id, "amount_c": 100015278})
        self.assertEqual((409, "INSUFFICIENT_CASH_CCY"), (r.http, r.code))
        self.assertEqual(("buy EUR 1,000,152.78 with Convert", 100015278, 0), (r.body["error"]["details"]["hint"], r.body["error"]["details"]["shortfall_c"], r.body["error"]["details"]["settled_c"]))
        self.assertIn("buy EUR 1,000,152.78 with Convert", r.message)
        self.assertEqual("OPEN", one(rt, "SELECT status FROM loans WHERE loan_id=?", (loan_id,))["status"])

        # ---- Thu 10:00: fx.spot buy EUR 1,000,500 (mid 1.1300 -> rate 1.13002): USD to_c(1,000,500 × 1.13002) = 1,130,585.01; spread 20.01; value Mon 10-26
        rt.clock.set("2026-10-22T10:00:00-04:00")
        r = spot(rt, CREDIT, "EUR", "USD", 100050000, "EUR")
        self.assertTrue(r.ok, r.body)
        d2 = r.body["deal"]
        self.assertEqual(("D-000002", "EUR", "USD", 100050000, 113058501, "1.13002", "1.13", 2001, "2026-10-22", "2026-10-26"),
                         (d2["deal_id"], d2["buy_ccy"], d2["sell_ccy"], d2["buy_c"], d2["sell_c"], d2["rate"], d2["mid"], d2["spread_cost_c"], d2["trade_date"], d2["value_date"]))
        # Dr 1250:EUR 1,000,500 / Cr 3900:EUR at 1.13002 (base ±1,130,585.01); Dr 3900:USD / Cr 2350:USD 1,130,585.01
        self.assertEqual([("1250", "EUR", 100050000, 113058501, "1.13002"), ("3900", "EUR", -100050000, -113058501, "1.13002"),
                          ("3900", "USD", 113058501, 113058501, "1"), ("2350", "USD", -113058501, -113058501, "1")], lines_of(rt, d2["entry_id"]))
        self.assertEqual([("S-000003", "BUY_CCY", "EUR", 100050000, "1250", "2026-10-26"), ("S-000004", "SELL_CCY", "USD", -113058501, "2350", "2026-10-26")],
                         [(s["settle_id"], s["leg"], s["currency"], s["amount_c"], s["gl"], s["due_date"]) for s in q(rt, "SELECT * FROM settlements WHERE fx_deal_id=? ORDER BY settle_id", (d2["deal_id"],))])
        # NAV = −6,392.64 − 20.01 = −6,412.65: S_EUR = −1,000,152.78 + 1,000,500 = 347.22 -> to_c(347.22 × 1.13) = 392.36; S_USD = 1,123,780 − 1,130,585.01 = −6,805.01
        nav = rt.accounting.nav(CREDIT)
        self.assertEqual((-641265, -624001), (nav.nav_c, nav.fx_result_c))
        self.assertEqual(0, rt.accounting.pnl("T1:ALL", "ITD").residual_c)
        assert_fx_entries_balance(self, rt)
        assert_integrity(self, rt, "full")

        # ---- Mon 10-26 00:35 (fx 1.13): accruals for the nights of Thu 22, Fri 23, Sat 24, Sun 25 — cumulative on 1,000,000 at 2.75 %:
        # n=3 to_c(229.166667) = 229.17 -> 76.39; n=4 to_c(305.555556) = 305.56 -> 76.39; n=5 to_c(381.944444) = 381.94 -> 76.38; n=6 to_c(458.333333) = 458.33 -> 76.39
        rt.clock.set("2026-10-26T00:35:00-04:00")
        posted = [accrue(rt, loan_id, d).body["amount_c"] for d in ("2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25")]
        self.assertEqual([7639, 7639, 7638, 7639], posted)
        self.assertEqual(45833, bal(rt, CREDIT, "2755", "EUR"))                       # the sum is exactly 458.33
        self.assertEqual((45833, "2026-10-25", "2026-10-20"), tuple(one(rt, "SELECT cum_c, accrued_through, segment_start FROM accrual_state WHERE kind='loan' AND ref_id=?", (loan_id,)).values()))
        # the 76.38 night at 1.13: to_c(76.38 × 1.13) = to_c(86.3094) = 86.31
        n5 = one(rt, "SELECT entry_id FROM journal_entries WHERE idem_key=?", (f"accr:loan:{loan_id}:2026-10-24",))["entry_id"]
        self.assertEqual([("5150", "EUR", 7638, 8631, "1.13"), ("2755", "EUR", -7638, -8631, "1.13")], lines_of(rt, n5))
        # settlement of D-000002: Dr 1010:EUR 1,000,500 / Cr 1250:EUR (base 1,130,565.00 at 1.13); USD payable 1,130,585.01 vs settled 1,123,780.00
        # -> fund() sweeps exactly 6,805.01 from Treasury (SWEEP_IN linked to S-000004), then Dr 2350:USD / Cr 1010:USD 1,130,585.01 -> USD settled 0.00
        s = execute(rt, "fx.settle", {"deal_id": d2["deal_id"]}, f"job:SETTLE_DUE:{d2['deal_id']}", actor="lifecycle")
        self.assertTrue(s.ok, s.body)
        buy_leg, sell_leg = s.body["settled"]
        self.assertEqual([("1010", "EUR", 100050000, 113056500, "1.13"), ("1250", "EUR", -100050000, -113056500, "1.13")], lines_of(rt, buy_leg["entry_id"]))
        self.assertEqual(("sweep", 680501, 680501, 112378000), (sell_leg["funding"]["source"], sell_leg["funding"]["shortfall_c"], sell_leg["funding"]["sweep_c"], sell_leg["funding"]["own_c"]))
        sweep = one(rt, "SELECT * FROM transfers WHERE kind='SWEEP_IN'")
        self.assertEqual((680501, 680501, "settlement", "S-000004", TREASURY, CREDIT, "USD"),
                         (sweep["amount_c"], sweep["shortfall_c"], sweep["linked_type"], sweep["linked_id"], sweep["from_account"], sweep["to_account"], sweep["currency"]))
        self.assertEqual([("2350", "USD", 113058501, 113058501, "1"), ("1010", "USD", -113058501, -113058501, "1")], lines_of(rt, sell_leg["entry_id"]))
        self.assertEqual((0, 100050000, 1000000000 - 680501), (bal(rt, CREDIT, "1010", "USD"), bal(rt, CREDIT, "1010", "EUR"), bal(rt, TREASURY, "1010", "USD")))
        self.assertEqual("SETTLED", one(rt, "SELECT status FROM fx_deals WHERE deal_id=?", (d2["deal_id"],))["status"])
        assert_integrity(self, rt, "full")                                           # L19: the sweep links to an existing settlement, amount = shortfall

        # ---- Mon 10:00 repay 1,000,458.33: Dr 2755:EUR 458.33 / Dr 2750:EUR 1,000,000.00 / Cr 1010:EUR 1,000,458.33; loan CLOSED; EUR left 41.67
        rt.clock.set("2026-10-26T10:00:00-04:00")
        r = execute(rt, "credit.loan_repay", {"loan_id": loan_id, "amount_c": 100045833})
        self.assertTrue(r.ok, r.body)
        self.assertEqual((45833, 100000000, 0, True, 4167), (r.body["interest_c"], r.body["principal_c"], r.body["principal_left_c"], r.body["closed"], r.body["settled_c"]))
        # base at 1.13 with the plug on the largest line (1010): 458.33 × 1.13 = to_c(517.9129) = 517.91; 1,000,000 × 1.13 = 1,130,000.00; 1010 = −(517.91 + 1,130,000.00)
        self.assertEqual([("2755", "EUR", 45833, 51791, "1.13"), ("2750", "EUR", 100000000, 113000000, "1.13"), ("1010", "EUR", -100045833, -113051791, "1.13")], lines_of(rt, r.body["entry_id"]))
        self.assertEqual(("CLOSED", 0), tuple(one(rt, "SELECT status, principal_c FROM loans WHERE loan_id=?", (loan_id,)).values()))
        self.assertEqual((0, 0, 4167), (bal(rt, CREDIT, "2750", "EUR"), bal(rt, CREDIT, "2755", "EUR"), bal(rt, CREDIT, "1010", "EUR")))
        # CREDIT NAV at 1.13 = to_c(41.67 × 1.13) = 47.09; financing base = 86.32 × 5 + 86.31 = 517.91; fx_result = 47.09 − Σ base 6,287.10 = −6,240.01
        # (Σ base by gl: 1010 −6,152.91; 2750 +6,200.00; 2350 +6,220.00; 1250 +20.01; 2755 0.00); P&L ITD = −517.91 − 6,240.01 = −6,757.92, residual 0
        nav = rt.accounting.nav(CREDIT)
        self.assertEqual((4709, -624001, 51791, 0, 680501), (nav.nav_c, nav.fx_result_c, nav.financing_c, nav.unrealised_c, nav.transfers_c))
        by_gl = {r_["gl"]: r_["b"] for r_ in q(rt, "SELECT gl, SUM(base_c) b FROM gl_balances WHERE account_id=? AND currency='EUR' GROUP BY gl", (CREDIT,))}
        self.assertEqual({"1010": -615291, "2750": 620000, "2350": 622000, "1250": 2001, "2755": 0, "3900": 112378000 - 113058501, "5150": 51791}, by_gl)
        pnl = rt.accounting.pnl(CREDIT, "ITD")
        self.assertEqual((-675792, -675792, 0, 680501), (pnl.pnl_c, pnl.explained_c, pnl.residual_c, pnl.flows_c))
        pnl_all = rt.accounting.pnl("T1:ALL", "ITD")
        self.assertEqual((-675792, 0, 1000000000 - 675792), (pnl_all.pnl_c, pnl_all.residual_c, rt.accounting.nav("T1:ALL").nav_c))
        # economics: 1,123,780 received − 1,130,585.01 paid + 47.09 left = −6,757.92
        self.assertEqual(-675792, 112378000 - 113058501 + 4709)
        assert_fx_entries_balance(self, rt)
        assert_integrity(self, rt, "full")

    # ---- JPY inversion ----------------------------------------------------------------------------------------------------------------
    def test_jpy_inversion(self):
        rt = self.rt
        rt.clock.set("2026-10-20T14:00:00-04:00")
        # USDJPY 158.10 (JPY per USD, Yahoo-inverted pair).  Buy JPY 10,000,000 with USD in Treasury (Treasury may convert for funding):
        # buying JPY receives fewer JPY per USD: quoted 158.10 − 0.002 = 158.098; ledger rate = 1/158.098 = 0.006325190705 -> 8 dp 0.00632519
        # USD = to_c(10,000,000 × 0.00632519) = 63,251.90; at the mid 1/158.10 = 0.006325110689 -> 0.00632511 -> 63,251.10; spread cost 0.80
        pv = fx.spot_preview(rt, TREASURY, "JPY", "USD", 1000000000, "JPY")
        self.assertEqual((True, "USDJPY", True, "158.1", "158.098", "0.00632519", 1000000000, 6325190, 80, "0.01", "2026-10-22"),
                         (pv["ok"], pv["pair"], pv["usd_quoted"], pv["mid"], pv["rate_quoted"], pv["rate"], pv["buy_c"], pv["sell_c"], pv["spread_cost_c"], pv["pip"], pv["value_date"]))
        r = spot(rt, TREASURY, "JPY", "USD", 1000000000, "JPY")
        self.assertTrue(r.ok, r.body)
        d = r.body["deal"]
        # JPY leg at 0.00632519: base ±6,325,190c = the USD leg exactly, so 3900 nets to zero in base (L5) despite the 8-decimal inversion
        self.assertEqual([("1250", "JPY", 1000000000, 6325190, "0.00632519"), ("3900", "JPY", -1000000000, -6325190, "0.00632519"),
                          ("3900", "USD", 6325190, 6325190, "1"), ("2350", "USD", -6325190, -6325190, "1")], lines_of(rt, d["entry_id"]))
        self.assertEqual(("0.00632519", "158.1", 80), (d["rate"], d["mid"], d["spread_cost_c"]))
        inputs = json.loads(one(rt, "SELECT inputs_json FROM fx_deals WHERE deal_id=?", (d["deal_id"],))["inputs_json"])
        self.assertEqual(("158.098", True, "USDJPY"), (inputs["rate_quoted"], inputs["usd_quoted"], inputs["pair"]))
        # selling JPY pays more JPY per USD: 158.10 + 0.002 = 158.102 -> 1/158.102 = 0.006325030676 -> 0.00632503; USD typed 1,000.00:
        # JPY = to_c(1,000 / 0.00632503): 158,102 × 0.00632503 = 999.99989306, remainder 0.00010694 / 0.00632503 = 0.0169 -> 158,102.02;
        # USD recomputed = to_c(158,102.02 × 0.00632503) = to_c(1,000.0000196) = 1,000.00 -> usd_adjusted 0
        pv2 = fx.spot_preview(rt, TREASURY, "USD", "JPY", 100000, "USD")
        self.assertEqual(("158.102", "0.00632503", 100000, 15810202, 0), (pv2["rate_quoted"], pv2["rate"], pv2["buy_c"], pv2["sell_c"], pv2["usd_adjusted_c"]))
        # the JPY receivable is valued through fx("JPY") = 1/158.10 = 0.00632511: NAV(T1:ALL) = 10,000,000 − 63,251.90 + to_c(10,000,000 × 0.00632511) = 10,000,000 − 0.80
        self.assertEqual(1000000000 - 80, rt.accounting.nav("T1:ALL").nav_c)
        self.assertEqual(-80, rt.accounting.nav(TREASURY).fx_result_c)
        assert_fx_entries_balance(self, rt)
        assert_integrity(self, rt, "full")

    # ---- refusals -----------------------------------------------------------------------------------------------------------------------
    def test_stale_quote_closed_session_cross_and_forward(self):
        rt = self.rt
        fund(rt, FOREIGN_FX, 10000000)                                                # USD 100,000.00 in Foreign Markets & FX
        # a stale EURUSD record -> preview blocker + 409 QUOTE_STALE, nothing stored
        rt.marks.set("EURUSD", "1.1238", "stale")
        pv = fx.spot_preview(rt, FOREIGN_FX, "EUR", "USD", 100000, "EUR")
        self.assertEqual((False, "QUOTE_STALE", "STALE"), (pv["ok"], pv["blockers"][0]["code"], pv["reason"]))
        n = one(rt, "SELECT COUNT(*) n FROM commands")["n"]
        r = spot(rt, FOREIGN_FX, "EUR", "USD", 100000, "EUR")
        self.assertEqual((409, "QUOTE_STALE"), (r.http, r.code))
        self.assertEqual((n, 0), (one(rt, "SELECT COUNT(*) n FROM commands")["n"], one(rt, "SELECT COUNT(*) n FROM fx_deals")["n"]))
        rt.marks.set("EURUSD", "1.1238", "delayed")
        # Saturday: the FX_SPOT session is closed (Fri 17:00 -> Sun 17:00) -> 409 SESSION_CLOSED
        rt.clock.set("2026-10-24T12:00:00-04:00")
        r = spot(rt, FOREIGN_FX, "EUR", "USD", 100000, "EUR")
        self.assertEqual((409, "SESSION_CLOSED"), (r.http, r.code))
        rt.clock.set("2026-10-20T14:00:00-04:00")
        # no USD rate for the currency at all -> QUOTE_UNAVAILABLE (never a defaulted rate)
        r = spot(rt, FOREIGN_FX, "CHF", "USD", 100000, "CHF")
        self.assertEqual((409, "QUOTE_UNAVAILABLE"), (r.http, r.code))
        # a cross is two deals through USD (400 NOT_IN_THIS_BUILD); forwards are Build 2; US_EXCHANGE may not convert
        r = spot(rt, FOREIGN_FX, "EUR", "GBP", 100000, "EUR")
        self.assertEqual((400, "NOT_IN_THIS_BUILD"), (r.http, r.code))
        r = execute(rt, "fx.forward", {"account_id": FOREIGN_FX, "buy_ccy": "EUR", "sell_ccy": "USD", "amount_c": 100000, "amount_ccy": "EUR", "value_date": "2026-12-21"})
        self.assertEqual((400, "NOT_IN_THIS_BUILD"), (r.http, r.code))
        r = spot(rt, US_EXCHANGE, "EUR", "USD", 100000, "EUR")
        self.assertEqual((400, "NOT_TRADEABLE_HERE"), (r.http, r.code))
        r = spot(rt, FOREIGN_FX, "EUR", "EUR", 100000, "EUR")
        self.assertEqual((400, "SAME_CURRENCY"), (r.http, r.code))
        # a good deal in the FX account: buy EUR 1,000 at 1.1238 + 0.00002 = 1.12382 -> USD 1,123.82, spread to_c(1,000 × 1.1238) = 1,123.80 -> 0.02
        r = spot(rt, FOREIGN_FX, "EUR", "USD", 100000, "EUR")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((100000, 112382, 2, "1.12382"), (r.body["deal"]["buy_c"], r.body["deal"]["sell_c"], r.body["deal"]["spread_cost_c"], r.body["deal"]["rate"]))
        # settling before the value date is refused unless forced
        r2 = execute(rt, "fx.settle", {"deal_id": r.body["deal"]["deal_id"]})
        self.assertEqual((400, "NOT_DUE"), (r2.http, r2.code))
        assert_integrity(self, rt, "full")

    # ---- fund_ccy (WP-26's hook) ----------------------------------------------------------------------------------------------------------
    def test_fund_ccy_own_then_treasury_then_refusal(self):
        rt = self.rt
        # nobody holds EUR: 409 INSUFFICIENT_FUNDS_CCY with the Convert hint for the whole need (own 0, Treasury 0)
        r = execute(rt, "test.fund_ccy", {"account_id": FOREIGN_FX, "currency": "EUR", "need_c": 50000, "link_type": "xccy", "link_id": "SWP-000001:start"})
        self.assertEqual((409, "INSUFFICIENT_FUNDS_CCY", "buy EUR 500.00 with Convert"), (r.http, r.code, r.body["error"]["details"]["hint"]))
        self.assertEqual((50000, 0, 50000, 0), tuple(r.body["error"]["details"][k] for k in ("need_c", "own_settled_c", "shortfall_c", "treasury_available_c")))
        # Treasury deposits EUR 300.00 (at 1.1238: base 337.14); the account holds EUR 0 -> a need of 500.00 is still short by 200.00 after Treasury's 300
        deposit(rt, 30000, "EUR")
        r = execute(rt, "test.fund_ccy", {"account_id": FOREIGN_FX, "currency": "EUR", "need_c": 50000, "link_type": "xccy", "link_id": "SWP-000001:start"})
        self.assertEqual((409, "INSUFFICIENT_FUNDS_CCY", 30000, "buy EUR 500.00 with Convert"), (r.http, r.code, r.body["error"]["details"]["treasury_available_c"], r.body["error"]["details"]["hint"]))
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM transfers WHERE kind='SWEEP_IN'")["n"])      # all or nothing: no partial sweep
        # need 250.00: exactly the shortfall (250.00 − own 0) is swept from Treasury's EUR, linked to an existing journal reference
        # (L19 resolves link types other than fill/settlement/borrow through journal_entries: the deposit's own idem key serves here)
        dep = one(rt, "SELECT idem_key FROM journal_entries WHERE kind='CAPITAL' ORDER BY entry_id DESC LIMIT 1")["idem_key"]
        r = execute(rt, "test.fund_ccy", {"account_id": FOREIGN_FX, "currency": "EUR", "need_c": 25000, "link_type": "entry", "link_id": dep})
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("sweep", 25000, 25000, 0), (r.body["source"], r.body["shortfall_c"], r.body["sweep_c"], r.body["own_c"]))
        self.assertEqual((25000, 5000), (bal(rt, FOREIGN_FX, "1010", "EUR"), bal(rt, TREASURY, "1010", "EUR")))
        # own cash covers a need of 200.00: no sweep
        r = execute(rt, "test.fund_ccy", {"account_id": FOREIGN_FX, "currency": "EUR", "need_c": 20000, "link_type": "entry", "link_id": dep})
        self.assertEqual(("own", 0, None, 25000), (r.body["source"], r.body["shortfall_c"], r.body["sweep_id"], r.body["own_c"]))
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM transfers WHERE kind='SWEEP_IN'")["n"])
        assert_integrity(self, rt, "full")

    # ---- an unfunded payable leg stays pending and is retried --------------------------------------------------------------------------------
    def test_settlement_unfunded_then_retried(self):
        rt = self.rt
        rt.clock.set("2026-10-20T14:00:00-04:00")
        # buy EUR 1,000 with USD in the FX account (no USD there): rate 1.12382 -> USD 1,123.82 payable Thu 10-22
        r = spot(rt, FOREIGN_FX, "EUR", "USD", 100000, "EUR")
        self.assertTrue(r.ok, r.body)
        deal_id = r.body["deal"]["deal_id"]
        # Treasury emptied (withdraw 10,000,000.00) before the value date
        execute(rt, "treasury.withdraw", {"lifetime_id": T1, "amount_c": 1000000000, "currency": "USD"})
        rt.clock.set("2026-10-22T00:35:00-04:00")
        s = execute(rt, "fx.settle", {"deal_id": deal_id}, actor="lifecycle")
        self.assertTrue(s.ok, s.body)
        # the EUR receivable settles (Dr 1010:EUR / Cr 1250:EUR 1,000.00); the USD payable cannot be funded: PENDING + one ERROR SETTLEMENT_UNFUNDED
        self.assertEqual(("PENDING", 1, 1, "S-000002", 112382, "INSUFFICIENT_FUNDS"), (s.body["status"], len(s.body["settled"]), len(s.body["unfunded"]), s.body["unfunded"][0]["settle_id"],
                                                                                        s.body["unfunded"][0]["amount_c"], s.body["unfunded"][0]["reason"]))
        self.assertEqual((100000, 0, 112382), (bal(rt, FOREIGN_FX, "1010", "EUR"), bal(rt, FOREIGN_FX, "1010", "USD"), bal(rt, FOREIGN_FX, "2350", "USD")))
        att = q(rt, "SELECT severity, code, ref_type, ref_id, resolved_ts FROM attention WHERE code='SETTLEMENT_UNFUNDED'")
        self.assertEqual([("ERROR", "SETTLEMENT_UNFUNDED", "settlement", "S-000002", None)], [tuple(a.values()) for a in att])
        self.assertEqual("PENDING", one(rt, "SELECT status FROM fx_deals WHERE deal_id=?", (deal_id,))["status"])
        assert_integrity(self, rt, "full")                                                          # L18: no negative cash, nothing overdrawn
        # a retry with the Treasury still empty opens no second item
        execute(rt, "fx.settle", {"deal_id": deal_id}, actor="lifecycle")
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM attention WHERE code='SETTLEMENT_UNFUNDED'")["n"])
        # a deposit of 2,000.00 and the next tick: sweep exactly 1,123.82 (own USD 0), the leg settles, the item resolves, the deal is SETTLED
        deposit(rt, 200000)
        s = execute(rt, "fx.settle", {"deal_id": deal_id}, actor="lifecycle")
        self.assertEqual(("SETTLED", "sweep", 112382), (s.body["status"], s.body["settled"][0]["funding"]["source"], s.body["settled"][0]["funding"]["sweep_c"]))
        self.assertEqual((0, 200000 - 112382), (bal(rt, FOREIGN_FX, "1010", "USD"), bal(rt, TREASURY, "1010", "USD")))
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM attention WHERE code='SETTLEMENT_UNFUNDED' AND resolved_ts IS NULL")["n"])
        self.assertEqual("SETTLED", one(rt, "SELECT status FROM fx_deals WHERE deal_id=?", (deal_id,))["status"])
        self.assertEqual([deal_id], [e["ref_id"] for e in q(rt, "SELECT ref_id FROM events WHERE type='FX_SETTLED'")])
        with rt.db.reader() as conn:
            self.assertEqual([], fx.due_deals(conn, "2026-10-22"))
        assert_integrity(self, rt, "full")


if __name__ == "__main__":
    unittest.main()
