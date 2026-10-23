"""Phase 4 — OTC derivatives: pricing, RFQ with dealers, IRS/FRA/caps/swaptions, cross-currency, TRS, CDS,
commodity swaps, ISDA/CSA margining, counterparty exposure, close-outs, credit events, defaults, replay."""
import unittest
from datetime import date

from helpers import make_world, assert_ledger_invariants
from finsim.domain.models import YieldCurve
from finsim.engines import otc_pricing as op
from finsim.engines.counterparties import CSA_TERMS, DEALERS, quote_half_width
from finsim.money import D
from finsim.world import CommandError, World


def best(r):
    return min((q for q in r.quotes if not q.get("declined")), key=lambda q: q["cost_vs_mid"])


def deal(w, pf, product, dealer=None, **params):
    r = w.request_quote(pf.id, product, params)
    q = best(r) if dealer is None else next(x for x in r.quotes if x["dealer"] == dealer)
    return w.execute_rfq(pf.id, r.id, q["dealer"]), r


class PricingTest(unittest.TestCase):
    def setUp(self):
        self.c = YieldCurve("2026-01-05", [0.25, 0.5, 1, 2, 3, 5, 7, 10, 20, 30], [0.03, 0.032, 0.035, 0.037, 0.038, 0.04, 0.041, 0.042, 0.044, 0.045])
        self.asof = date(2026, 1, 5)
        self.fp = op.schedule(self.asof, date(2031, 1, 5), 6, lambda d: d)
        self.fl = op.schedule(self.asof, date(2031, 1, 5), 3, lambda d: d)

    def test_swap_par_dv01_and_direction(self):
        r = op.par_rate(self.c, self.asof, 1e7, self.fp, self.fl, {})
        self.assertAlmostEqual(op.swap_pv(self.c, self.asof, 1e7, r, self.fp, self.fl, {}, True), 0.0, places=4)
        dv01 = op.swap_dv01(self.c, self.asof, 1e7, r, self.fp, self.fl, {}, True)
        self.assertGreater(dv01, 0, "paying fixed gains when rates rise")
        up = YieldCurve(self.c.date, self.c.tenors, [x + 0.01 for x in self.c.rates])
        self.assertAlmostEqual(op.swap_pv(up, self.asof, 1e7, r, self.fp, self.fl, {}, True), dv01 * 100, delta=dv01 * 100 * 0.05)
        self.assertLess(op.swap_pv(up, self.asof, 1e7, r, self.fp, self.fl, {}, False), 0)
        self.assertEqual(len(self.fp), 10)
        self.assertEqual(len(self.fl), 20)

    def test_fra_cap_swaption_cds(self):
        s, e = date(2026, 4, 5), date(2026, 7, 5)
        f = op.fwd_rate(self.c, op.years(self.asof, s), op.years(self.asof, e))
        self.assertAlmostEqual(op.fra_pv(self.c, self.asof, 1e7, f, s, e, True), 0.0, places=6)
        self.assertGreater(op.fra_pv(self.c, self.asof, 1e7, f - 0.005, s, e, True), 0)
        cap_lo = op.cap_pv(self.c, self.asof, 1e7, 0.04, self.fl, 0.2, True, {})["pv"]
        cap_hi = op.cap_pv(self.c, self.asof, 1e7, 0.04, self.fl, 0.4, True, {})["pv"]
        self.assertGreater(cap_hi, cap_lo)
        self.assertGreater(cap_lo, 0)
        r = op.par_rate(self.c, self.asof, 1e7, self.fp, self.fl, {})
        pay = op.swaption_pv(self.c, self.asof, 1e7, r, date(2027, 1, 5), self.fp, self.fl, 0.25, True)
        rec = op.swaption_pv(self.c, self.asof, 1e7, r, date(2027, 1, 5), self.fp, self.fl, 0.25, False)
        self.assertAlmostEqual(pay["pv"] - rec["pv"], 1e7 * pay["annuity"] * (pay["forward_rate"] - r), delta=1.0, msg="payer − receiver = forward swap value")
        per = op.schedule(self.asof, date(2031, 1, 5), 3, lambda d: d)
        h = op.hazard_from_spread(200, 0.4)
        par = op.cds_par_spread(self.c, self.asof, 1e7, h, 0.4, per)
        self.assertAlmostEqual(par, 200, delta=6)
        self.assertGreater(op.cds_pv(self.c, self.asof, 1e7, 100, h, 0.4, per, True), 0, "buying at 100 running when the market is 200 costs an upfront")
        self.assertGreater(op.cds_cs01(self.c, self.asof, 1e7, 100, 200, 0.4, per, True), 0)


