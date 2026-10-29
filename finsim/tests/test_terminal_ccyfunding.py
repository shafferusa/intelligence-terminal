"""FX_CCS_PLAN rev 2 (release 1.0.6.1, package P-D) D-T4: the standing settlement-funding instruction (``ccyfunding``): the job
OTC_CCY_FUNDING, ``plan()`` and the command ``otc.settlement_funding``.

Every expected number is worked by hand in a comment.  No network: FakeClock, FixedMarks (EURUSD 1.1158, GBPUSD 1.3323), a temp
database, books="real" (F1:PB_GS; F1 Treasury starts with USD 25,000,000.00).  The products' scheduled flows (K5 / K6, P-B and
P-C) are stood in by fixture hooks on ``rt.swaps_trs`` / ``rt.otc_options`` returning the flows each test states, so the job is
tested apart from the product code; the contracts are fixture rows carrying ``settlement_funding {"EUR": "BUY_WITH_USD"}``.

The desk rate of an SFI purchase (BUY EUR / SELL USD): EURUSD 1.1158 + 0.2 pip = 1.11582.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import types
import unittest

from tests.terminal_helpers import bal, execute, make_runtime, set_mark
from finsim.terminal import ccyfunding, fx, lifecycle, prime
from finsim.terminal.db import dumps

PB = "F1:PB_GS"
TRE = "F1:TREASURY"
CCS = "SWP-000007"
FWD = "OTC-000001"
MARKS = {"EURUSD": ("1.1158", "delayed"), "GBPUSD": ("1.3323", "delayed")}
SFI = {"EUR": "BUY_WITH_USD"}


def _h_open_pb_unchecked(ctx, payload):
    return prime.open_pb(ctx, payload["lifetime_id"], payload["broker"], check_eligibility=False)


class Flows:
    """The fixture K5 / K6 hooks: ``flows[contract_id]`` -> the flows that contract has scheduled (each test sets them)."""

    def __init__(self):
        self.flows = {}

    def scheduled_flows(self, conn, row):
        return [dict(f) for f in self.flows.get(row["contract_id"], [])]


def flow(d, ccy, amount_c, what, event, known=True):
    return {"date": d, "ccy": ccy, "amount_c": amount_c, "what": what, "event": event, "known": known}


def runtime(tmp, at):
    rt = make_runtime(tmp, at=at, books="real", marks=MARKS)
    prime.register(rt.bus)
    prime.bind(rt)
    fx.register(rt.bus)
    fx.bind(rt)
    rt.fx = fx
    ccyfunding.register(rt.bus)
    ccyfunding.bind(rt)
    rt.ccyfunding = ccyfunding
    rt.hooks = Flows()
    rt.swaps_trs = types.SimpleNamespace(scheduled_flows=rt.hooks.scheduled_flows)
    rt.otc_options = types.SimpleNamespace(scheduled_flows=rt.hooks.scheduled_flows)
    lifecycle.register(rt.bus, rt)
    rt.lifecycle = lifecycle.LifecycleScheduler(rt.bus, rt.db, rt, rt.clock, rt.log)
    rt.bus.register("test.open_pb_unchecked", _h_open_pb_unchecked)
    r = execute(rt, "account.open_pb", {"lifetime_id": "F1", "broker": "GS"})
    assert r.ok, r.body
    return rt


def insert_contract(rt, cid, kind, start, maturity, terms):
    with rt.db.writer() as tx:
        tx.conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date,"
                        " margin_mode, im_c, status, mark_c, vm_balance_c) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,'OPEN',0,0)",
                        (cid, 0, PB, "GS", kind, json.dumps(terms), 223160000, "USD", start, maturity, "CTM"))


def ccs(rt, sfi=SFI):
    insert_contract(rt, CCS, "XCCY", "2026-11-02", "2028-11-06", {"kind": "XCCY", "variant": "FLOAT_FLOAT", "settlement_funding": sfi,
                                                                 "legs": {"pay": {"currency": "EUR", "notional_c": 200000000},
                                                                          "receive": {"currency": "USD", "notional_c": 223160000}}})


def forward(rt, sfi=SFI):
    insert_contract(rt, FWD, "FX_FORWARD", "2026-10-29", "2028-11-02", {"pair": "EURGBP", "side": "SELL", "buy_ccy": "GBP", "sell_ccy": "EUR", "buy_c": 173324800,
                                                                       "sell_c": 200000000, "value_date": "2028-11-02", "settlement_funding": sfi})
    rt.hooks.flows[FWD] = [flow("2028-11-02", "EUR", -200000000, "forward delivery: deliver EUR 2,000,000.00", "DELIVERY"),
                           flow("2028-11-02", "GBP", 173324800, "forward delivery: receive GBP 1,733,248.00", "DELIVERY")]


def q(rt, sql, params=()):
    with rt.db.reader() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(rt, sql, params=()):
    rows = q(rt, sql, params)
    return rows[0] if rows else None


def keys(rt):
    with rt.db.reader() as conn:
        return ccyfunding.keys_due(conn, rt, int(rt.clock.epoch()), rt.sessions)


def run_key(rt, key, suffix=""):
    return execute(rt, ccyfunding.JOB_KIND, {"key": key}, f"job:{ccyfunding.JOB}:{key}{suffix}", actor="lifecycle")


def drain_treasury_usd(rt, keep_c=0):
    """Withdraw F1 Treasury's USD from the fund (all but ``keep_c``): no account of F1 holds it any more."""
    have = bal(rt, TRE, "1010", "USD")
    r = execute(rt, "treasury.withdraw", {"lifetime_id": "F1", "currency": "USD", "amount_c": have - keep_c})
    assert r.ok, r.body


class CcyFundingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-ccyf-")

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dt4_coupon_known_on_e1_bought_two_days_before_and_settled_pvp(self):
        # Tue 2027-02-02 10:00 ET = e_1, the period end: the EURUSD spot date of 02-02 is Thu 02-04 = p_1, the payment date
        self.rt = rt = runtime(self.tmp, "2027-02-02T10:00:00-05:00")
        ccs(rt)
        rt.hooks.flows[CCS] = [flow("2027-02-04", "EUR", -1294583, "pay leg coupon 1 (ESTR + 0.1 bp)", "PAY"),
                               flow("2027-02-04", "USD", 2357146, "receive leg coupon 1 (SOFR)", "PAY"),
                               flow("2028-11-06", "EUR", -200000000, "final exchange: notional borrowed repaid", "END"),
                               flow("2028-11-06", "USD", 223160000, "final exchange: notional lent comes back", "END")]
        # the account holds EUR 1,000.00 already (deposited into F1 Treasury, moved to the PB account)
        self.assertTrue(execute(rt, "treasury.deposit", {"lifetime_id": "F1", "amount_c": 100000, "currency": "EUR"}).ok)
        self.assertTrue(execute(rt, "treasury.transfer", {"from_account": TRE, "to_account": PB, "currency": "EUR", "amount_c": 100000}).ok)
        key = f"{PB}:EUR:2027-02-04"
        self.assertEqual([key], keys(rt))
        r = run_key(rt, key)
        self.assertTrue(r.ok, r.body)
        self.assertEqual("DONE", r.body["status"], r.body)
        d = r.body["deal"]
        # need = coupon 12,945.83 - EUR held 1,000.00 = 11,945.83; USD = 11,945.83 x 1.11582 = 11,945.83 + 11,945.83 x 0.11582
        # (1,194.583 + 119.4583 + 59.72915 + 9.556664 + 0.2389166 = 1,383.5660306) = 13,329.3960306 -> 13,329.40
        self.assertEqual(("EUR", 1194583, "USD", 1332940, "1.11582", "2027-02-02", "2027-02-04"),
                         (d["buy_ccy"], d["buy_c"], d["sell_ccy"], d["sell_c"], d["rate_quoted"], d["trade_date"], d["value_date"]))
        # spread: at the mid 11,945.83 x 1.1158 = 11,945.83 + 1,383.327114 = 13,329.157114 -> 13,329.16; 13,329.40 - 13,329.16 = 0.24
        self.assertEqual(24, d["spread_cost_c"])
        # the only obligation is the swap's: the rows are tagged for its PAY (settled PvP there)
        self.assertEqual([("xccy_event", CCS, "2027-02-04")] * 2, [(x["ref_type"], x["ref_id"], x["due_date"]) for x in
                                                                   q(rt, "SELECT ref_type, ref_id, due_date FROM settlements WHERE fx_deal_id=?", (d["deal_id"],))])
        link = json.loads(one(rt, "SELECT inputs_json FROM fx_deals WHERE deal_id=?", (d["deal_id"],))["inputs_json"])["link"]
        self.assertEqual(("otc_funding", [CCS], "2027-02-04", [1294583]), (link["type"], link["contracts"], link["due"], [o["amount_c"] for o in link["obligations"]]))
        # DONE: the key is not due again, and a re-run of the work buys nothing (the deal's EUR receivable now counts as held)
        self.assertEqual([], keys(rt))
        with rt.db.reader() as conn:
            n = ccyfunding.need_for(conn, rt, PB, "EUR", "2027-02-04")
        # holding = settled 1,000.00 + the deal's receivable 11,945.83 = 12,945.83 = the coupon -> need 0
        self.assertEqual((0, 1294583), (n["need_c"], n["holding"]["total_c"]))
        # the PAY on p_1 (00:45 ET): settle_linked receive then pay, settled PvP; the EUR the coupon needs is then in 1010
        rt.clock.set("2027-02-04T00:45:00-05:00")

        def _h_pay(ctx, payload):
            out = {"receive": fx.settle_linked(ctx, "xccy_event", CCS, "2027-02-04", "receive")}
            out["eur_before_coupon_c"] = int(ctx.conn.execute("SELECT COALESCE(SUM(balance_c),0) FROM gl_balances WHERE account_id=? AND gl='1010' AND currency='EUR'",
                                                              (PB,)).fetchone()[0])
            out["pay"] = fx.settle_linked(ctx, "xccy_event", CCS, "2027-02-04", "pay")
            ctx.conn.execute("INSERT INTO otc_resets(contract_id, event_date, kind, amount_c, detail_json, entry_id) VALUES (?,?,'PAY',0,?,NULL)",
                             (CCS, "2027-02-04", dumps({"test": True})))
            return out
        rt.bus.register("test.pay", _h_pay)
        r = execute(rt, "test.pay", {}, "job:test:pay-1", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        self.assertEqual(1294583, r.body["eur_before_coupon_c"])                   # 1,000.00 + 11,945.83 = 12,945.83: the coupon exactly
        self.assertEqual([("USD", -1332940)], [(x["currency"], x["amount_c"]) for x in r.body["pay"]["settled"]])
        self.assertEqual("SETTLED", one(rt, "SELECT status FROM fx_deals WHERE deal_id=?", (d["deal_id"],))["status"])

    def test_dt4_forward_delivery_bought_on_v_minus_2_and_switch_off(self):
        # Tue 2028-10-31 10:00 ET: the EURUSD spot date is Thu 2028-11-02 = the forward's value date V
        self.rt = rt = runtime(self.tmp, "2028-10-31T10:00:00-04:00")
        forward(rt)
        key = f"{PB}:EUR:2028-11-02"
        # switched off by the owner: no key, no deal; the change is one SETTINGS_CHANGED event with before and after
        r = execute(rt, "otc.settlement_funding", {"contract_id": FWD, "currency": "EUR", "mode": "NONE"})
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("BUY_WITH_USD", "NONE"), (r.body["before"], r.body["after"]))
        ev = one(rt, "SELECT payload_json FROM events WHERE type='SETTINGS_CHANGED' AND ref_id=?", (FWD,))
        self.assertEqual(({"mode": "BUY_WITH_USD", "source": "term"}, {"mode": "NONE"}), (json.loads(ev["payload_json"])["before"], json.loads(ev["payload_json"])["after"]))
        self.assertEqual([], keys(rt))
        # switched on again: the key is due; EUR held 0 -> buy EUR 2,000,000.00 at 1.11582 = USD 2,231,640.00 (spread vs 2,231,600.00 at the mid: 40.00)
        self.assertTrue(execute(rt, "otc.settlement_funding", {"contract_id": FWD, "currency": "EUR", "mode": "BUY_WITH_USD"}).ok)
        self.assertEqual([key], keys(rt))
        r = run_key(rt, key)
        self.assertTrue(r.ok, r.body)
        d = r.body["deal"]
        self.assertEqual((200000000, 223164000, 4000, "2028-11-02"), (d["buy_c"], d["sell_c"], d["spread_cost_c"], d["value_date"]))
        # a forward's purchase is not one swap's event: untagged, SETTLE_DUE settles it at 00:30 on V (before the delivery)
        self.assertEqual([(None, None)] * 2, [(x["ref_type"], x["ref_id"]) for x in q(rt, "SELECT ref_type, ref_id FROM settlements WHERE fx_deal_id=?", (d["deal_id"],))])
        rt.clock.set("2028-11-02T00:31:00-04:00")
        rt.lifecycle.tick()
        self.assertEqual("SETTLED", one(rt, "SELECT status FROM fx_deals WHERE deal_id=?", (d["deal_id"],))["status"])
        self.assertEqual(200000000, bal(rt, PB, "1010", "EUR"))                     # the EUR the delivery needs is in the account

    def test_dt4_no_quote_is_quiet_then_warns_at_1630(self):
        self.rt = rt = runtime(self.tmp, "2028-10-31T10:00:00-04:00")
        forward(rt)
        set_mark(rt, "EURUSD", "1.1158", "stale")                                    # no executable EURUSD print
        key = f"{PB}:EUR:2028-11-02"
        r = run_key(rt, key)
        self.assertTrue(r.ok, r.body)
        self.assertEqual(("SKIPPED", "NO_QUOTE"), (r.body["status"], r.body.get("code")))
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM attention WHERE resolved_ts IS NULL AND code IN ('NO_QUOTE','SFI_NOT_FUNDED')")["n"])   # quiet
        rt.clock.set("2028-10-31T16:26:00-04:00")                                     # the last retry before 16:30
        r = run_key(rt, key, ":r1")
        self.assertEqual(("SKIPPED", "SFI_NOT_FUNDED"), (r.body["status"], r.body.get("code")))
        a = one(rt, "SELECT severity, message FROM attention WHERE code='SFI_NOT_FUNDED' AND resolved_ts IS NULL")
        self.assertEqual("WARN", a["severity"])
        self.assertIn("may overdraw EUR", a["message"])
        self.assertEqual(0, one(rt, "SELECT COUNT(*) n FROM fx_deals")["n"])
        rt.clock.set("2028-10-31T16:31:00-04:00")
        self.assertEqual([], keys(rt))                                                # after 16:30 the job is quiet

    def test_dt4_usd_short_on_the_value_date_waits_unfunded(self):
        self.rt = rt = runtime(self.tmp, "2028-10-31T10:00:00-04:00")
        forward(rt)
        drain_treasury_usd(rt)                                                        # the PB account and Treasury hold no USD
        us_before = bal(rt, "F1:US_EXCHANGE", "1010", "USD")
        r = run_key(rt, f"{PB}:EUR:2028-11-02")
        self.assertTrue(r.ok, r.body)
        self.assertEqual((223164000, False), (r.body["deal"]["sell_c"], r.body["funding"]["fundable"]))     # booked anyway (D9), the note says so
        self.assertIn("SETTLEMENT_UNFUNDED", r.body["note"])
        rt.clock.set("2028-11-02T00:31:00-04:00")
        rt.lifecycle.tick()
        legs = {x["leg"]: x["status"] for x in q(rt, "SELECT leg, status FROM settlements WHERE fx_deal_id=?", (r.body["deal"]["deal_id"],))}
        self.assertEqual({"BUY_CCY": "SETTLED", "SELL_CCY": "PENDING"}, legs)        # the EUR arrives; the USD leg waits
        self.assertEqual(1, one(rt, "SELECT COUNT(*) n FROM attention WHERE code='SETTLEMENT_UNFUNDED' AND severity='ERROR' AND resolved_ts IS NULL")["n"])
        self.assertEqual((us_before, 0), (bal(rt, "F1:US_EXCHANGE", "1010", "USD"), bal(rt, PB, "1010", "USD")))   # nothing else moved cash

    def test_dt4_plan_shows_the_2028_gap(self):
        # after S (Tue 2026-11-03): F1 Treasury USD 1,925,382.48 (FX_CCS_PLAN D9: 2,103,950.48 - IM 178,528.00 - the 40.00 at S), PB USD 0
        self.rt = rt = runtime(self.tmp, "2026-11-03T10:00:00-05:00")
        drain_treasury_usd(rt, keep_c=192538248)
        ccs(rt)
        forward(rt)
        # D9's projected coupons 1-7 in one fixture flow each: USD 181,172.68 in, EUR 105,051.74 out (p_7 = 2028-08-04); the last
        # coupons (projected) and the final exchange on p_n = 2028-11-06
        rt.hooks.flows[CCS] = [flow("2028-08-04", "USD", 18117268, "receive leg coupons 1-7 (projected)", "PAY", known=False),
                               flow("2028-08-04", "EUR", -10505174, "pay leg coupons 1-7 (projected)", "PAY", known=False),
                               flow("2028-11-06", "USD", 2800000, "receive leg coupon 8 (projected)", "END", known=False),
                               flow("2028-11-06", "EUR", -1600000, "pay leg coupon 8 (projected)", "END", known=False),
                               flow("2028-11-06", "USD", 223160000, "final exchange: notional lent comes back", "END"),
                               flow("2028-11-06", "EUR", -200000000, "final exchange: notional borrowed repaid", "END")]
        p = ccyfunding.plan(rt, PB)
        self.assertEqual((0, 192538248), (p["usd_start"]["pb_c"], p["usd_start"]["treasury_c"]))
        rows = [(x["value_date"], x["funding_trade_date"], x["amount_c"], x["usd_at_mid_c"], x["usd_before_c"], x["gap_c"]) for x in p["purchases"]]
        # 2028-08-04 (dealt Wed 08-02): EUR 105,051.74 x 1.1158 = 105,051.74 + 12,164.991492 = 117,216.731492 -> 117,216.73; before it
        #   1,925,382.48 + the USD coupons 181,172.68 (same day) = 2,106,555.16: no gap; after it 2,106,555.16 - 117,216.73 = 1,989,338.43
        # 2028-11-02 (dealt Tue 10-31, the forward's delivery): EUR 2,000,000.00 x 1.1158 = 2,231,600.00 against 1,989,338.43:
        #   short 2,231,600.00 - 1,989,338.43 = 242,261.57 (D9); the gap is assumed covered for what follows (USD 0 after it)
        # 2028-11-06 (dealt Thu 11-02, final exchange + coupon 8): EUR 2,016,000.00 x 1.1158 = 2,231,600.00 + 17,852.80 = 2,249,452.80
        #   against 0 + 2,231,600.00 + 28,000.00 = 2,259,600.00: no gap (self-funding apart from the FX move)
        self.assertEqual([("2028-08-04", "2028-08-02", 10505174, 11721673, 210655516, 0),
                          ("2028-11-02", "2028-10-31", 200000000, 223160000, 198933843, 24226157),
                          ("2028-11-06", "2028-11-02", 201600000, 224945280, 225960000, 0)], rows)
        self.assertEqual([("2028-11-02", 24226157)], [(g["value_date"], g["gap_c"]) for g in p["gaps"]])
        self.assertIn("short about USD 242,261.57", p["gaps"][0]["text"])


class PlanAtQuoteTimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-ccyf-q-")

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dt4_plan_with_the_quoted_trade_shows_its_gap_before_booking(self):
        """The forward being quoted is not a contract yet: passed as ``extra`` it gets its purchase and the D9 gap in the quote."""
        self.rt = rt = runtime(self.tmp, "2026-11-03T10:00:00-05:00")
        drain_treasury_usd(rt, keep_c=192538248)                     # USD 1,925,382.48 in Treasury, as in D9
        quoted = [{"date": "2028-11-02", "ccy": "EUR", "amount_c": -200000000, "what": "forward delivery: deliver EUR 2,000,000.00", "event": "DELIVERY"},
                  {"date": "2028-11-02", "ccy": "GBP", "amount_c": 173324800, "what": "forward delivery: receive GBP 1,733,248.00", "event": "DELIVERY"}]
        p = ccyfunding.plan(rt, PB, extra=quoted)
        # EUR 2,000,000.00 x 1.1158 = USD 2,231,600.00 against the 1,925,382.48 held: short 2,231,600.00 - 1,925,382.48 = 306,217.52
        self.assertEqual([("2028-11-02", "2028-10-31", 200000000, 223160000, 192538248, 30621752)],
                         [(x["value_date"], x["funding_trade_date"], x["amount_c"], x["usd_at_mid_c"], x["usd_before_c"], x["gap_c"]) for x in p["purchases"]])
        self.assertEqual(["quote"], [o["instruction_source"] for o in p["obligations"]])
        self.assertEqual([], ccyfunding.plan(rt, PB)["purchases"])     # nothing booked: nothing planned


if __name__ == "__main__":
    unittest.main()
