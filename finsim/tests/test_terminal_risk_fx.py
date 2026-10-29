"""FX_CCS_PLAN rev 2, package P-B (release 1.0.6.1): the Risk page's FX block (K7, D14, section 6) - the ledger part of the currency exposure
once (``risk._currency_balances``) plus the OTC FX contracts' off-ledger part from their hooks, the pair scenarios, rate01 per currency,
basis01 per pair, points01, the counterparty line, the ladder and the limits.  Money tests B-T6, B-T7, B-T8 and B-T12.

The world is the worked example's (test_terminal_otc_fx_forward.runtime: Thu 2026-10-29 10:00 ET, the 2026-10-27 curves, EURUSD 1.1158,
GBPUSD 1.3323, EURGBP print 0.83725).  Trade B = SELL EUR 2,000,000.00 / BUY GBP 2Y at K 0.866624 (V 2028-11-02).  Every expected number is
worked by hand in the comment above it (the designer's Decimal scripts reproduce them).

Hand-worked (section 6, at T):
- nominal: EUR -2,000,000.00, GBP +2,000,000 x 0.866624 = +1,733,248.00 (both legs off the ledger until delivery: from the hook);
- PV deltas: EUR -2,000,000 x DF*_EUR 0.9413073853 = -1,882,614.77; GBP +1,733,248 x DF*_GBP 0.9094909816 = +1,576,373.42;
- EURUSD +5 % (GBPUSD held) = -1,882,614.77 x 1.1158 x 0.05 = -105,031.08; GBPUSD +5 % (EURUSD held) = +1,576,373.42 x 1.3323 x 0.05 =
  +105,010.12; EURGBP +5 % with EURUSD held moves GBPUSD by 1 / 1.05: +1,576,373.42 x 1.3323 x (1 / 1.05 - 1) = -100,009.63;
- rate01 EUR +420.66, GBP -420.57; basis01 EUR/USD +420.66, GBP/USD -420.57 (the same discount curves); EUR OIS spread +423.92; points01
  -2,000,000 x 0.0001 x 0.9094909816 x 1.3323 = -242.34.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from decimal import Decimal

from tests import test_terminal_otc_asof as A
from tests import test_terminal_otc_fx_forward as F
from tests.terminal_helpers import assert_integrity, execute, post_entry
from finsim.terminal import risk

D = Decimal
PB = A.PB


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst-riskfx-")
        self.rt = F.runtime(self.tmp)

    def tearDown(self):
        self.rt.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def book_b(self, rid="ui:riskfx-b-0001"):
        r = execute(self.rt, "otc.book", {"account_id": PB, "kind": "FX_FORWARD", "terms": dict(F.B_TERMS)}, rid)
        self.assertTrue(r.ok, r.body)
        return r.body["contract_id"]

    def fxb(self, scope=PB):
        v = risk.report(self.rt, scope)
        return v, v.fx_sensitivities


def by_ccy(block):
    return {r["ccy"]: r for r in block["by_currency"]}


def pair_pnl(block, pair, shock, held):
    got = [p["pnl_c"] for p in block["pairs"] if p["pair"] == pair and p["shock"] == shock and p["held"].startswith(held)]
    assert len(got) == 1, (pair, shock, held, block["pairs"])
    return got[0]


class ForwardAloneTests(_Base):
    """B-T6, B-T7, B-T8."""

    def test_b_t6_risk_after_the_forward_alone_at_t(self):
        self.book_b()
        v, b = self.fxb()
        c = by_ccy(b)
        # nominal: the ledger holds nothing of the forward until delivery; the hook gives both legs
        self.assertEqual((-200000000, 0, -200000000), (c["EUR"]["nominal_c"], c["EUR"]["nominal_ledger_c"], c["EUR"]["nominal_hooks_c"]))
        self.assertEqual((173324800, 0, 173324800), (c["GBP"]["nominal_c"], c["GBP"]["nominal_ledger_c"], c["GBP"]["nominal_hooks_c"]))
        self.assertEqual((-188261477, 157637342), (c["EUR"]["pv_delta_c"], c["GBP"]["pv_delta_c"]))
        # exposure_by_currency = the ledger part + the hooks' part, in currency units
        self.assertEqual((-200000000, 173324800), (v.exposure_by_currency["EUR"]["native_c"], v.exposure_by_currency["GBP"]["native_c"]))
        # the module docstring's scenarios: -105,031.08 / +105,010.12 / -100,009.63
        self.assertEqual(-10503108, pair_pnl(b, "EURUSD", "+5 %", "GBPUSD"))
        self.assertEqual(10501012, pair_pnl(b, "GBPUSD", "+5 %", "EURUSD"))
        self.assertEqual(-10000963, pair_pnl(b, "EURGBP", "+5 %", "EURUSD held"))
        # the 1 % and 5 % columns: -1,882,614.77 x 1.1158 x 1 % = -21,006.22, x 5 % = -105,031.08
        self.assertEqual((-2100622, -10503108), (c["EUR"]["usd_per_1pct_c"], c["EUR"]["usd_per_5pct_c"]))
        assert_integrity(self, self.rt, "full")

    def test_b_t6_a_ledger_eur_balance_is_counted_once(self):
        """A fixture EUR liability of 500,000.00 in 2360 (a cross-currency notional payable) is the ledger part; the forward's hook adds only
        its own legs: EUR nominal -500,000.00 - 2,000,000.00 = -2,500,000.00, PV delta -500,000.00 - 1,882,614.77 = -2,382,614.77."""
        rt = self.rt
        cid = self.book_b()
        r = post_entry(rt, [[PB, "2360", "EUR", -50000000, cid, "1.1158"], [PB, "3900", "EUR", 50000000, None, "1.1158"], [PB, "3900", "USD", -55790000],
                            [PB, "1010", "USD", 55790000]], "test:riskfx:2360", kind="FX")
        self.assertTrue(r.ok, r.body)
        c = by_ccy(self.fxb()[1])
        self.assertEqual((-250000000, -50000000, -200000000), (c["EUR"]["nominal_c"], c["EUR"]["nominal_ledger_c"], c["EUR"]["nominal_hooks_c"]))
        self.assertEqual((-238261477, -50000000), (c["EUR"]["pv_delta_c"], c["EUR"]["pv_delta_ledger_c"]))

    def test_b_t7_the_otc_fx_forward_is_not_in_the_headline(self):
        self.book_b()
        v = risk.report(self.rt, PB)
        b = {e["bucket"]: e for e in v.exposures}
        # its USD notional 2,231,600.00 sits in 'FX forwards (notional)' (a sale: short), outside the headline long / short
        self.assertEqual((0, 223160000, False), (b["FX forwards (notional)"]["long_c"], b["FX forwards (notional)"]["short_c"], b["FX forwards (notional)"]["headline"]))
        self.assertEqual((0, 0, 0), (v.long_c, v.short_c, v.gross_c))
        self.assertNotIn("OTC options and forwards", b)

    def test_b_t8_rate_basis_spread_and_points_sensitivities_at_t(self):
        self.book_b()
        v, b = self.fxb()
        self.assertEqual({"EUR": 42066, "GBP": -42057}, {r["ccy"]: r["usd_per_bp_c"] for r in b["rates01"]})
        self.assertEqual({"EURUSD": 42066, "GBPUSD": -42057}, {r["pair"]: r["usd_per_bp_c"] for r in b["basis01"]})
        self.assertEqual([("EURGBP", -24234)], [(r["pair"], r["usd_per_pip_c"]) for r in b["points01"]])
        self.assertEqual({"EUR": 42392}, {r["ccy"]: r["usd_per_bp_c"] for r in b["spread01"]})
        self.assertIn("MODEL", b["labels"])
        # OTC-09: the forward is not a USD DV01 row and stays out of the rates scenario (its rate risk is the rows above)
        self.assertEqual([], [r for r in v.dv01_rows if r["kind"] == "SWAP"])
        rates = [s for s in v.scenarios if s["group"] == "rates"]
        self.assertEqual({0}, {s["pnl_c"] for s in rates})
        self.assertEqual(set(), {i for s in rates for i in s["unpriced"]})

    def test_counterparty_ladder_and_limits(self):
        cid = self.book_b()
        v, b = self.fxb()
        cp = {r["account_id"]: r for r in b["counterparty"]}[PB]
        # the GS line: the contract's value -419.25 (no VM yet), IM posted 133,896.00; exposure to a GS default = IM 133,896.00 + max(0,
        # -419.25 + 0 - 0) = 133,896.00
        self.assertEqual((-41925, 13389600, 0, 0, 13389600), (cp["mtm_c"], cp["im_posted_c"], cp["vm_posted_c"], cp["vm_received_c"], cp["exposure_c"]))
        flows = [(r["date"], r["ccy"], r["amount_c"], r["kind"]) for r in b["ladder"] if r.get("contract_id") == cid]
        self.assertEqual([("2028-11-02", "EUR", -200000000, "flow"), ("2028-11-02", "GBP", 173324800, "flow")], sorted(flows))
        self.assertEqual("none configured", b["limits"]["caps"])
        self.assertIn("none configured", b["limits"]["text"])
        d = v.to_dict()["fx_sensitivities"]                                     # JSON: cents as decimal strings, nothing is a float
        self.assertEqual("-1882614.77", {r["ccy"]: r for r in d["by_currency"]}["EUR"]["pv_delta"])


class LadderPlanTests(_Base):
    """K7 ladder + K8 (package P-D's ``ccyfunding.plan``, read lazily): with the settlement-funding instruction ON for the EUR the forward delivers,
    the ladder shows the planned purchase - EUR 2,000,000.00 for value 2028-11-02, dealt two TARGET2 + New York business days before
    (2028-10-31) - besides the forward's two delivery flows, and no obligation twice."""

    def test_the_ladder_carries_the_settlement_funding_purchase(self):
        try:
            from finsim.terminal import ccyfunding
        except ImportError:
            self.skipTest("the settlement-funding module (package P-D) is not in this build")
        rt = self.rt
        rt.ccyfunding = ccyfunding
        cid = self.book_b()
        p = ccyfunding.plan(rt, PB)
        if not p.get("available", True) or not p.get("purchases"):
            self.skipTest(f"the settlement-funding plan is not built yet ({p.get('reason')})")
        b = self.fxb()[1]
        rows = [(r["date"], r["ccy"], r["amount_c"], r["kind"]) for r in b["ladder"]]
        self.assertIn(("2028-11-02", "EUR", 200000000, "purchase"), rows)
        self.assertEqual(1, sum(1 for r in rows if r == ("2028-11-02", "EUR", -200000000, "flow")))          # the delivery once, from the contract
        purchase = next(r for r in b["ladder"] if r["kind"] == "purchase")
        self.assertIn("dealt 2028-10-31", purchase["what"])
        self.assertIn(cid, purchase["what"])


class BothTradesTests(_Base):
    """B-T12 (integration step 2; package P-C's float / float cross-currency swap and its hooks are in the build): both trades after the
    start date - Trade A (pay EUR ESTR + 0.1 bp / receive USD SOFR flat, EUR 2,000,000.00 / USD 2,231,600.00 at X0 1.1158, with its
    inception conversion) and Trade B (SELL EUR 2,000,000.00 / BUY GBP 1,733,248.00 2Y at K 0.866624), booked at T = 2026-10-29 on the
    2026-10-27 curves, then the START of 2026-11-02 00:45 ET (the conversion settles PvP with the exchange) and the valuation of 2026-11-02
    16:00 ET (values for the spot date 11-04, D19).

    (1) The plan's own section-6 table values the 2026-10-27 curves on 2026-11-02, 4 London / TARGET2 / SIFMA business days later, which
    the live age rule refuses (at most 3, D2).  So this check raises ``fxcurves.CURVE_MAX_AGE_BD`` to 4 for its own duration and restores it
    (the fxcurves module docstring's rule for tests; the terminal never does).  The figures are the plan's, to the cent:
    - EUR nominal = the ledger's 2360 -2,000,000.00 (A's final exchange, on the balance sheet since the START) + B's EUR leg -2,000,000.00
      (off the ledger until delivery: B's hook) = -4,000,000.00;
    - EUR PV delta = ledger -2,000,000.00 + A's hook +347.92 (its clean mark's FX delta) + B's -2,000,000 x DF*_EUR(11-04, 2028-11-02) =
      -1,882,871.00 -> -3,882,523.08; GBP nominal +1,733,248.00, PV delta +1,733,248 x DF*_GBP(11-04, 2028-11-02) = +1,576,702.24;
    - EURUSD +5 % (GBPUSD held) = -3,882,523.08 x 1.1158 x 0.05 = -4,332,119.25 x 0.05 = -216,605.96 (-5 %: +216,605.96); +1 % = -43,321.19;
    - GBPUSD +5 % (EURUSD held) = +1,576,702.24 x 1.3323 x 0.05 = +2,100,640.39 x 0.05 = +105,032.02; +1 % = +21,006.40;
    - EURGBP with EURUSD held moves GBPUSD by 1 / (1 + shock): +5 % = 2,100,640.39 x (1 / 1.05 - 1) = -100,030.49; -5 % = 2,100,640.39 x
      (1 / 0.95 - 1) = +110,560.02; +1 % = 2,100,640.39 x (1 / 1.01 - 1) = -20,798.42; with GBPUSD held it is the EURUSD row (-216,605.96);
    - rates +1 bp: EUR = A +1.10 + B +419.56 = +420.66; GBP = B -419.51; USD = A -1.03 (B's USD only translates);
    - basis levels +1 bp: EUR/USD = A +436.82 + B +419.56 = +856.38; GBP/USD = B -419.51; forward points +1 pip = B -242.39.
    (2) The live rule: the same rows re-dated to Friday 2026-10-30 (``fixture_curves(d0=2026-10-30)``: identical values, t measured from
    10-30) - the designer's functions re-run with that date (scratchpad bt12.py, Decimal precision 34): A's EUR PV delta -2,000,000.00 +
    348.29 = -1,999,651.71; B's -1,882,964.66; together -3,882,616.37; EURUSD +5 % = -3,882,616.37 x 1.1158 x 0.05 = -216,611.17;
    EUR/USD basis01 = A +436.83 + B +419.58 = +856.41; EUR nominal -4,000,000.00 (no curve in it)."""

    def test_b_t12_both_trades_after_the_start(self):
        from datetime import date as _date
        from finsim.terminal import fxcurves
        rt = self.rt
        terms = {"pay_ccy": "EUR", "recv_ccy": "USD", "pay_notional": "2000000.00", "pay_index": "ESTR", "recv_index": "SOFR", "months": 24, "mtm_reset": False,
                 "start_conversion": True, "settlement_funding": {"EUR": "BUY_WITH_USD"}}
        a = execute(rt, "otc.book", {"account_id": PB, "kind": "XCCY", "terms": dict(terms)}, "ui:riskfx-a-0001")
        self.assertTrue(a.ok, a.body)
        b_id = self.book_b("ui:riskfx-b-0002")
        cid = a.body["contract_id"]
        # the START on 2026-11-02 00:45 ET (the conversion settles PvP with the exchange), then the valuation of 2026-11-02 16:00 (values for 11-04)
        rt.clock.set("2026-11-02T00:45:00-05:00")
        r = execute(rt, "xccy.exchange", {"contract_id": cid, "date": "2026-11-02", "event": "START", "key": f"{cid}:2026-11-02:START"},
                    f"job:XCCY_EVENTS:{cid}:2026-11-02:START", actor="lifecycle")
        self.assertTrue(r.ok, r.body)
        rt.clock.set("2026-11-02T16:00:00-05:00")                                    # FX trade date 11-02: values for the spot date 11-04
        F.prints(rt)
        # (1) the plan's table on its own 2026-10-27 curves (4 business days old on 11-02): the age limit raised to 4 for this check only
        saved = fxcurves.CURVE_MAX_AGE_BD
        self.addCleanup(setattr, fxcurves, "CURVE_MAX_AGE_BD", saved)
        fxcurves.CURVE_MAX_AGE_BD = 4
        try:
            v, blk = self.fxb()
        finally:
            fxcurves.CURVE_MAX_AGE_BD = saved
        c = by_ccy(blk)
        self.assertEqual([], [n for n in blk["notes"] if "unavailable" in n], blk["notes"])
        # EUR nominal: 2360 -2,000,000.00 (ledger) + B's leg -2,000,000.00 (hook) = -4,000,000.00; GBP +1,733,248.00 (B's leg, hook)
        self.assertEqual((-400000000, -200000000, -200000000), (c["EUR"]["nominal_c"], c["EUR"]["nominal_ledger_c"], c["EUR"]["nominal_hooks_c"]))
        self.assertEqual(173324800, c["GBP"]["nominal_c"])
        # EUR PV delta: -2,000,000.00 + 347.92 - 1,882,871.00 = -3,882,523.08; GBP PV delta +1,576,702.24
        self.assertEqual((-388252308, 157670224), (c["EUR"]["pv_delta_c"], c["GBP"]["pv_delta_c"]))
        # the pair scenarios of section 6 (hand-worked in the docstring)
        self.assertEqual((-21660596, 21660596, -4332119), (pair_pnl(blk, "EURUSD", "+5 %", "GBPUSD"), pair_pnl(blk, "EURUSD", "-5 %", "GBPUSD"),
                                                            pair_pnl(blk, "EURUSD", "+1 %", "GBPUSD")))
        self.assertEqual((10503202, -10503202, 2100640), (pair_pnl(blk, "GBPUSD", "+5 %", "EURUSD"), pair_pnl(blk, "GBPUSD", "-5 %", "EURUSD"),
                                                           pair_pnl(blk, "GBPUSD", "+1 %", "EURUSD")))
        self.assertEqual((-10003049, 11056002, -2079842, -21660596), (pair_pnl(blk, "EURGBP", "+5 %", "EURUSD"), pair_pnl(blk, "EURGBP", "-5 %", "EURUSD"),
                                                                       pair_pnl(blk, "EURGBP", "+1 %", "EURUSD"), pair_pnl(blk, "EURGBP", "+5 %", "GBPUSD")))
        # rates +1 bp per currency, the basis levels per pair, forward points per pip
        self.assertEqual({"EUR": 42066, "GBP": -41951, "USD": -103}, {x["ccy"]: x["usd_per_bp_c"] for x in blk["rates01"]})
        self.assertEqual({"EURUSD": 85638, "GBPUSD": -41951}, {x["pair"]: x["usd_per_bp_c"] for x in blk["basis01"]})
        self.assertEqual([("EURGBP", -24239)], [(x["pair"], x["usd_per_pip_c"]) for x in blk["points01"]])
        # (2) the live 3-business-day rule: the same rows re-dated to Fri 10-30
        fxcurves.fixture_curves(rt, d0=_date(2026, 10, 30), persist=False)
        v, blk = self.fxb()
        c = by_ccy(blk)
        self.assertEqual([], [n for n in blk["notes"] if "unavailable" in n], blk["notes"])
        self.assertEqual((-400000000, -200000000, -200000000), (c["EUR"]["nominal_c"], c["EUR"]["nominal_ledger_c"], c["EUR"]["nominal_hooks_c"]))
        # EUR PV delta: -2,000,000.00 + 348.29 - 1,882,964.66 = -3,882,616.37
        self.assertEqual(-388261637, c["EUR"]["pv_delta_c"])
        # EURUSD +5 % (GBPUSD held): -3,882,616.37 x 1.1158 x 0.05 = -216,611.17
        self.assertEqual(-21661117, pair_pnl(blk, "EURUSD", "+5 %", "GBPUSD"))
        # EUR/USD basis01: A +436.83 + B +419.58 = +856.41
        self.assertEqual(85641, {r["pair"]: r["usd_per_bp_c"] for r in blk["basis01"]}["EURUSD"])
        self.assertTrue(b_id)
        assert_integrity(self, rt, "full")


if __name__ == "__main__":
    unittest.main()