class RFQTest(unittest.TestCase):
    def test_quotes_deterministic_wider_in_stress_and_expire(self):
        w, pf, store = make_world(capital=50_000_000)
        r1 = w.request_quote(pf.id, "IRS", {"notional": 10_000_000, "tenor_years": 5, "pay_fixed": True})
        self.assertGreaterEqual(len(r1.quotes), 4)
        for q in r1.quotes:
            self.assertGreater(q["ask"], q["bid"])
            self.assertEqual(q["level"], q["ask"], "paying fixed deals on the dealer's offer")
        w2, pf2, _ = make_world(capital=50_000_000)
        r2 = w2.request_quote(pf2.id, "IRS", {"notional": 10_000_000, "tenor_years": 5, "pay_fixed": True})
        self.assertEqual([q["level"] for q in r1.quotes], [q["level"] for q in r2.quotes])
        calm = quote_half_width("IRS", "GOLDMAN", "NORMAL_GROWTH", 0.0)
        self.assertGreater(quote_half_width("IRS", "GOLDMAN", "LIQUIDITY_STRESS", 0.0), 2 * calm)
        self.assertGreater(quote_half_width("IRS", "CITI", "NORMAL_GROWTH", 0.5), quote_half_width("IRS", "CITI", "NORMAL_GROWTH", 0.0))
        with self.assertRaises(CommandError):
            w.request_quote(pf.id, "CDS", {"reference": "NVDA", "notional": 1})
        with self.assertRaises(CommandError):
            w.request_quote(pf.id, "TRS", {"security_id": "UST-10Y", "units": 100})
        with self.assertRaises(CommandError):
            w.execute_rfq(pf.id, r1.id, "DEUTSCHE")          # does not quote rates
        w.advance(1)
        self.assertEqual(pf.rfqs[r1.id].status, "EXPIRED")
        with self.assertRaises(CommandError):
            w.execute_rfq(pf.id, r1.id, "GOLDMAN")
        fi = w.create_portfolio("FI", "PERSONAL", D(20_000_000), job="FIXED_INCOME_PM")
        w.request_quote(fi.id, "IRS", {"notional": 1_000_000, "tenor_years": 2})
        with self.assertRaises(CommandError):
            w.request_quote(fi.id, "TRS", {"security_id": "NVDA", "units": 100})
        cm = w.create_portfolio("CM", "PERSONAL", D(20_000_000), job="COMMODITY_TRADER")
        with self.assertRaises(CommandError):
            w.request_quote(cm.id, "IRS", {"notional": 1_000_000})


class RatesTest(unittest.TestCase):
    def test_irs_lifecycle_coupons_and_accounting(self):
        w, pf, store = make_world(capital=50_000_000)
        t, r = deal(w, pf, "IRS", notional=10_000_000, tenor_years=2, pay_fixed=True)
        led = w.ledgers[pf.id]
        self.assertLess(t.mtm, 0, "dealing on the offer costs the bid/offer")
        self.assertAlmostEqual(float(-t.mtm), best(r)["cost_vs_mid"], delta=abs(best(r)["cost_vs_mid"]) * 0.3 + 5)
        self.assertEqual(led.security_balance(t.id, "2800"), -t.mtm)
        self.assertGreater(t.analytics["dv01"], 0)
        self.assertEqual(pf.csas[t.counterparty].im_posted, D(0), "IM moves at the close")
        assert_ledger_invariants(self, w, pf)
        w.advance(1)
        csa = pf.csas[t.counterparty]
        self.assertEqual(csa.im_posted, (D(str(CSA_TERMS[t.counterparty]["im"]["IRS"])) * t.notional).quantize(D("0.01")))
        self.assertEqual(pf.cash_collateral[f"IM:{t.counterparty}"], csa.im_posted)
        self.assertIn(t.start, t.fixings, "first floating period fixed on the trade date")
        assert_ledger_invariants(self, w, pf)
        # run to the first floating payment (3 months) and the first fixed payment (6 months)
        fl = w.otc.periods(t.start, t.maturity, 3)
        first_pay = fl[0][2]
        while w.current_date < first_pay:
            w.advance(1)
        flows = [c for c in t.cashflows if c["kind"] == "FLOAT_COUPON"]
        self.assertEqual(len(flows), 1)
        tau = op.yearfrac(fl[0][0], fl[0][1], "ACT/360")
        expect = D(repr(10_000_000 * t.fixings[t.start] * tau)).quantize(D("0.01"))
        self.assertEqual(flows[0]["base"], expect, "we pay fixed, so we receive the floating coupon")
        self.assertEqual(led.security_balance(t.id, "4910"), expect)
        fp = w.otc.periods(t.start, t.maturity, 6)
        while w.current_date < fp[0][2]:
            w.advance(1)
        fixed = [c for c in t.cashflows if c["kind"] == "FIXED_COUPON"]
        self.assertEqual(len(fixed), 1)
        self.assertEqual(fixed[0]["base"], -D(repr(10_000_000 * t.terms["fixed_rate"] * op.yearfrac(fp[0][0], fp[0][1], "30/360"))).quantize(D("0.01")))
        assert_ledger_invariants(self, w, pf)
        # P&L identity: cumulative price P&L on the trade = MTM change + cash
        cum = sum((s.by_position.get(t.id, {}).get("price_pnl", D(0)) for s in pf.nav_history), D(0))
        self.assertEqual(cum, t.mtm + t.realized)
        w2 = World.load(store, "t")
        t2 = w2.portfolios[pf.id].otc_trades[t.id]
        self.assertEqual((t2.mtm, t2.fixings, t2.state, len(t2.cashflows)), (t.mtm, t.fixings, t.state, len(t.cashflows)))
        self.assertEqual(w2.ledgers[pf.id].nav(), led.nav())

    def test_fra_settles_at_fixing(self):
        w, pf, store = make_world(capital=20_000_000)
        t, r = deal(w, pf, "FRA", notional=25_000_000, start_months=2, length_months=3, pay_fixed=True)
        start = date.fromisoformat(t.terms["fra_start"])
        while w.current_date < start:
            w.advance(1)
        self.assertEqual(t.status, "MATURED")
        fix = t.fixings[t.terms["fra_start"]]
        tau = op.yearfrac(start, date.fromisoformat(t.terms["fra_end"]), "ACT/360")
        expect = D(repr(25_000_000 * (fix - t.terms["rate"]) * tau / (1 + fix * tau))).quantize(D("0.01"))
        self.assertEqual(t.cashflows[-1]["base"], expect)
        self.assertEqual(t.mtm, D(0))
        assert_ledger_invariants(self, w, pf)

    def test_cap_premium_and_swaption_exercise(self):
        w, pf, store = make_world(capital=50_000_000)
        cap, r = deal(w, pf, "CAP", notional=20_000_000, strike=0.03, tenor_years=1, buyer=True)
        led = w.ledgers[pf.id]
        prem = -cap.cashflows[0]["base"]
        self.assertGreater(prem, 0)
        self.assertEqual(led.security_balance(cap.id, "1800"), cap.mtm)
        self.assertLess(cap.mtm, prem, "marked to mid below the offer paid")
        self.assertEqual(led.security_balance(cap.id, "4910") + led.security_balance(cap.id, "4900"), cap.mtm - prem)
        r_irs = w.request_quote(pf.id, "IRS", {"notional": 20_000_000, "tenor_years": 5})
        par = r_irs.mid["mid"] / 1e4
        itm, _ = deal(w, pf, "SWAPTION", notional=20_000_000, expiry_months=1, swap_years=5, payer=True, buyer=True, strike=par - 0.015)
        otm, _ = deal(w, pf, "SWAPTION", notional=20_000_000, expiry_months=1, swap_years=5, payer=True, buyer=True, strike=par + 0.03)
        cash_settled, _ = deal(w, pf, "SWAPTION", notional=20_000_000, expiry_months=1, swap_years=5, payer=True, buyer=True, strike=par - 0.015, settlement="CASH")
        self.assertGreater(itm.mtm, otm.mtm)
        assert_ledger_invariants(self, w, pf)
        exp = date.fromisoformat(itm.terms["expiry"])
        while w.current_date < exp:
            w.advance(1)
            assert_ledger_invariants(self, w, pf)
        self.assertEqual(itm.status, "EXERCISED")
        self.assertEqual(otm.status, "EXPIRED")
        self.assertEqual(cash_settled.status, "EXERCISED")
        new = [t for t in pf.otc_trades.values() if t.product == "IRS" and t.terms.get("from_swaption") == itm.id]
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0].terms["fixed_rate"], itm.terms["strike"])
        self.assertTrue(new[0].terms["pay_fixed"])
        self.assertGreater(new[0].mtm, 0, "the swap struck below par is in the money")
        self.assertGreater(cash_settled.cashflows[-1]["base"], 0)
        self.assertAlmostEqual(float(cash_settled.cashflows[-1]["base"]), float(new[0].mtm), delta=float(new[0].mtm) * 0.02 + 50,
                               msg="cash settlement equals the value of the swap received on physical exercise")
        self.assertEqual(led.security_balance(otm.id, "1800"), D(0))
        # cap: a caplet settles on the first period end when fixed above the strike
        per = w.otc.periods(cap.start, cap.maturity, 3)
        while w.current_date < per[0][2]:
            w.advance(1)
        f0 = cap.fixings[cap.start]
        pays = [c for c in cap.cashflows if c["kind"] == "OPTION_PAYOFF"]
        if f0 > 0.03:
            self.assertEqual(len(pays), 1)
            self.assertEqual(pays[0]["base"], D(repr(20_000_000 * (f0 - 0.03) * op.yearfrac(per[0][0], per[0][1], "ACT/360"))).quantize(D("0.01")))
        else:
            self.assertEqual(pays, [])
        assert_ledger_invariants(self, w, pf)


class FXEquityCreditCommodityTest(unittest.TestCase):
    def test_cross_currency_swap_exchanges_and_par(self):
        w, pf, store = make_world(capital=50_000_000)
        t, r = deal(w, pf, "XCCY", usd_notional=10_000_000, ccy="EUR", tenor_years=1, direction="BORROW_FOREIGN")
        eur = pf.cash_account("EUR")
        self.assertEqual(eur.balance, t.terms["ccy_notional"])
        self.assertEqual(pf.cash_account("USD").balance, D(40_000_000))
        self.assertLess(abs(t.mtm), t.notional * D("0.002"), "a swap dealt near the market basis starts near par")
        # the foreign leg is par at the market basis whatever the tenor, currency or direction: inception PV is the dealer's cost, nothing else
        w3, pf3, _ = make_world(capital=50_000_000)
        for ccy, yrs, direction in (("GBP", 5, "LEND_FOREIGN"), ("EUR", 5, "BORROW_FOREIGN"), ("JPY", 2, "BORROW_FOREIGN")):
            t2, r2 = deal(w3, pf3, "XCCY", usd_notional=10_000_000, ccy=ccy, tenor_years=yrs, direction=direction)
            q2 = next(q for q in r2.quotes if q["dealer"] == t2.counterparty)
            self.assertLess(abs(float(t2.mtm) + float(q2["cost_vs_mid"])), 0.0005 * float(t2.notional), f"{ccy} {yrs}y {direction}: PV {t2.mtm} vs cost {q2['cost_vs_mid']}")
        self.assertLess(t.mtm, 0, "the dealer's basis costs a little")
        assert_ledger_invariants(self, w, pf)
        per = w.otc.periods(t.start, t.maturity, 3)
        while w.current_date < per[0][2]:
            w.advance(1)
        ix = [c for c in t.cashflows if c["kind"] == "XCCY_INTEREST"]
        self.assertEqual(len(ix), 1)
        legs = {l["currency"]: l["amount"] for l in ix[0]["legs"]}
        self.assertGreater(legs["USD"], 0, "we receive USD interest")
        self.assertLess(legs["EUR"], 0, "we pay EUR interest plus basis")
        assert_ledger_invariants(self, w, pf)
        while w.current_date < date.fromisoformat(t.maturity):
            w.advance(1)
        self.assertEqual(t.status, "MATURED")
        exch = [c for c in t.cashflows if c["kind"] == "NOTIONAL_EXCHANGE"]
        self.assertEqual(len(exch), 2, "initial and final exchange")
        self.assertEqual(exch[-1]["date"], t.maturity)
        self.assertEqual({l["currency"]: l["amount"] for l in exch[-1]["legs"]}["EUR"], -t.terms["ccy_notional"])
        eur_flows = sum((l["amount"] for c in t.cashflows for l in c["legs"] if l["currency"] == "EUR"), D(0))
        self.assertEqual(eur.balance, sum((m.amount for m in pf.cash_movements if m.currency == "EUR"), D(0)), "EUR ledger = its recorded movements")
        eur_interest = sum((m.amount for m in pf.cash_movements if m.currency == "EUR" and m.kind != "OTC"), D(0))
        self.assertEqual(eur.balance, eur_flows + eur_interest, "EUR balance = swap legs + interest earned on the EUR held")
        self.assertLess(eur_flows, D(0), "net EUR interest paid on the swap")
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())

    def test_total_return_swap_reset(self):
        w, pf, store = make_world(capital=50_000_000)
        t, r = deal(w, pf, "TRS", security_id="JPM", units=100_000, tenor_years=1, receiver=True)
        self.assertEqual(t.mtm, D(0))
        p0 = t.terms["initial_price"]
        assert_ledger_invariants(self, w, pf)
        per = w.otc.periods(t.start, t.maturity, 1)
        while w.current_date < per[0][2]:
            w.advance(1)
            assert_ledger_invariants(self, w, pf)
        resets = [c for c in t.cashflows if c["kind"] == "TRS_RESET"]
        self.assertEqual(len(resets), 1)
        price = float(w.market.last_bar("JPM").close)
        divs = sum(d["amount"] for d in t.state.get("dividend_log", []))
        fin = 0.0
        # reconstruct financing from the daily accrual: rate x notional x days/360 across the month
        note = resets[0]["note"]
        self.assertIn("total return", note)
        tr = 100_000 * (price - p0) + divs
        self.assertAlmostEqual(float(resets[0]["base"]) + float(D(note.split("less financing ")[1].replace(",", ""))), tr, delta=0.02)
        self.assertEqual(t.state["reset_price"], price)
        self.assertEqual(t.mtm, D(0), "flat after the reset")
        self.assertEqual(t.analytics["delta_units"], 100_000.0)
        w2 = World.load(store, "t")
        self.assertEqual(w2.portfolios[pf.id].otc_trades[t.id].state, t.state)

    def test_cds_premium_and_credit_event(self):
        w, pf, store = make_world(capital=50_000_000)
        buy, r = deal(w, pf, "CDS", reference="AAL-28", notional=10_000_000, tenor_years=5, buyer=True)
        sell, _ = deal(w, pf, "CDS", reference="JPM-29", notional=10_000_000, tenor_years=3, buyer=False, dealer="BARCLAYS")
        led = w.ledgers[pf.id]
        self.assertEqual(buy.terms["running_bps"], 500, "high-yield names trade 500 running")
        self.assertEqual(sell.terms["running_bps"], 100)
        up = [c for c in buy.cashflows if c["kind"] == "UPFRONT"]
        self.assertTrue(up and up[0]["base"] < 0 if buy.terms["upfront"] > 0 else True)
        assert_ledger_invariants(self, w, pf)
        per = w.otc.periods(buy.start, buy.maturity, 3)
        while w.current_date < per[0][2]:
            w.advance(1)
        prem = [c for c in buy.cashflows if c["kind"] == "CDS_PREMIUM"]
        self.assertEqual(len(prem), 1)
        self.assertEqual(prem[0]["base"], -D(repr(10_000_000 * 0.05 * op.yearfrac(per[0][0], per[0][1], "ACT/360"))).quantize(D("0.01")))
        self.assertGreater(buy.analytics["cs01"], 0)
        self.assertLess(sell.analytics["cs01"], 0)
        nav0 = led.nav()
        w.credit_event("AAL-28")
        self.assertEqual(buy.status, "SETTLED_DEFAULT")
        prot = [c for c in buy.cashflows if c["kind"] == "PROTECTION"][0]
        accrued = buy.state.get("accrued", 0)
        self.assertEqual(prot["base"], D(repr(10_000_000 * (1 - w.securities["AAL-28"].recovery_rate) - accrued)).quantize(D("0.01")))
        self.assertGreater(led.nav(), nav0, "protection buyer gains on the credit event")
        self.assertEqual(sell.status, "OPEN")
        self.assertEqual(led.security_balance(buy.id, "1800"), D(0))
        assert_ledger_invariants(self, w, pf)
        with self.assertRaises(CommandError):
            w.credit_event("NVDA")
        w2 = World.load(store, "t")
        self.assertEqual(w2.ledgers[pf.id].nav(), led.nav())
        self.assertEqual(w2.portfolios[pf.id].otc_trades[buy.id].status, "SETTLED_DEFAULT")

    def test_commodity_swap_settles_on_the_average(self):
        w, pf, store = make_world(capital=50_000_000)
        t, r = deal(w, pf, "COMMODITY_SWAP", code="CL", quantity=50_000, tenor_years=1, pay_fixed=True)
        self.assertAlmostEqual(r.mid["mid"], sum(r.mid["strip"]) / len(r.mid["strip"]), places=9)
        per = w.otc.periods(t.start, t.maturity, 1)
        spots = []
        while w.current_date < per[0][2]:
            w.advance(1)
            if w.current_date < per[0][1]:
                spots.append(w.market.spot("CL"))
        sets = [c for c in t.cashflows if c["kind"] == "COMMODITY_SETTLEMENT"]
        self.assertEqual(len(sets), 1)
        avg = t.state["realised"][per[0][0].isoformat()]
        self.assertAlmostEqual(avg, sum(spots) / len(spots), places=6)
        self.assertEqual(sets[0]["base"], D(repr(50_000 * (avg - t.terms["fixed_price"]))).quantize(D("0.01")))
        assert_ledger_invariants(self, w, pf)


class CSATest(unittest.TestCase):
    def test_variation_and_initial_margin_follow_the_csa(self):
        w, pf, store = make_world(capital=100_000_000)
        pay, _ = deal(w, pf, "IRS", notional=200_000_000, tenor_years=10, pay_fixed=True, dealer="MORGAN_STANLEY")     # threshold 0, MTA 50k
        csa = pf.csas["MORGAN_STANLEY"]
        for _ in range(6):
            w.advance(1)
            assert_ledger_invariants(self, w, pf)
            im = (D(str(csa.im_pct["IRS"])) * pay.notional).quantize(D("0.01"))
            self.assertEqual(csa.im_posted, im)
            self.assertEqual(csa.im_received, im)
            net = pay.mtm
            if net > csa.threshold + csa.mta:
                self.assertGreater(csa.vm_received, D(0))
                self.assertEqual(csa.vm_posted, D(0))
                self.assertLessEqual(abs(csa.vm_received - net), csa.mta)
            elif net < -(csa.threshold + csa.mta):
                self.assertGreater(csa.vm_posted, D(0))
                self.assertEqual(csa.vm_received, D(0))
                self.assertLessEqual(abs(csa.vm_posted + net), csa.mta)
            self.assertEqual(w.ledgers[pf.id].balance("2450"), csa.vm_received)
        ex = w.otc.exposure(pf)
        row = next(r for r in ex["rows"] if r["dealer"] == "MORGAN_STANLEY")
        self.assertEqual(row["current_exposure"], max(D(0), pay.mtm - csa.vm_received + csa.vm_posted))
        self.assertGreaterEqual(row["pfe"], max(D(0), pay.mtm - csa.vm_received))
        self.assertGreater(row["expected_loss_1y"], D(0))
        w2 = World.load(store, "t")
        c2 = w2.portfolios[pf.id].csas["MORGAN_STANLEY"]
        self.assertEqual((c2.vm_posted, c2.vm_received, c2.im_posted), (csa.vm_posted, csa.vm_received, csa.im_posted))

    def test_unmet_csa_call_closes_out_the_netting_set(self):
        # initial margin consumes nearly all the cash; the first losing day's variation margin cannot be funded
        found = None
        for seed in (42, 7, 99, 3):
            w, pf, store = make_world(seed=seed, capital=2_700_000)
            a, _ = deal(w, pf, "IRS", notional=100_000_000, tenor_years=10, pay_fixed=True, dealer="MORGAN_STANLEY")     # IM 1.5%, threshold 0, MTA 50k
            b, _ = deal(w, pf, "IRS", notional=50_000_000, tenor_years=10, pay_fixed=True, dealer="CITI")    # IM 2.0%, threshold 0, MTA 50k
            calls = lambda: [c for c in pf.collateral_calls.values() if c.source == "OTC"]
            for _ in range(20):
                w.advance(1)
                assert_ledger_invariants(self, w, pf)
                if any(c.status == "FORCED" for c in calls()):
                    break
            if any(c.status == "FORCED" for c in calls()):
                found = (w, pf, store, calls)
                break
        self.assertIsNotNone(found, "rates fall on at least one of these seeds: variation margin is called and the dealer closes out")
        w, pf, store, calls = found
        forced = [c for c in calls() if c.status == "FORCED"]
        for c in forced:
            self.assertGreaterEqual(c.cycles_open, 1, "one unmet cycle before the close-out")
            closed = [t for t in pf.otc_trades.values() if t.counterparty == c.reference]
            self.assertTrue(closed and all(t.status == "TERMINATED" for t in closed))
            self.assertTrue(all(t.mtm == D(0) for t in closed))
            self.assertTrue(any(cf["kind"] == "TERMINATION" for t in closed for cf in t.cashflows), "close-out settles at mid less unwind cost")
            csa = pf.csas[c.reference]
            self.assertEqual(csa.vm_posted, D(0))
        att = " ".join(x["text"] for x in pf.briefings[-1]["attention"])
        self.assertIn("CLOSE-OUT", att)
        assert_ledger_invariants(self, w, pf)
        w.advance(2)
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())


class DefaultAndExposureTest(unittest.TestCase):
    def test_counterparty_default_settles_at_recovery(self):
        w, pf, store = make_world(capital=50_000_000)
        a, _ = deal(w, pf, "IRS", notional=50_000_000, tenor_years=7, pay_fixed=True, dealer="CITI")
        b, _ = deal(w, pf, "CAP", notional=50_000_000, strike=0.02, tenor_years=3, buyer=True, dealer="CITI")   # deep in the money: big positive PV
        c, _ = deal(w, pf, "IRS", notional=20_000_000, tenor_years=5, pay_fixed=True, dealer="GOLDMAN")
        led = w.ledgers[pf.id]
        csa = pf.csas["CITI"]
        for _ in range(12):                       # a day on which the netting set is owed more than the VM we hold (VM lags a day)
            w.advance(1)
            net = a.mtm + b.mtm
            claim = net - csa.vm_received + csa.vm_posted
            if net > 0 and claim > 0:
                break
        self.assertGreater(net, 0)
        nav0 = led.nav()
        im0 = csa.im_posted
        w.default_counterparty("CITI", recovery=0.4)
        self.assertTrue(w.market.dealers.state["CITI"].defaulted)
        self.assertEqual(a.status, "TERMINATED")
        self.assertEqual(b.status, "TERMINATED")
        self.assertEqual(c.status, "OPEN")
        self.assertEqual(csa.status, "TERMINATED")
        self.assertEqual((csa.vm_received, csa.vm_posted, csa.im_posted), (D(0), D(0), D(0)))
        self.assertNotIn("IM:CITI", pf.cash_collateral)
        expected_loss = (max(claim, D(0)) * D("0.6")).quantize(D("0.01"))      # excess VM we hold is simply returned: no unsecured exposure
        self.assertAlmostEqual(float(nav0 - led.nav()), float(expected_loss), delta=0.05, msg="unsecured claim loses (1 − recovery)")
        assert_ledger_invariants(self, w, pf)
        with self.assertRaises(CommandError):
            w.request_quote(pf.id, "IRS", {"notional": 1_000_000}) and w.execute_rfq(pf.id, list(pf.rfqs)[-1], "CITI")
        w.advance(1)
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        self.assertTrue(w2.market.dealers.state["CITI"].defaulted)
        self.assertEqual(w2.ledgers[pf.id].nav(), led.nav())
        self.assertEqual(len(w2.events), len(w.events))
        # the CSA's own state is part of the projection: terminated, flat, and no stale cash-collateral entry after a reload
        c2 = w2.portfolios[pf.id].csas["CITI"]
        self.assertEqual((c2.status, c2.vm_received, c2.vm_posted, c2.im_posted), ("TERMINATED", D(0), D(0), D(0)))
        self.assertNotIn("CSA:CITI", w2.portfolios[pf.id].cash_collateral)
        assert_ledger_invariants(self, w2, w2.portfolios[pf.id])
        w2.advance(2)
        assert_ledger_invariants(self, w2, w2.portfolios[pf.id])
        self.assertEqual(w2.portfolios[pf.id].csas["CITI"].status, "TERMINATED", "a terminated netting set never re-margins")

    def test_degenerate_periods_are_rejected(self):
        w, pf, store = make_world(capital=20_000_000)
        for params in ({"notional": 10_000_000, "tenor_years": 5, "fixed_months": 0}, {"notional": 10_000_000, "tenor_years": 5, "float_months": 0},
                       {"notional": 10_000_000, "tenor_years": 0}):
            with self.assertRaises(CommandError):
                w.request_quote(pf.id, "IRS", params)
        with self.assertRaises(CommandError):
            w.request_quote(pf.id, "COMMODITY_SWAP", {"code": "CL", "quantity": 1000, "tenor_years": 1, "months": 0})
        with self.assertRaises(CommandError):
            w.request_quote(pf.id, "TRS", {"security_id": "NVDA", "units": 1000, "tenor_years": 1, "reset_months": 0})
        # a five-year swap has exactly ten semi-annual fixed periods even when the maturity rolls to the next business day
        t, _ = deal(w, pf, "IRS", notional=10_000_000, tenor_years=5, pay_fixed=True)
        self.assertEqual(len(w.otc.periods(t.start, t.maturity, 6)), 10)
        self.assertEqual(len(w.otc.periods(t.start, t.maturity, 3)), 20)

    def test_exposure_matches_the_close_out_claim(self):
        w, pf, store = make_world(capital=50_000_000)
        t, _ = deal(w, pf, "IRS", notional=100_000_000, tenor_years=10, pay_fixed=True, dealer="MORGAN_STANLEY")
        w.advance(3)
        csa = pf.csas["MORGAN_STANLEY"]
        row = next(r for r in w.otc.exposure(pf)["rows"] if r["dealer"] == "MORGAN_STANLEY")
        claim = t.mtm - csa.vm_received + csa.vm_posted
        self.assertEqual(row["current_exposure"], max(D(0), claim))
        self.assertGreaterEqual(row["pfe"], row["current_exposure"])

    def test_netting_reduces_exposure(self):
        w, pf, store = make_world(capital=50_000_000)
        deal(w, pf, "IRS", notional=50_000_000, tenor_years=5, pay_fixed=True, dealer="GOLDMAN")
        deal(w, pf, "IRS", notional=50_000_000, tenor_years=5, pay_fixed=False, dealer="GOLDMAN")
        w.advance(3)
        row = next(r for r in w.otc.exposure(pf)["rows"] if r["dealer"] == "GOLDMAN")
        self.assertEqual(row["trades"], 2)
        self.assertLess(abs(row["net_mtm"]), row["gross_positive"] + abs(row["gross_negative"]) + D(1))
        self.assertGreaterEqual(row["netting_benefit"], D(0))
        self.assertLessEqual(row["current_exposure"], row["pfe"])
        b = w.otc.book(pf)
        self.assertLess(abs(b["aggregate"]["rates_dv01"]), 200.0, "offsetting swaps leave little DV01")
        self.assertTrue(pf.briefings[-1]["otc"]["open"] == 2)
        self.assertIn("rates", pf.briefings[-1]["pnl_buckets"])


class OTCOptionsAndForwardsTest(unittest.TestCase):
    def test_otc_options_and_forwards_quote_settle_and_replay(self):
        w, pf, store = make_world(capital=50_000_000)
        t1, r1 = deal(w, pf, "EQUITY_OPTION", security_id="SPY", option_type="P", units=1000, tenor_months=1, buyer=True)
        t2, _ = deal(w, pf, "EQUITY_OPTION", security_id="SPX", option_type="C", strike=8000, units=10, tenor_months=2, buyer=False)
        t3, _ = deal(w, pf, "FX_OPTION", ccy="EUR", option_type="C", amount=1_000_000, tenor_months=1, buyer=True)
        t4, _ = deal(w, pf, "EQUITY_FORWARD", security_id="NVDA", units=2000, tenor_months=1, long=True)
        t5, _ = deal(w, pf, "COMMODITY_FORWARD", code="CL", units=5000, tenor_months=1, long=False)
        t6, _ = deal(w, pf, "FX_FORWARD", ccy="JPY", units=100_000_000, tenor_months=1, long=True)
        q1 = next(q for q in r1.quotes if q["dealer"] == t1.counterparty)
        self.assertGreater(t1.mtm, 0, "a bought option is an asset")
        self.assertLess(t2.mtm, 0, "a written option is a liability")
        self.assertEqual(t1.terms["strike"], r1.mid["strike"], "blank strike = at the money forward")
        self.assertTrue(any(c["kind"] == "PREMIUM" and c["base"] < 0 for c in t1.cashflows), "premium paid at inception")
        for t in (t4, t5, t6):
            self.assertLess(abs(t.mtm), D("0.01") * t.notional, "a forward dealt near mid starts near zero")
        assert_ledger_invariants(self, w, pf)
        w.advance(3)
        for t in (t1, t2, t3, t4, t5, t6):
            self.assertEqual(t.status, "OPEN")
        assert_ledger_invariants(self, w, pf)
        while w.current_date < date.fromisoformat(t6.maturity):
            w.advance(1)
        w.advance(1)
        self.assertIn(t1.status, ("EXERCISED", "EXPIRED"))
        self.assertIn(t3.status, ("EXERCISED", "EXPIRED"))
        for t in (t4, t5, t6):
            self.assertEqual(t.status, "MATURED")
            self.assertTrue(any(c["kind"] == "FORWARD_SETTLEMENT" for c in t.cashflows), f"{t.product} settles in cash at maturity")
        self.assertEqual(t2.status, "OPEN", "the two-month option is still alive")
        assert_ledger_invariants(self, w, pf)
        w2 = World.load(store, "t")
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())
        self.assertEqual(len(w2.replay_errors), 0)


class OTCDefinitionOfDoneTest(unittest.TestCase):
    def run_scenario(self):
        w, pf, store = make_world(seed=23, capital=80_000_000)
        deal(w, pf, "IRS", notional=50_000_000, tenor_years=5, pay_fixed=True)
        deal(w, pf, "FRA", notional=20_000_000, start_months=1, length_months=3, pay_fixed=False)
        deal(w, pf, "CAP", notional=20_000_000, strike=0.04, tenor_years=2)
        deal(w, pf, "SWAPTION", notional=20_000_000, expiry_months=1, swap_years=3, payer=False)
        deal(w, pf, "XCCY", usd_notional=10_000_000, ccy="JPY", tenor_years=1, direction="LEND_FOREIGN")
        deal(w, pf, "TRS", security_id="NVDA", units=20_000, tenor_years=1, receiver=False)
        deal(w, pf, "CDS", reference="F-32", notional=20_000_000, tenor_years=5, buyer=True)
        deal(w, pf, "COMMODITY_SWAP", code="GC", quantity=1_000, tenor_years=1, pay_fixed=False)
        assert_ledger_invariants(self, w, pf)
        for _ in range(8):
            w.advance(1)
            assert_ledger_invariants(self, w, pf)
        w.force_regime("LIQUIDITY_STRESS")
        for _ in range(15):
            w.advance(1)
            assert_ledger_invariants(self, w, pf)
        irs = next(t for t in pf.otc_trades.values() if t.product == "IRS")
        w.terminate_otc(pf.id, irs.id)
        w.credit_event("F-32")
        assert_ledger_invariants(self, w, pf)
        for _ in range(12):
            w.advance(1)
            assert_ledger_invariants(self, w, pf)
        kinds = {e.type for e in w.events}
        for k in ("RFQ_REQUESTED", "RFQ_EXECUTED", "OTC_TRADE_OPENED", "OTC_TRADE_MARKED", "OTC_FIXING", "OTC_CASHFLOW", "OTC_TRADE_TERMINATED", "CSA_MARGIN_MOVED", "CREDIT_EVENT"):
            self.assertIn(k, kinds)
        for s in pf.nav_history[1:]:
            prev = pf.nav_history[pf.nav_history.index(s) - 1]
            self.assertEqual(s.nav - prev.nav - s.capital_flows, s.day_pnl, s.date)
        w2 = World.load(store, "t")
        self.assertEqual(len(w2.events), len(w.events))
        self.assertEqual(w2.ledgers[pf.id].nav(), w.ledgers[pf.id].nav())
        assert_ledger_invariants(self, w2, w2.portfolios[pf.id])
        return w.ledgers[pf.id].nav(), len(w.events), {t.id: (t.status, t.mtm) for t in pf.otc_trades.values()}

    def test_deterministic(self):
        self.assertEqual(self.run_scenario(), self.run_scenario())


if __name__ == "__main__":
    unittest.main()
