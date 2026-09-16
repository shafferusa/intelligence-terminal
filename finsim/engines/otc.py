"""OTC derivatives desk: request-for-quote with dealers, bilateral trades under
ISDA/CSA netting sets, daily fixings, payments, resets, marks, variation and
initial margin, counterparty exposure, close-outs, credit events.

Products: interest-rate swaps (fixed vs 3M term rate), FRAs, caps/floors,
European swaptions (physical or cash), cross-currency swaps, equity total
return swaps, single-name CDS, commodity fixed-for-floating swaps.

Accounting: every trade is carried at its dirty PV (accruals included) as a
derivative asset (1800) or liability (2800) with the daily change in 4900;
every cash flow is realised through 4910. P&L on a payment day nets to the
accrual already recognised, so "P&L = change in PV + cash" holds exactly.
Variation margin we post sits in cash-collateral-posted (1400, ref CSA:<dealer>),
variation margin we receive is cash with a liability (2450); initial margin we
post is segregated at the dealer's custodian (1400, ref IM:<dealer>); the
dealer's initial margin to us is segregated at a third party (memo only).
"""
from __future__ import annotations

import math
import random
from datetime import date
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Tuple

from ..domain.events import Event
from ..domain.models import Portfolio
from ..domain.otc_models import BUCKET_BY_PRODUCT, CSA, OE, OTCTrade, PRODUCTS, RFQ
from ..engines import otc_pricing as px
from ..engines.counterparties import CSA_TERMS, DEALERS, HALF_WIDTH, UNIT, dealers_for, quote_half_width
from ..engines.ledger import dr, cr
from ..engines.pricing import interp_rate
from ..money import D, money, ZERO

RATES_PRODUCTS = ("IRS", "FRA", "CAP", "FLOOR", "SWAPTION")
PFE_ADDON = {"IRS": 0.006, "FRA": 0.002, "CAP": 0.004, "FLOOR": 0.004, "SWAPTION": 0.004, "XCCY": 0.030, "TRS": 0.120, "CDS": 0.050, "COMMODITY_SWAP": 0.100}
TRS_BASE_SPREAD_BPS = {"NORMAL_GROWTH": 45.0, "RATE_CUTTING": 40.0, "RATE_HIKING": 60.0, "RECESSION": 90.0, "LIQUIDITY_STRESS": 180.0}
CSA_CALL_GRACE_CYCLES = 1
DEFAULT_RECOVERY = 0.4


def _f(x) -> float:
    return float(x)


class OTCEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        w = self.w
        w.on(OE.RFQ_REQUESTED, lambda world, ev: self._h_rfq(ev))
        w.on(OE.RFQ_EXECUTED, lambda world, ev: self._h_rfq_status(ev, "EXECUTED"))
        w.on(OE.RFQ_EXPIRED, lambda world, ev: self._h_rfq_status(ev, "EXPIRED"))
        w.on(OE.OTC_TRADE_OPENED, lambda world, ev: self._h_opened(ev))
        w.on(OE.OTC_TRADE_MARKED, lambda world, ev: self._h_marked(ev))
        w.on(OE.OTC_FIXING, lambda world, ev: self._h_fixing(ev))
        w.on(OE.OTC_CASHFLOW, lambda world, ev: self._h_cashflow(ev))
        w.on(OE.OTC_TRADE_TERMINATED, lambda world, ev: self._h_terminated(ev))
        w.on(OE.OTC_TRADE_EXERCISED, lambda world, ev: None)
        w.on(OE.CSA_MARGIN_MOVED, lambda world, ev: self._h_csa_moved(ev))
        w.on(OE.CSA_CALL, lambda world, ev: None)
        w.on(OE.CREDIT_EVENT, lambda world, ev: self._h_credit_event(ev))
        w.on(OE.COUNTERPARTY_DEFAULTED, lambda world, ev: self._h_cpty_defaulted(ev))

    # ------------------------------------------------------------------ market helpers
    def curve(self):
        return self.w.market.curve()

    def roll(self) -> Callable[[date], date]:
        return self.w.calendar.roll

    def periods(self, start: str, end: str, months: int):
        return px.schedule(date.fromisoformat(start), date.fromisoformat(end), months, self.roll())

    def term_rate(self, months: int) -> float:
        """Today's fixing for a money-market tenor: the zero curve at that tenor."""
        return interp_rate(self.curve(), months / 12.0)

    def rate_vol(self) -> float:
        return px.rate_vol(self.w.market.state.regime, self.w.market.vol_index())

    def reference_spread_bps(self, reference: str) -> float:
        """Market CDS spread for a reference entity: the issuer's bond spread scaled by where the credit index trades now."""
        w = self.w
        sec = w.securities[reference]
        cur = w.market.curve()
        first = w.market.curves[0]
        if (sec.rating or "BBB") in ("AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-"):
            scale = cur.ig_spread_bps / max(1.0, first.ig_spread_bps)
        else:
            scale = cur.hy_spread_bps / max(1.0, first.hy_spread_bps)
        return max(5.0, sec.spread_bps_credit * scale)

    def bucket_of(self, trade_id: str) -> str:
        for pf in self.w.portfolios.values():
            t = pf.otc_trades.get(trade_id)
            if t:
                return BUCKET_BY_PRODUCT.get(t.product, "rates")
        return "rates"

    def csa(self, pf: Portfolio, dealer: str) -> CSA:
        if dealer not in pf.csas:
            terms = CSA_TERMS[dealer]
            pf.csas[dealer] = CSA(counterparty=dealer, portfolio_id=pf.id, threshold=D(terms["threshold"]), mta=D(terms["mta"]), im_pct=dict(terms["im"]),
                                  eligible=["CASH"])
        return pf.csas[dealer]

    # ------------------------------------------------------------------ valuation
    def value(self, pf: Portfolio, t: OTCTrade, asof: Optional[date] = None) -> Tuple[Decimal, Dict, Dict]:
        """(PV in base currency from the portfolio's view, analytics, state updates)."""
        w = self.w
        asof = asof or w.current_date
        c = self.curve()
        T = t.terms
        N = _f(t.notional)
        p = t.product
        st = dict(t.state)
        an: Dict = {}
        pv = 0.0
        if p == "IRS":
            fp = self.periods(t.start, t.maturity, T["fixed_months"])
            fl = self.periods(t.start, t.maturity, T["float_months"])
            pay = T["pay_fixed"]
            pv = px.swap_pv(c, asof, N, T["fixed_rate"], fp, fl, t.fixings, pay)
            an = {"dv01": px.swap_dv01(c, asof, N, T["fixed_rate"], fp, fl, t.fixings, pay), "par_rate": px.par_rate(c, asof, N, fp, fl, t.fixings),
                  "fixed_rate": T["fixed_rate"], "next_fixed_payment": next((pd.isoformat() for _, _, pd in fp if pd > asof), None),
                  "next_float_payment": next((pd.isoformat() for _, _, pd in fl if pd > asof), None)}
        elif p == "FRA":
            s, e = date.fromisoformat(T["fra_start"]), date.fromisoformat(T["fra_end"])
            fixing = t.fixings.get(T["fra_start"])
            pv = px.fra_pv(c, asof, N, T["rate"], s, e, T["pay_fixed"], fixing)
            an = {"forward": fixing if fixing is not None else px.fwd_rate(c, px.years(asof, s), px.years(asof, e)), "rate": T["rate"],
                  "dv01": (px.fra_pv(c, asof, N, T["rate"] - 1e-4, s, e, T["pay_fixed"], fixing) - px.fra_pv(c, asof, N, T["rate"] + 1e-4, s, e, T["pay_fixed"], fixing)) / 2}
        elif p in ("CAP", "FLOOR"):
            per = self.periods(t.start, t.maturity, T["months"])
            r = px.cap_pv(c, asof, N, T["strike"], per, self.rate_vol() * T.get("vol_mult", 1.0), p == "CAP", t.fixings)
            sign = 1.0 if T["buyer"] else -1.0
            pv = sign * r["pv"]
            an = {"dv01": sign * r["dv01"], "vega": sign * r["vega"], "strike": T["strike"], "vol": self.rate_vol()}
        elif p == "SWAPTION":
            exp = date.fromisoformat(T["expiry"])
            fp = self.periods(T["expiry"], T["swap_maturity"], T["fixed_months"])
            fl = self.periods(T["expiry"], T["swap_maturity"], T["float_months"])
            r = px.swaption_pv(c, asof, N, T["strike"], exp, fp, fl, self.rate_vol() * T.get("vol_mult", 1.0), T["payer"])
            sign = 1.0 if T["buyer"] else -1.0
            pv = sign * r["pv"]
            an = {"dv01": sign * r["dv01"], "vega": sign * r["vega"], "forward_rate": r["forward_rate"], "strike": T["strike"], "annuity": r["annuity"], "vol": self.rate_vol()}
        elif p == "XCCY":
            pv, an = self._xccy_value(t, asof)
        elif p == "TRS":
            price = _f(w.market.last_bar(T["security_id"]).close)
            units = _f(T["units"])
            pv = px.trs_pv(units, price, _f(st.get("reset_price", price)), _f(st.get("dividends", 0)), _f(st.get("financing", 0)), T["receiver"])
            an = {"price": price, "reset_price": _f(st.get("reset_price", price)), "delta_units": units if T["receiver"] else -units,
                  "financing_rate": self.curve().policy_rate + T["spread_bps"] / 1e4, "dividends_accrued": _f(st.get("dividends", 0)), "financing_accrued": _f(st.get("financing", 0))}
        elif p == "CDS":
            per = self.periods(t.start, t.maturity, 3)
            mkt = self.reference_spread_bps(T["reference"])
            h = px.hazard_from_spread(mkt, T["recovery"])
            core = px.cds_pv(c, asof, N, T["running_bps"], h, T["recovery"], per, T["buyer"])
            accrued = _f(st.get("accrued", 0))
            pv = core - accrued if T["buyer"] else core + accrued
            an = {"market_spread_bps": mkt, "par_spread_bps": px.cds_par_spread(c, asof, N, h, T["recovery"], per),
                  "cs01": px.cds_cs01(c, asof, N, T["running_bps"], mkt, T["recovery"], per, T["buyer"]), "hazard": h, "accrued_premium": accrued,
                  "pd_1y": min(0.99, h)}
        elif p == "COMMODITY_SWAP":
            pv, an, st = self._commodity_value(t, asof, st)
        return money(D(repr(pv))), an, st

    def market_basis(self, ccy: str) -> float:
        """Cross-currency basis (bp on the foreign leg) as the market quotes it today: structurally negative for the funding
        currencies, widening as dollar funding tightens in stress."""
        base = {"EUR": -15.0, "JPY": -35.0, "CHF": -20.0, "GBP": -5.0, "CAD": 3.0, "AUD": 8.0}.get(ccy, 0.0)
        mult = {"NORMAL_GROWTH": 1.0, "RATE_CUTTING": 0.9, "RATE_HIKING": 1.3, "RECESSION": 1.8, "LIQUIDITY_STRESS": 3.5}.get(self.w.market.state.regime, 1.0)
        return base * mult if base < 0 else base / mult

    def _xccy_value(self, t: OTCTrade, asof: date) -> Tuple[float, Dict]:
        w = self.w
        T = t.terms
        c = self.curve()
        ccy = T["ccy"]
        spot = w.market.fx.spot[ccy]
        mkt_basis = self.market_basis(ccy)
        r_ccy_flat = w.market.fx.rate[ccy]
        r_ccy_disc = r_ccy_flat + mkt_basis / 1e4
        per = self.periods(t.start, t.maturity, T["months"])
        n_usd, n_ccy = _f(T["usd_notional"]), _f(T["ccy_notional"])
        pv_usd = pv_ccy = 0.0
        for (s, e, p) in per:
            if p <= asof:
                continue
            tau = px.yearfrac(s, e, "ACT/360")
            k = s.isoformat()
            r_usd = t.fixings.get("USD:" + k, px.fwd_rate(c, px.years(asof, s), px.years(asof, e)))
            r_c = t.fixings.get(ccy + ":" + k, r_ccy_flat)
            pv_usd += n_usd * r_usd * tau * px.df(c, px.years(asof, p))
            pv_ccy += n_ccy * (r_c + T["basis_bps"] / 1e4) * tau / (1 + r_ccy_disc) ** px.years(asof, p)
        Tm = px.years(asof, date.fromisoformat(t.maturity))
        pv_usd += n_usd * px.df(c, Tm)
        pv_ccy += n_ccy / (1 + r_ccy_disc) ** Tm
        pv_ccy_base = pv_ccy * spot
        pv = (pv_usd - pv_ccy_base) if T["direction"] == "BORROW_FOREIGN" else (pv_ccy_base - pv_usd)
        fx_delta = (-pv_ccy if T["direction"] == "BORROW_FOREIGN" else pv_ccy) * spot * 0.01   # per 1% move in the foreign currency
        return pv, {"pv_usd_leg": pv_usd, "pv_ccy_leg_base": pv_ccy_base, "spot": spot, "fx_delta_1pct": fx_delta, "basis_bps": T["basis_bps"], "market_basis_bps": mkt_basis}

    def _commodity_value(self, t: OTCTrade, asof: date, st: Dict) -> Tuple[float, Dict, Dict]:
        w = self.w
        T = t.terms
        code = T["code"]
        per = self.periods(t.start, t.maturity, T["months"])
        curve = w.market.curve_for(code)
        spot = w.market.spot(code)
        realised = {k: _f(v) for k, v in st.get("realised", {}).items()}
        cur_key = next((s.isoformat() for (s, e, p) in per if s <= asof < e or (s <= asof and p > asof)), None)

        def expected(e: date) -> float:
            k = e.strftime("%Y-%m")
            if k in curve:
                return curve[k]
            keys = sorted(curve)
            later = [x for x in keys if x >= k]
            return curve[later[0]] if later else (curve[keys[-1]] if keys else spot)
        # blend realised-to-date into the current period
        if cur_key and cur_key not in realised:
            acc = st.get("acc", {}).get(cur_key, [0.0, 0])
            n = int(acc[1])
            s_end = next(e for (s, e, p) in per if s.isoformat() == cur_key)
            remaining = max(0, w.calendar.business_days_between(asof, s_end))
            avg_est = (_f(acc[0]) + expected(s_end) * remaining) / max(1, n + remaining)
            realised = {**realised, cur_key: avg_est}
        pv = px.commodity_swap_pv(self.curve(), asof, _f(T["quantity"]), _f(T["fixed_price"]), per, expected, realised, T["pay_fixed"])
        an = {"spot": spot, "fixed_price": T["fixed_price"], "strip": {s.isoformat(): (realised.get(s.isoformat(), expected(e))) for (s, e, p) in per if p > asof},
              "delta_units": (_f(T["quantity"]) * sum(1 for (s, e, p) in per if p > asof)) * (1 if T["pay_fixed"] else -1)}
        return pv, an, st

    # ------------------------------------------------------------------ RFQ
    def fair(self, product: str, params: Dict) -> Dict:
        """Engine mid for a product: quote unit, mid level and PV (base) of the trade at mid."""
        w = self.w
        p = product.upper()
        c = self.curve()
        asof = w.current_date
        today = asof.isoformat()
        if p == "IRS":
            mat = self._maturity(params)
            N = _f(params["notional"])
            fp = self.periods(today, mat, int(params.get("fixed_months", 6)))
            fl = self.periods(today, mat, int(params.get("float_months", 3)))
            par = px.par_rate(c, asof, N, fp, fl, {})
            return {"unit": UNIT[p], "mid": par * 1e4, "pv": 0.0, "dv01": px.swap_dv01(c, asof, N, par, fp, fl, {}, bool(params.get("pay_fixed", True))), "maturity": mat}
        if p == "FRA":
            s = w.calendar.roll(px._add_months(asof, int(params["start_months"])))
            e = w.calendar.roll(px._add_months(asof, int(params["start_months"]) + int(params.get("length_months", 3))))
            f = px.fwd_rate(c, px.years(asof, s), px.years(asof, e))
            return {"unit": UNIT[p], "mid": f * 1e4, "pv": 0.0, "fra_start": s.isoformat(), "fra_end": e.isoformat(), "maturity": e.isoformat()}
        if p in ("CAP", "FLOOR"):
            mat = self._maturity(params)
            per = self.periods(today, mat, int(params.get("months", 3)))
            r = px.cap_pv(c, asof, _f(params["notional"]), _f(params["strike"]), per, self.rate_vol(), p == "CAP", {})
            return {"unit": UNIT[p], "mid": r["pv"], "pv": r["pv"], "maturity": mat, "vol": self.rate_vol()}
        if p == "SWAPTION":
            exp = w.calendar.roll(px._add_months(asof, int(params["expiry_months"])))
            swap_mat = w.calendar.roll(px._add_months(exp, 12 * int(params["swap_years"])))
            fp = self.periods(exp.isoformat(), swap_mat.isoformat(), 6)
            fl = self.periods(exp.isoformat(), swap_mat.isoformat(), 3)
            strike = _f(params["strike"]) if params.get("strike") else px.par_rate(c, asof, _f(params["notional"]), fp, fl, {})
            r = px.swaption_pv(c, asof, _f(params["notional"]), strike, exp, fp, fl, self.rate_vol(), bool(params.get("payer", True)))
            return {"unit": UNIT[p], "mid": r["pv"], "pv": r["pv"], "expiry": exp.isoformat(), "swap_maturity": swap_mat.isoformat(), "strike": strike,
                    "forward_rate": r["forward_rate"], "vol": self.rate_vol(), "maturity": exp.isoformat()}
        if p == "XCCY":
            mat = self._maturity(params)
            ccy = params["ccy"].upper()
            spot = w.market.fx.spot[ccy]
            return {"unit": UNIT[p], "mid": self.market_basis(ccy), "pv": 0.0, "maturity": mat, "spot": spot, "ccy_notional": _f(params["usd_notional"]) / spot}
        if p == "TRS":
            mat = self._maturity(params)
            sec = w.securities[params["security_id"]]
            price = _f(w.market.last_bar(sec.id).close)
            base = TRS_BASE_SPREAD_BPS.get(w.market.state.regime, 50.0) * (1.0 if sec.liquidity_tier == "LARGE" else 1.5)
            return {"unit": UNIT[p], "mid": base, "pv": 0.0, "maturity": mat, "price": price, "notional": price * _f(params["units"])}
        if p == "CDS":
            mat = self._maturity(params, default_years=5)
            ref = params["reference"]
            sec = w.securities[ref]
            mkt = self.reference_spread_bps(ref)
            per = self.periods(today, mat, 3)
            running = _f(params.get("running_bps", 100 if mkt < 300 else 500))
            h = px.hazard_from_spread(mkt, sec.recovery_rate)
            upfront = px.cds_pv(c, asof, _f(params["notional"]), running, h, sec.recovery_rate, per, True)   # buyer pays positive upfront
            return {"unit": UNIT[p], "mid": mkt, "pv": upfront, "maturity": mat, "running_bps": running, "recovery": sec.recovery_rate, "issuer": sec.issuer}
        if p == "COMMODITY_SWAP":
            mat = self._maturity(params, default_years=1)
            code = params["code"].upper()
            per = self.periods(today, mat, int(params.get("months", 1)))
            curve = w.market.curve_for(code)
            spot = w.market.spot(code)
            strip = []
            for (s, e, pd_) in per:
                k = e.strftime("%Y-%m")
                strip.append(curve.get(k, spot))
            fixed = sum(strip) / len(strip) if strip else spot
            return {"unit": UNIT[p], "mid": fixed, "pv": 0.0, "maturity": mat, "strip": strip, "spot": spot}
        raise ValueError(f"unknown product {product}")

    def _maturity(self, params: Dict, default_years: int = 5) -> str:
        w = self.w
        if params.get("maturity"):
            return w.calendar.roll(date.fromisoformat(params["maturity"])).isoformat()
        yrs = _f(params.get("tenor_years", default_years))
        return w.calendar.roll(px._add_months(w.current_date, int(round(yrs * 12)))).isoformat()

    def request_quote(self, pf: Portfolio, product: str, params: Dict) -> RFQ:
        from ..world import CommandError
        w = self.w
        p = product.upper()
        if p not in PRODUCTS:
            raise CommandError(f"unknown product {p}; choose from {', '.join(PRODUCTS)}")
        job = w.careers.job_for(pf)
        if job and job.allowed_classes and not ("OTC" in job.allowed_classes or ("OTC_RATES" in job.allowed_classes and p in RATES_PRODUCTS)):
            raise CommandError(f"OTC {p} is outside this job's mandate")
        hard = w.risk.hard_breaches(pf)
        if hard:
            raise CommandError(f"hard risk limit breached ({'; '.join(hard)}): no new OTC trades until the book is back within limits (terminations are allowed)")
        params = self._norm_params(p, params)
        fair = self.fair(p, params)
        regime = w.market.state.regime
        quotes = []
        rng = random.Random(f"{w.seed}|rfq|{pf.id}|{p}|{w.current_date.isoformat()}|{len(pf.rfqs)}")
        for dk in dealers_for(p):
            dst = w.market.dealers.state[dk]
            if dst.defaulted:
                continue
            spec = DEALERS[dk]
            hw = quote_half_width(p, dk, regime, dst.stress)
            if regime == "LIQUIDITY_STRESS" and dst.stress > 0.4 and rng.random() < 0.5:
                quotes.append({"dealer": dk, "name": spec.name, "declined": True, "note": "declined to quote (funding stress)"})
                continue
            lean = spec.skew * 0.3 * hw
            if p in ("CAP", "FLOOR", "SWAPTION"):
                bid, ask = fair["pv"] * (1 - hw) + lean * fair["pv"], fair["pv"] * (1 + hw) + lean * fair["pv"]
            elif p == "COMMODITY_SWAP":
                bid, ask = fair["mid"] * (1 - hw) + lean * fair["mid"], fair["mid"] * (1 + hw) + lean * fair["mid"]
            else:
                bid, ask = fair["mid"] - hw + lean, fair["mid"] + hw + lean
            level, cost = self._player_level(p, params, fair, bid, ask)
            quotes.append({"dealer": dk, "name": spec.name, "rating": dst.rating, "cds_bps": round(dst.cds, 1), "bid": bid, "ask": ask, "level": level,
                           "cost_vs_mid": cost, "half_width": hw, "declined": False, "note": spec.style})
        rid = w.new_id("RFQ")
        expires = w.calendar.next_business_day(w.current_date).isoformat()
        w.emit(OE.RFQ_REQUESTED, {"portfolio_id": pf.id, "rfq_id": rid, "product": p, "params": params, "quotes": quotes,
                                  "mid": {k: v for k, v in fair.items() if k != "strip"} | ({"strip": fair["strip"]} if "strip" in fair else {}), "expires": expires}, portfolio_id=pf.id)
        return pf.rfqs[rid]

    def _norm_params(self, p: str, params: Dict) -> Dict:
        from ..world import CommandError
        w = self.w
        q = {k: v for k, v in params.items() if v is not None}
        if p in ("IRS", "FRA", "CAP", "FLOOR", "SWAPTION", "XCCY", "CDS"):
            key = "usd_notional" if p == "XCCY" else "notional"
            if _f(q.get(key, 0)) <= 0:
                raise CommandError("notional must be positive")
        if p == "IRS":
            q.setdefault("pay_fixed", True)
            q.setdefault("fixed_months", 6)
            q.setdefault("float_months", 3)
        if p == "FRA":
            q.setdefault("pay_fixed", True)
            if int(q.get("start_months", 0)) < 1:
                raise CommandError("FRA start must be at least one month ahead")
        if p in ("CAP", "FLOOR"):
            q.setdefault("buyer", True)
            q.setdefault("months", 3)
            if _f(q.get("strike", 0)) <= 0:
                raise CommandError("strike rate required (e.g. 0.045)")
        if p == "SWAPTION":
            q.setdefault("buyer", True)
            q.setdefault("payer", True)
            q.setdefault("settlement", "PHYSICAL")
            if int(q.get("expiry_months", 0)) < 1 or int(q.get("swap_years", 0)) < 1:
                raise CommandError("swaption needs expiry_months >= 1 and swap_years >= 1")
        if p == "XCCY":
            from ..engines.fx_market import CURRENCIES
            if q.get("ccy", "").upper() not in CURRENCIES or q.get("ccy", "").upper() == "USD":
                raise CommandError("ccy must be a non-USD currency")
            q["ccy"] = q["ccy"].upper()
            q.setdefault("direction", "BORROW_FOREIGN")
            q.setdefault("months", 3)
        if p == "TRS":
            sec = w.securities.get(q.get("security_id", ""))
            if sec is None or sec.is_bond or sec.is_future or sec.is_option:
                raise CommandError("TRS reference must be an equity, ETF, ADR or REIT")
            if _f(q.get("units", 0)) <= 0:
                raise CommandError("units must be positive")
            q.setdefault("receiver", True)
            q.setdefault("reset_months", 1)
        if p == "CDS":
            sec = w.securities.get(q.get("reference", ""))
            if sec is None or sec.asset_class != "CORP_BOND":
                raise CommandError("CDS reference must be a corporate bond issuer (e.g. MRDN-29, PTRX-32, NGSL-30)")
            q.setdefault("buyer", True)
        if p == "COMMODITY_SWAP":
            if q.get("code", "").upper() not in w.market.commodities.state:
                raise CommandError("unknown commodity code")
            q["code"] = q["code"].upper()
            if _f(q.get("quantity", 0)) <= 0:
                raise CommandError("quantity per period must be positive")
            q.setdefault("pay_fixed", True)
            q.setdefault("months", 1)
        return q

    def _player_level(self, p: str, params: Dict, fair: Dict, bid: float, ask: float) -> Tuple[float, float]:
        """The level the player would deal at with this dealer and its cost vs mid in base currency."""
        N = _f(params.get("notional", params.get("usd_notional", 0)))
        if p == "IRS":
            level = ask if params["pay_fixed"] else bid
            return level, abs(level - fair["mid"]) * abs(fair["dv01"])
        if p == "FRA":
            level = ask if params["pay_fixed"] else bid
            tau = px.yearfrac(date.fromisoformat(fair["fra_start"]), date.fromisoformat(fair["fra_end"]), "ACT/360")
            return level, abs(level - fair["mid"]) * 1e-4 * N * tau
        if p in ("CAP", "FLOOR", "SWAPTION"):
            level = ask if params["buyer"] else bid
            return level, abs(level - fair["pv"])
        if p == "XCCY":
            level = ask if params["direction"] == "BORROW_FOREIGN" else bid     # basis we pay on the foreign leg
            yrs = px.years(self.w.current_date, date.fromisoformat(fair["maturity"]))
            return level, abs(level - fair["mid"]) * 1e-4 * N * yrs
        if p == "TRS":
            level = ask if params["receiver"] else bid
            yrs = px.years(self.w.current_date, date.fromisoformat(fair["maturity"]))
            return level, abs(level - fair["mid"]) * 1e-4 * fair["notional"] * yrs
        if p == "CDS":
            level = ask if params["buyer"] else bid
            yrs = px.years(self.w.current_date, date.fromisoformat(fair["maturity"]))
            return level, abs(level - fair["mid"]) * 1e-4 * N * yrs * 0.9
        if p == "COMMODITY_SWAP":
            level = ask if params["pay_fixed"] else bid
            n_per = len(fair["strip"])
            return level, abs(level - fair["mid"]) * _f(params["quantity"]) * n_per
        return fair["mid"], 0.0

    def execute_rfq(self, pf: Portfolio, rfq_id: str, dealer: str) -> OTCTrade:
        from ..world import CommandError
        w = self.w
        r = pf.rfqs.get(rfq_id)
        if r is None:
            raise CommandError(f"unknown RFQ {rfq_id}")
        if r.status != "OPEN":
            raise CommandError(f"RFQ {rfq_id} is {r.status}")
        dealer = dealer.upper()
        q = next((x for x in r.quotes if x["dealer"] == dealer), None)
        if q is None or q.get("declined"):
            raise CommandError(f"{dealer} did not quote this RFQ")
        if w.market.dealers.state[dealer].defaulted:
            raise CommandError(f"{dealer} is in default")
        terms, notional, ccy, start, maturity, initial = self._build_terms(r, q)
        # independent amount + upfront must be financeable
        csa = self.csa(pf, dealer)
        im = money(D(repr(csa.im_pct.get(r.product, 0.0))) * notional)
        upfront = ZERO
        for f in initial:
            for leg in (f.get("legs") or [{"currency": f["currency"], "amount": f["amount"]}]):
                if leg["currency"] == pf.base_currency:
                    upfront -= D(str(leg["amount"]))
        need = im + max(ZERO, upfront)
        why = w.prime.affordable(pf, need, None) if need > 0 else None
        if why:
            raise CommandError(f"initial margin {im:,.0f} plus upfront {max(ZERO, upfront):,.0f} not financeable: {why}")
        tid = w.new_id("OTC")
        ev = w.emit(OE.RFQ_EXECUTED, {"portfolio_id": pf.id, "rfq_id": r.id, "dealer": dealer, "trade_id": tid, "level": q["level"]}, portfolio_id=pf.id)
        w.emit(OE.OTC_TRADE_OPENED, {"portfolio_id": pf.id, "trade_id": tid, "product": r.product, "counterparty": dealer, "notional": notional, "currency": ccy,
                                     "start": start, "maturity": maturity, "terms": terms, "initial_cashflows": initial, "rfq_id": r.id,
                                     "dealer_level": q["level"], "mid": r.mid.get("mid"), "cost_vs_mid": q.get("cost_vs_mid", 0.0)}, cause_id=ev.id, portfolio_id=pf.id)
        return pf.otc_trades[tid]

    def _build_terms(self, r: RFQ, q: Dict):
        w = self.w
        p, P, M = r.product, r.params, r.mid
        today = w.current_date.isoformat()
        level = q["level"]
        base = w.base_currency
        initial: List[Dict] = []
        if p == "IRS":
            N = money(P["notional"])
            terms = {"pay_fixed": bool(P["pay_fixed"]), "fixed_rate": level / 1e4, "fixed_months": int(P["fixed_months"]), "float_months": int(P["float_months"]), "index": f"TERM{int(P['float_months'])}M"}
            return terms, N, base, today, M["maturity"], initial
        if p == "FRA":
            N = money(P["notional"])
            terms = {"pay_fixed": bool(P["pay_fixed"]), "rate": level / 1e4, "fra_start": M["fra_start"], "fra_end": M["fra_end"]}
            return terms, N, base, today, M["fra_end"], initial
        if p in ("CAP", "FLOOR"):
            N = money(P["notional"])
            terms = {"buyer": bool(P["buyer"]), "strike": _f(P["strike"]), "months": int(P["months"]), "premium": level}
            initial.append({"kind": "PREMIUM", "currency": base, "amount": -level if P["buyer"] else level, "note": f"{p.lower()} premium {'paid' if P['buyer'] else 'received'}"})
            return terms, N, base, today, M["maturity"], initial
        if p == "SWAPTION":
            N = money(P["notional"])
            terms = {"buyer": bool(P["buyer"]), "payer": bool(P["payer"]), "strike": M["strike"], "expiry": M["expiry"], "swap_maturity": M["swap_maturity"],
                     "fixed_months": 6, "float_months": 3, "settlement": P.get("settlement", "PHYSICAL").upper(), "premium": level}
            initial.append({"kind": "PREMIUM", "currency": base, "amount": -level if P["buyer"] else level, "note": f"swaption premium {'paid' if P['buyer'] else 'received'}"})
            return terms, N, base, today, M["expiry"], initial
        if p == "XCCY":
            N = money(P["usd_notional"])
            ccy = P["ccy"]
            ccy_n = money(D(repr(M["ccy_notional"])))
            terms = {"ccy": ccy, "usd_notional": N, "ccy_notional": ccy_n, "direction": P["direction"], "basis_bps": level, "months": int(P["months"]), "spot_at_start": M["spot"]}
            if P["direction"] == "BORROW_FOREIGN":
                initial.append({"kind": "NOTIONAL_EXCHANGE", "legs": [{"currency": base, "amount": -N}, {"currency": ccy, "amount": ccy_n}], "note": "initial exchange: pay USD, receive foreign"})
            else:
                initial.append({"kind": "NOTIONAL_EXCHANGE", "legs": [{"currency": base, "amount": N}, {"currency": ccy, "amount": -ccy_n}], "note": "initial exchange: receive USD, pay foreign"})
            return terms, N, base, today, M["maturity"], initial
        if p == "TRS":
            units = money(P["units"])
            N = money(D(repr(M["notional"])))
            terms = {"security_id": P["security_id"], "units": units, "receiver": bool(P["receiver"]), "spread_bps": level, "reset_months": int(P["reset_months"]), "initial_price": M["price"]}
            return terms, N, base, today, M["maturity"], initial
        if p == "CDS":
            N = money(P["notional"])
            sec = w.securities[P["reference"]]
            per = self.periods(today, M["maturity"], 3)
            h = px.hazard_from_spread(level, M["recovery"])
            upfront = px.cds_pv(self.curve(), w.current_date, _f(N), M["running_bps"], h, M["recovery"], per, True)   # buyer's view at the dealt spread
            terms = {"reference": P["reference"], "issuer": sec.issuer, "buyer": bool(P["buyer"]), "running_bps": M["running_bps"], "recovery": M["recovery"],
                     "dealt_spread_bps": level, "upfront": upfront}
            amt = -upfront if P["buyer"] else upfront
            if abs(amt) >= 0.01:
                initial.append({"kind": "UPFRONT", "currency": base, "amount": amt, "note": f"CDS upfront at {level:.0f}bp vs {M['running_bps']:.0f} running"})
            return terms, N, base, today, M["maturity"], initial
        if p == "COMMODITY_SWAP":
            qty = money(P["quantity"])
            n_per = len(M["strip"])
            N = money(D(repr(level)) * qty * n_per)
            terms = {"code": P["code"], "quantity": qty, "fixed_price": level, "months": int(P["months"]), "pay_fixed": bool(P["pay_fixed"])}
            return terms, N, base, today, M["maturity"], initial
        raise ValueError(p)

    # ------------------------------------------------------------------ commands: terminate, credit events, defaults
    def unwind_cost(self, t: OTCTrade) -> Decimal:
        w = self.w
        dst = w.market.dealers.state[t.counterparty]
        hw = quote_half_width(t.product, t.counterparty, w.market.state.regime, dst.stress)
        an = t.analytics
        if t.product in ("IRS", "FRA"):
            c = hw * abs(an.get("dv01", 0.0))
        elif t.product in ("CAP", "FLOOR", "SWAPTION"):
            c = hw * abs(_f(t.mtm))
        elif t.product == "CDS":
            c = hw * abs(an.get("cs01", 0.0))
        elif t.product == "XCCY":
            c = hw * 1e-4 * _f(t.notional) * px.years(w.current_date, date.fromisoformat(t.maturity))
        elif t.product == "TRS":
            c = hw * 1e-4 * _f(t.notional) * max(0.1, px.years(w.current_date, date.fromisoformat(t.maturity)))
        else:
            c = hw * abs(an.get("delta_units", 0.0)) * an.get("spot", 0.0)
        return money(D(repr(max(c, 0.0))))

    def terminate(self, pf: Portfolio, trade_id: str, cause: Optional[Event] = None, reason: str = "terminated by player", cost_mult: float = 1.0) -> Decimal:
        from ..world import CommandError
        w = self.w
        t = pf.otc_trades.get(trade_id)
        if t is None:
            raise CommandError(f"unknown OTC trade {trade_id}")
        if not t.is_open:
            raise CommandError(f"{trade_id} is {t.status}")
        pv, an, st = self.value(pf, t)
        cost = money(self.unwind_cost(t) * D(repr(cost_mult)))
        amount = pv - cost
        ev = w.emit(OE.OTC_TRADE_TERMINATED, {"portfolio_id": pf.id, "trade_id": t.id, "mtm": pv, "cost": cost, "settlement": amount, "reason": reason},
                    cause_id=cause.id if cause else None, portfolio_id=pf.id)
        if amount != 0:
            self._cashflow(pf, t, "TERMINATION", amount, pf.base_currency, f"close-out at {pv:,.2f} less {cost:,.2f} unwind cost", ev, derived=False)
        return amount

    def credit_event(self, reference: str, cause: Optional[Event] = None, recovery: Optional[float] = None) -> Event:
        from ..world import CommandError
        w = self.w
        sec = w.securities.get(reference)
        if sec is None or sec.asset_class != "CORP_BOND":
            raise CommandError("credit events apply to corporate bond issuers")
        rec = recovery if recovery is not None else sec.recovery_rate
        return w.emit(OE.CREDIT_EVENT, {"reference": reference, "issuer": sec.issuer, "recovery": rec, "note": "failure to pay / bankruptcy: CDS settle at recovery"},
                      cause_id=cause.id if cause else None)

    def default_counterparty(self, dealer: str, cause: Optional[Event] = None, recovery: float = DEFAULT_RECOVERY) -> Event:
        from ..world import CommandError
        w = self.w
        dealer = dealer.upper()
        if dealer not in DEALERS:
            raise CommandError(f"unknown dealer {dealer}")
        if w.market.dealers.state[dealer].defaulted:
            raise CommandError(f"{dealer} already defaulted")
        return w.emit(OE.COUNTERPARTY_DEFAULTED, {"dealer": dealer, "name": DEALERS[dealer].name, "recovery": recovery,
                                                 "note": "ISDA early termination: netting set closed out at mid; unsecured claims recover at the recovery rate"},
                      cause_id=cause.id if cause else None)

    # ------------------------------------------------------------------ daily processing
    def process_day(self, cause: Event, prev: date) -> None:
        w = self.w
        today = w.current_date
        for pf in w.portfolios.values():
            for r in list(pf.rfqs.values()):
                if r.status == "OPEN" and r.expires <= today.isoformat():
                    w.emit(OE.RFQ_EXPIRED, {"portfolio_id": pf.id, "rfq_id": r.id}, cause_id=cause.id, portfolio_id=pf.id)
            for t in list(pf.otc_trades.values()):
                if t.is_open:
                    self._lifecycle(pf, t, cause, prev)
            for t in list(pf.otc_trades.values()):
                if t.is_open:
                    self.mark(pf, t, cause)
            self._margin(pf, cause)

    def mark(self, pf: Portfolio, t: OTCTrade, cause: Event, derived: bool = False) -> None:
        w = self.w
        pv, an, st = self.value(pf, t)
        payload = {"portfolio_id": pf.id, "trade_id": t.id, "mtm": pv, "delta": pv - t.mtm, "analytics": an, "state": st}
        if derived:
            w.derive(OE.OTC_TRADE_MARKED, payload, cause, portfolio_id=pf.id)
        else:
            w.emit(OE.OTC_TRADE_MARKED, payload, cause_id=cause.id, portfolio_id=pf.id)

    def _fix(self, pf: Portfolio, t: OTCTrade, key: str, value: float, cause: Event, note: str) -> None:
        self.w.emit(OE.OTC_FIXING, {"portfolio_id": pf.id, "trade_id": t.id, "key": key, "value": value, "note": note}, cause_id=cause.id, portfolio_id=pf.id)

    def _cashflow(self, pf: Portfolio, t: OTCTrade, kind: str, amount: Decimal, ccy: str, note: str, cause: Event, legs: Optional[List[Dict]] = None,
                  derived: bool = False) -> None:
        w = self.w
        payload = {"portfolio_id": pf.id, "trade_id": t.id, "kind": kind, "note": note,
                   "legs": legs if legs is not None else [{"currency": ccy, "amount": money(amount)}]}
        if derived:
            w.derive(OE.OTC_CASHFLOW, payload, cause, portfolio_id=pf.id)
        else:
            w.emit(OE.OTC_CASHFLOW, payload, cause_id=cause.id, portfolio_id=pf.id)

    def _status(self, pf: Portfolio, t: OTCTrade, status: str, cause: Event, note: str) -> None:
        self.w.emit(OE.OTC_TRADE_TERMINATED, {"portfolio_id": pf.id, "trade_id": t.id, "mtm": ZERO, "cost": ZERO, "settlement": ZERO, "reason": note, "status": status},
                    cause_id=cause.id, portfolio_id=pf.id)

    def _lifecycle(self, pf: Portfolio, t: OTCTrade, cause: Event, prev: date) -> None:
        w = self.w
        today = w.current_date
        iso = today.isoformat()
        T = t.terms
        N = _f(t.notional)
        p = t.product
        paid = set(t.state.get("paid", []))
        days = max(1, (today - prev).days)
        if p == "IRS":
            fl = self.periods(t.start, t.maturity, T["float_months"])
            fp = self.periods(t.start, t.maturity, T["fixed_months"])
            for (s, e, pd_) in fl:
                if s <= today and s.isoformat() not in t.fixings:
                    self._fix(pf, t, s.isoformat(), self.term_rate(T["float_months"]), cause, f"{T['index']} fixing for {s.isoformat()}–{e.isoformat()}")
            for (s, e, pd_) in fl:
                key = "FLOAT:" + pd_.isoformat()
                if pd_ <= today and key not in paid:
                    r = t.fixings.get(s.isoformat(), self.term_rate(T["float_months"]))
                    amt = N * r * px.yearfrac(s, e, "ACT/360")
                    self._cashflow(pf, t, "FLOAT_COUPON", D(repr(amt if T["pay_fixed"] else -amt)), t.currency, f"floating {r:.4%} on {s.isoformat()}–{e.isoformat()}", cause)
                    paid.add(key)
            for (s, e, pd_) in fp:
                key = "FIXED:" + pd_.isoformat()
                if pd_ <= today and key not in paid:
                    amt = N * T["fixed_rate"] * px.yearfrac(s, e, "30/360")
                    self._cashflow(pf, t, "FIXED_COUPON", D(repr(-amt if T["pay_fixed"] else amt)), t.currency, f"fixed {T['fixed_rate']:.4%} on {s.isoformat()}–{e.isoformat()}", cause)
                    paid.add(key)
            if paid != set(t.state.get("paid", [])):
                t.state["paid"] = sorted(paid)       # persisted through the day's mark payload
            if today >= date.fromisoformat(t.maturity):
                self._status(pf, t, "MATURED", cause, "final payments made; swap matured")
        elif p == "FRA":
            s = date.fromisoformat(T["fra_start"])
            if today >= s:
                fixing = self.term_rate(int(round(px.yearfrac(s, date.fromisoformat(T["fra_end"]), "ACT/360") * 12)) or 3)
                self._fix(pf, t, T["fra_start"], fixing, cause, "FRA fixing")
                t.fixings[T["fra_start"]] = fixing
                tau = px.yearfrac(s, date.fromisoformat(T["fra_end"]), "ACT/360")
                settle = N * (fixing - T["rate"]) * tau / (1 + fixing * tau)
                self._cashflow(pf, t, "FRA_SETTLEMENT", D(repr(settle if T["pay_fixed"] else -settle)), t.currency, f"FRA settled at fixing {fixing:.4%} vs {T['rate']:.4%}", cause)
                self._status(pf, t, "MATURED", cause, "FRA settled at the fixing")
        elif p in ("CAP", "FLOOR"):
            per = self.periods(t.start, t.maturity, T["months"])
            for (s, e, pd_) in per:
                if s <= today and s.isoformat() not in t.fixings:
                    self._fix(pf, t, s.isoformat(), self.term_rate(T["months"]), cause, f"caplet fixing for {s.isoformat()}")
                key = "PAY:" + pd_.isoformat()
                if pd_ <= today and key not in paid:
                    r = t.fixings.get(s.isoformat(), self.term_rate(T["months"]))
                    intrinsic = max(0.0, (r - T["strike"]) if p == "CAP" else (T["strike"] - r))
                    amt = N * intrinsic * px.yearfrac(s, e, "ACT/360")
                    if amt > 0.005:
                        self._cashflow(pf, t, "OPTION_PAYOFF", D(repr(amt if T["buyer"] else -amt)), t.currency, f"{p.lower()}let payoff: fixing {r:.4%} vs strike {T['strike']:.4%}", cause)
                    paid.add(key)
            if paid != set(t.state.get("paid", [])):
                t.state["paid"] = sorted(paid)
            if today >= date.fromisoformat(t.maturity):
                self._status(pf, t, "MATURED", cause, "last caplet settled")
        elif p == "SWAPTION":
            exp = date.fromisoformat(T["expiry"])
            if today >= exp:
                fp = self.periods(T["expiry"], T["swap_maturity"], T["fixed_months"])
                fl = self.periods(T["expiry"], T["swap_maturity"], T["float_months"])
                F = px.par_rate(self.curve(), today, N, fp, fl, {})
                itm = (F > T["strike"]) if T["payer"] else (F < T["strike"])
                if itm:
                    ann = px.swap_legs(self.curve(), today, N, 1.0, fp, fl, {})["annuity"]
                    value = N * ann * abs(F - T["strike"])
                    ev = w.emit(OE.OTC_TRADE_EXERCISED, {"portfolio_id": pf.id, "trade_id": t.id, "forward_rate": F, "strike": T["strike"], "value": value,
                                                          "settlement": T["settlement"]}, cause_id=cause.id, portfolio_id=pf.id)
                    if T["settlement"] == "CASH" or not T["buyer"]:
                        # cash-settled, or we were short: settle the intrinsic value in cash
                        self._cashflow(pf, t, "EXERCISE_SETTLEMENT", D(repr(value if T["buyer"] else -value)), t.currency,
                                       f"swaption cash settlement: forward {F:.4%} vs strike {T['strike']:.4%}", ev)
                    else:
                        tid = w.new_id("OTC")
                        terms = {"pay_fixed": T["payer"], "fixed_rate": T["strike"], "fixed_months": T["fixed_months"], "float_months": T["float_months"], "index": "TERM3M",
                                 "from_swaption": t.id}
                        w.emit(OE.OTC_TRADE_OPENED, {"portfolio_id": pf.id, "trade_id": tid, "product": "IRS", "counterparty": t.counterparty, "notional": t.notional,
                                                     "currency": t.currency, "start": T["expiry"], "maturity": T["swap_maturity"], "terms": terms, "initial_cashflows": [],
                                                     "rfq_id": None, "dealer_level": T["strike"] * 1e4, "mid": F * 1e4, "cost_vs_mid": 0.0}, cause_id=ev.id, portfolio_id=pf.id)
                    self._status(pf, t, "EXERCISED", cause, f"exercised at expiry (forward {F:.4%} vs strike {T['strike']:.4%})")
                else:
                    self._status(pf, t, "EXPIRED", cause, f"expired out of the money (forward {F:.4%} vs strike {T['strike']:.4%})")
        elif p == "XCCY":
            per = self.periods(t.start, t.maturity, T["months"])
            ccy = T["ccy"]
            n_usd, n_ccy = _f(T["usd_notional"]), _f(T["ccy_notional"])
            for (s, e, pd_) in per:
                if s <= today:
                    if "USD:" + s.isoformat() not in t.fixings:
                        self._fix(pf, t, "USD:" + s.isoformat(), self.term_rate(T["months"]), cause, "USD leg fixing")
                    if ccy + ":" + s.isoformat() not in t.fixings:
                        self._fix(pf, t, ccy + ":" + s.isoformat(), w.market.fx.rate[ccy], cause, f"{ccy} leg fixing")
                key = "PAY:" + pd_.isoformat()
                if pd_ <= today and key not in paid:
                    tau = px.yearfrac(s, e, "ACT/360")
                    usd_i = n_usd * t.fixings.get("USD:" + s.isoformat(), self.term_rate(T["months"])) * tau
                    ccy_i = n_ccy * (t.fixings.get(ccy + ":" + s.isoformat(), w.market.fx.rate[ccy]) + T["basis_bps"] / 1e4) * tau
                    borrow = T["direction"] == "BORROW_FOREIGN"
                    legs = [{"currency": "USD", "amount": money(D(repr(usd_i if borrow else -usd_i)))}, {"currency": ccy, "amount": money(D(repr(-ccy_i if borrow else ccy_i)))}]
                    self._cashflow(pf, t, "XCCY_INTEREST", ZERO, "USD", f"interest exchange for {s.isoformat()}–{e.isoformat()}", cause, legs=legs)
                    paid.add(key)
            if paid != set(t.state.get("paid", [])):
                t.state["paid"] = sorted(paid)
            if today >= date.fromisoformat(t.maturity):
                borrow = T["direction"] == "BORROW_FOREIGN"
                legs = [{"currency": "USD", "amount": money(D(repr(n_usd if borrow else -n_usd)))}, {"currency": ccy, "amount": money(D(repr(-n_ccy if borrow else n_ccy)))}]
                self._cashflow(pf, t, "NOTIONAL_EXCHANGE", ZERO, "USD", "final exchange of notionals", cause, legs=legs)
                self._status(pf, t, "MATURED", cause, "notionals re-exchanged; swap matured")
        elif p == "TRS":
            sid = T["security_id"]
            price = _f(w.market.last_bar(sid).close)
            st = t.state
            if "reset_price" not in st:
                st["reset_price"] = T["initial_price"]
                st["reset_date"] = t.start
            fin_rate = self.curve().policy_rate + T["spread_bps"] / 1e4
            if t.trade_date != iso:
                st["financing"] = _f(st.get("financing", 0)) + _f(t.notional) * fin_rate * days / 360.0
            for ca in w.corporate_actions.values():
                if ca.security_id == sid and ca.ex_date == iso and ca.action_type == "CASH_DIVIDEND":
                    st["dividends"] = _f(st.get("dividends", 0)) + _f(ca.amount_per_unit) * _f(T["units"])
                    st.setdefault("dividend_log", []).append({"date": iso, "amount": _f(ca.amount_per_unit) * _f(T["units"])})
            resets = self.periods(t.start, t.maturity, T["reset_months"])
            for (s, e, pd_) in resets:
                key = "RESET:" + pd_.isoformat()
                if pd_ <= today and key not in paid:
                    units = _f(T["units"])
                    tr = units * (price - _f(st["reset_price"])) + _f(st.get("dividends", 0))
                    fin = _f(st.get("financing", 0))
                    net = (tr - fin) if T["receiver"] else (fin - tr)
                    self._cashflow(pf, t, "TRS_RESET", D(repr(net)), t.currency,
                                   f"reset: total return {tr:,.2f} (price {st['reset_price']:.4f} → {price:.4f}, dividends {_f(st.get('dividends', 0)):,.2f}) less financing {fin:,.2f}", cause)
                    st["reset_price"], st["reset_date"], st["dividends"], st["financing"] = price, iso, 0.0, 0.0
                    st["notional"] = units * price
                    paid.add(key)
            st["paid"] = sorted(paid)
            t.state = st
            if today >= date.fromisoformat(t.maturity):
                self._status(pf, t, "MATURED", cause, "final reset paid; swap matured")
        elif p == "CDS":
            st = t.state
            per = self.periods(t.start, t.maturity, 3)
            if t.trade_date != iso:
                st["accrued"] = _f(st.get("accrued", 0)) + N * T["running_bps"] / 1e4 * days / 360.0
            for (s, e, pd_) in per:
                key = "PREM:" + pd_.isoformat()
                if pd_ <= today and key not in paid:
                    amt = N * T["running_bps"] / 1e4 * px.yearfrac(s, e, "ACT/360")
                    self._cashflow(pf, t, "CDS_PREMIUM", D(repr(-amt if T["buyer"] else amt)), t.currency, f"running premium {T['running_bps']:.0f}bp for {s.isoformat()}–{e.isoformat()}", cause)
                    st["accrued"] = max(0.0, _f(st.get("accrued", 0)) - amt)
                    paid.add(key)
            st["paid"] = sorted(paid)
            t.state = st
            if today >= date.fromisoformat(t.maturity):
                self._status(pf, t, "MATURED", cause, "protection expired unused")
        elif p == "COMMODITY_SWAP":
            st = t.state
            per = self.periods(t.start, t.maturity, T["months"])
            spot = w.market.spot(T["code"])
            acc = {k: list(v) for k, v in st.get("acc", {}).items()}       # period start -> [sum of daily spots, days]
            cur = next(((s, e, pd_) for (s, e, pd_) in per if s <= today < e), None)
            if cur:
                a = acc.setdefault(cur[0].isoformat(), [0.0, 0])
                a[0] += spot
                a[1] += 1
            for (s, e, pd_) in per:
                key = "SETTLE:" + pd_.isoformat()
                if pd_ <= today and key not in paid:
                    a = acc.pop(s.isoformat(), None)
                    avg = (a[0] / a[1]) if a and a[1] > 0 else spot
                    realised = dict(st.get("realised", {}))
                    realised[s.isoformat()] = avg
                    st["realised"] = realised
                    amt = _f(T["quantity"]) * (avg - T["fixed_price"])
                    self._cashflow(pf, t, "COMMODITY_SETTLEMENT", D(repr(amt if T["pay_fixed"] else -amt)), t.currency,
                                   f"period {s.isoformat()}–{e.isoformat()}: average {avg:.4f} vs fixed {T['fixed_price']:.4f} x {_f(T['quantity']):,.0f}", cause)
                    paid.add(key)
            st["acc"] = acc
            st["paid"] = sorted(paid)
            t.state = st
            if today >= date.fromisoformat(t.maturity):
                self._status(pf, t, "MATURED", cause, "last period settled")

    # ------------------------------------------------------------------ CSA margining
    def netting_sets(self, pf: Portfolio) -> Dict[str, List[OTCTrade]]:
        out: Dict[str, List[OTCTrade]] = {}
        for t in pf.otc_trades.values():
            if t.is_open:
                out.setdefault(t.counterparty, []).append(t)
        return out

    def required_im(self, pf: Portfolio, dealer: str, extra: Optional[List[Tuple[str, Decimal]]] = None) -> Decimal:
        csa = self.csa(pf, dealer)
        total = ZERO
        for t in pf.otc_trades.values():
            if t.is_open and t.counterparty == dealer:
                total += money(D(repr(csa.im_pct.get(t.product, 0.0))) * t.notional)
        for prod, n in (extra or []):
            total += money(D(repr(csa.im_pct.get(prod, 0.0))) * n)
        return total

    def _margin(self, pf: Portfolio, cause: Event) -> None:
        w = self.w
        sets = self.netting_sets(pf)
        for dealer, csa in list(pf.csas.items()):
            if csa.status != "ACTIVE":
                continue
            trades = sets.get(dealer, [])
            net = sum((t.mtm for t in trades), ZERO)
            # variation margin: target holdings above the threshold either way
            target_recv = max(ZERO, net - csa.threshold)
            target_post = max(ZERO, -net - csa.threshold)
            if not trades:
                target_recv = target_post = ZERO
            moves: List[Tuple[str, Decimal]] = []
            d_recv = target_recv - csa.vm_received
            if d_recv != 0 and (abs(d_recv) >= csa.mta or target_recv == 0):
                moves.append(("VM_RECEIVED", d_recv))
            d_post = target_post - csa.vm_posted
            if d_post != 0 and (abs(d_post) >= csa.mta or target_post == 0):
                moves.append(("VM_POSTED", d_post))
            # independent amounts
            im_req = self.required_im(pf, dealer)
            d_im = im_req - csa.im_posted
            if d_im != 0 and (abs(d_im) >= csa.mta or im_req == 0):
                moves.append(("IM_POSTED", d_im))
            if im_req != csa.im_received:
                moves.append(("IM_RECEIVED", im_req - csa.im_received))
            call = w.collateral.open_call(pf, "OTC", dealer)
            shortfall = ZERO
            for kind, amt in moves:
                if kind in ("VM_POSTED", "IM_POSTED") and amt > 0:
                    why = w.prime.affordable(pf, amt, None)
                    if why:
                        shortfall += amt
                        continue
                w.emit(OE.CSA_MARGIN_MOVED, {"portfolio_id": pf.id, "dealer": dealer, "kind": kind, "amount": amt, "net_mtm": net, "threshold": csa.threshold,
                                             "mta": csa.mta, "currency": pf.base_currency}, cause_id=cause.id, portfolio_id=pf.id)
            if shortfall > 0:
                if call is not None and call.cycles_open >= CSA_CALL_GRACE_CYCLES:
                    self.close_out(pf, dealer, cause, f"unmet CSA call {call.id}: {DEALERS[dealer].name} terminated the netting set")
                    w.collateral.resolve_call(pf, call, "FORCED", "dealer closed out the netting set at mid less unwind costs", cause)
                else:
                    w.collateral.issue_or_update_call(pf, "OTC", dealer, shortfall, f"CSA margin call from {DEALERS[dealer].name}: {shortfall:,.0f} of variation/initial margin could not be funded", cause)
            elif call is not None:
                w.collateral.resolve_call(pf, call, "MET", "margin posted", cause)

    def close_out(self, pf: Portfolio, dealer: str, cause: Event, reason: str, cost_mult: float = 1.5) -> Decimal:
        total = ZERO
        for t in list(pf.otc_trades.values()):
            if t.is_open and t.counterparty == dealer:
                total += self.terminate(pf, t.id, cause, reason, cost_mult)
        return total

    # ------------------------------------------------------------------ exposure
    def exposure(self, pf: Portfolio) -> Dict:
        w = self.w
        nav = w.pnl.compute_summary(pf)["nav"]
        rows = []
        sets = self.netting_sets(pf)
        for dealer, spec in DEALERS.items():
            trades = sets.get(dealer, [])
            csa = pf.csas.get(dealer)
            dst = w.market.dealers.state[dealer]
            pos = sum((t.mtm for t in trades if t.mtm > 0), ZERO)
            neg = sum((t.mtm for t in trades if t.mtm < 0), ZERO)
            net = pos + neg
            vm_recv = csa.vm_received if csa else ZERO
            vm_post = csa.vm_posted if csa else ZERO
            im_post = csa.im_posted if csa else ZERO
            current = max(ZERO, net - vm_recv) + vm_post
            addon = ZERO
            for t in trades:
                T_ = max(0.25, px.years(w.current_date, date.fromisoformat(t.maturity)))
                addon += money(D(repr(PFE_ADDON[t.product] * math.sqrt(T_))) * t.notional)
            ngr = float(max(ZERO, net) / pos) if pos > 0 else 0.0
            pfe = max(ZERO, net - vm_recv) + money(addon * D(repr(0.4 + 0.6 * ngr)))
            pd1 = w.market.dealers.default_probability_1y(dealer)
            rows.append({"dealer": dealer, "name": spec.name, "rating": dst.rating, "cds_bps": dst.cds, "stress": dst.stress, "defaulted": dst.defaulted,
                         "trades": len(trades), "gross_positive": pos, "gross_negative": neg, "net_mtm": net, "vm_received": vm_recv, "vm_posted": vm_post,
                         "im_posted": im_post, "im_received": csa.im_received if csa else ZERO, "current_exposure": current, "pfe": pfe,
                         "expected_loss_1y": money(pfe * D(repr(pd1 * (1 - DEFAULT_RECOVERY)))), "pd_1y": pd1, "netting_benefit": pos - max(ZERO, net),
                         "pct_nav": float(current / nav) if nav else 0.0, "threshold": csa.threshold if csa else D(CSA_TERMS[dealer]["threshold"]),
                         "mta": csa.mta if csa else D(CSA_TERMS[dealer]["mta"]), "csa_status": csa.status if csa else "NONE",
                         "call": w.collateral.open_call(pf, "OTC", dealer)})
        return {"rows": rows, "total_current_exposure": sum((r["current_exposure"] for r in rows), ZERO), "total_pfe": sum((r["pfe"] for r in rows), ZERO),
                "method": "current exposure = max(0, net MTM − VM received) + VM posted; PFE = current + add-on (notional × product factor × √T) × (0.4 + 0.6 × net/gross); "
                          "expected loss = PFE × 1y PD (from the dealer's CDS) × (1 − 40% recovery). Simplified current-exposure method, documented as such."}

    def upcoming(self, pf: Portfolio, horizon_bd: int = 5) -> List[Dict]:
        """Scheduled OTC events within `horizon_bd` business days: payments, fixings, resets, expiries, maturities."""
        w = self.w
        today = w.current_date
        out: List[Dict] = []

        def add(t, d: date, kind: str, note: str):
            bd = w.calendar.business_days_between(today, d)
            if 0 <= bd <= horizon_bd and d > today:
                out.append({"date": d.isoformat(), "business_days": bd, "trade_id": t.id, "product": t.product, "counterparty": t.counterparty, "kind": kind, "note": note})
        for t in pf.otc_trades.values():
            if not t.is_open:
                continue
            T = t.terms
            paid = set(t.state.get("paid", []))
            mat = date.fromisoformat(t.maturity)
            if t.product == "IRS":
                for (s, e, pd_) in self.periods(t.start, t.maturity, T["fixed_months"]):
                    if "FIXED:" + pd_.isoformat() not in paid:
                        add(t, pd_, "FIXED_COUPON", f"fixed {T['fixed_rate']:.4%} x {px.yearfrac(s, e, '30/360'):.3f}y")
                for (s, e, pd_) in self.periods(t.start, t.maturity, T["float_months"]):
                    if "FLOAT:" + pd_.isoformat() not in paid:
                        add(t, pd_, "FLOAT_COUPON", "floating coupon")
                    if s > today and s.isoformat() not in t.fixings:
                        add(t, s, "FIXING", f"{T['index']} fixing")
            elif t.product == "FRA":
                add(t, date.fromisoformat(T["fra_start"]), "FRA_SETTLEMENT", "fixing and discounted settlement")
            elif t.product in ("CAP", "FLOOR"):
                for (s, e, pd_) in self.periods(t.start, t.maturity, T["months"]):
                    if "PAY:" + pd_.isoformat() not in paid:
                        add(t, pd_, "OPTION_PAYOFF", "caplet/floorlet settlement if in the money")
            elif t.product == "SWAPTION":
                add(t, date.fromisoformat(T["expiry"]), "EXPIRY", f"exercise if forward {'>' if T['payer'] else '<'} {T['strike']:.4%} ({T['settlement'].lower()})")
            elif t.product == "XCCY":
                for (s, e, pd_) in self.periods(t.start, t.maturity, T["months"]):
                    if "PAY:" + pd_.isoformat() not in paid:
                        add(t, pd_, "XCCY_INTEREST", "interest exchange in both currencies")
                add(t, mat, "NOTIONAL_EXCHANGE", f"final exchange: {T['ccy']} {T['ccy_notional']:,.0f} vs USD {T['usd_notional']:,.0f}")
            elif t.product == "TRS":
                for (s, e, pd_) in self.periods(t.start, t.maturity, T["reset_months"]):
                    if "RESET:" + pd_.isoformat() not in paid:
                        add(t, pd_, "TRS_RESET", "total return less financing settles; notional resets")
            elif t.product == "CDS":
                for (s, e, pd_) in self.periods(t.start, t.maturity, 3):
                    if "PREM:" + pd_.isoformat() not in paid:
                        add(t, pd_, "CDS_PREMIUM", f"{T['running_bps']:.0f}bp running premium")
            elif t.product == "COMMODITY_SWAP":
                for (s, e, pd_) in self.periods(t.start, t.maturity, T["months"]):
                    if "SETTLE:" + pd_.isoformat() not in paid:
                        add(t, pd_, "COMMODITY_SETTLEMENT", "period average vs fixed settles")
            if t.product not in ("FRA", "SWAPTION"):
                add(t, mat, "MATURITY", "trade matures")
        out.sort(key=lambda r: (r["date"], r["trade_id"]))
        return out

    def book(self, pf: Portfolio) -> Dict:
        w = self.w
        rows = []
        for t in pf.otc_trades.values():
            rows.append({"id": t.id, "product": t.product, "counterparty": t.counterparty, "dealer_name": DEALERS[t.counterparty].name, "notional": t.notional,
                         "currency": t.currency, "trade_date": t.trade_date, "start": t.start, "maturity": t.maturity, "status": t.status, "mtm": t.mtm,
                         "realized": t.realized, "terms": t.terms, "analytics": t.analytics, "state": {k: v for k, v in t.state.items() if k != "paid"},
                         "cashflows": t.cashflows[-12:], "fixings": t.fixings, "bucket": BUCKET_BY_PRODUCT[t.product], "description": self.describe(t),
                         "unwind_cost": self.unwind_cost(t) if t.is_open else ZERO, "notes": t.notes})
        agg = {"rates_dv01": sum(_f(r["analytics"].get("dv01", 0.0)) for r in rows if r["status"] == "OPEN" and r["product"] in RATES_PRODUCTS),
               "rates_vega": sum(_f(r["analytics"].get("vega", 0.0)) for r in rows if r["status"] == "OPEN"),
               "cs01": sum(_f(r["analytics"].get("cs01", 0.0)) for r in rows if r["status"] == "OPEN"),
               "net_mtm": sum((r["mtm"] for r in rows if r["status"] == "OPEN"), ZERO),
               "assets": sum((r["mtm"] for r in rows if r["status"] == "OPEN" and r["mtm"] > 0), ZERO),
               "liabilities": -sum((r["mtm"] for r in rows if r["status"] == "OPEN" and r["mtm"] < 0), ZERO),
               "open": sum(1 for r in rows if r["status"] == "OPEN")}
        csas = [{"dealer": c.counterparty, "threshold": c.threshold, "mta": c.mta, "im_pct": c.im_pct, "vm_posted": c.vm_posted, "vm_received": c.vm_received,
                 "im_posted": c.im_posted, "im_received": c.im_received, "status": c.status} for c in pf.csas.values()]
        rfqs = [{"id": r.id, "product": r.product, "params": r.params, "quotes": r.quotes, "mid": r.mid, "status": r.status, "date": r.date, "expires": r.expires,
                 "executed_with": r.executed_with, "trade_id": r.trade_id} for r in pf.rfqs.values()]
        return {"trades": rows, "aggregate": agg, "csas": csas, "rfqs": rfqs, "exposure": self.exposure(pf), "rate_vol": self.rate_vol(), "upcoming": self.upcoming(pf),
                "calls": [c for c in pf.collateral_calls.values() if c.source == "OTC"],
                "dealers": {k: {"name": s.name, "products": s.products, "style": s.style, "rating": w.market.dealers.state[k].rating, "cds_bps": w.market.dealers.state[k].cds,
                                "defaulted": w.market.dealers.state[k].defaulted} for k, s in DEALERS.items()}}

    def describe(self, t: OTCTrade) -> str:
        T = t.terms
        if t.product == "IRS":
            return f"{'Pay' if T['pay_fixed'] else 'Receive'} fixed {T['fixed_rate']:.4%} vs {T['index']} on {t.notional:,.0f} to {t.maturity}"
        if t.product == "FRA":
            return f"{'Pay' if T['pay_fixed'] else 'Receive'} {T['rate']:.4%} FRA {T['fra_start']}→{T['fra_end']} on {t.notional:,.0f}"
        if t.product in ("CAP", "FLOOR"):
            return f"{'Long' if T['buyer'] else 'Short'} {t.product.lower()} {T['strike']:.2%} on {t.notional:,.0f} to {t.maturity}"
        if t.product == "SWAPTION":
            return f"{'Long' if T['buyer'] else 'Short'} {'payer' if T['payer'] else 'receiver'} swaption {T['strike']:.4%} exp {T['expiry']} into swap to {T['swap_maturity']} ({T['settlement'].lower()})"
        if t.product == "XCCY":
            return f"{'Borrow' if T['direction'] == 'BORROW_FOREIGN' else 'Lend'} {T['ccy']} {T['ccy_notional']:,.0f} vs USD {T['usd_notional']:,.0f}, basis {T['basis_bps']:+.1f}bp, to {t.maturity}"
        if t.product == "TRS":
            return f"{'Receive' if T['receiver'] else 'Pay'} total return on {T['units']:,.0f} {T['security_id']} vs policy + {T['spread_bps']:.0f}bp, monthly resets, to {t.maturity}"
        if t.product == "CDS":
            return f"{'Buy' if T['buyer'] else 'Sell'} protection on {T['issuer']} ({T['reference']}) {t.notional:,.0f}, {T['running_bps']:.0f} running (dealt {T['dealt_spread_bps']:.0f}bp), to {t.maturity}"
        if t.product == "COMMODITY_SWAP":
            return f"{'Pay' if T['pay_fixed'] else 'Receive'} fixed {T['fixed_price']:.4f} vs {T['code']} monthly average x {T['quantity']:,.0f}, to {t.maturity}"
        return t.product

    # ------------------------------------------------------------------ handlers
    def _h_rfq(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        pf.rfqs[p["rfq_id"]] = RFQ(id=p["rfq_id"], portfolio_id=pf.id, product=p["product"], params=p["params"], quotes=p["quotes"], mid=p["mid"], status="OPEN",
                                   date=ev.sim_date, expires=p["expires"])

    def _h_rfq_status(self, ev: Event, status: str) -> None:
        p = ev.payload
        r = self.w.portfolios[p["portfolio_id"]].rfqs[p["rfq_id"]]
        r.status = status
        if status == "EXECUTED":
            r.executed_with, r.trade_id = p["dealer"], p["trade_id"]

    def _h_opened(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        t = OTCTrade(id=p["trade_id"], portfolio_id=pf.id, product=p["product"], counterparty=p["counterparty"], notional=D(p["notional"]), currency=p["currency"],
                     trade_date=ev.sim_date, start=p["start"], maturity=p["maturity"], terms=_decimals(p["terms"]))
        t.notes.append({"date": ev.sim_date, "note": f"opened with {DEALERS[t.counterparty].name} at {p.get('dealer_level')} (mid {p.get('mid')}, cost vs mid {_f(p.get('cost_vs_mid', 0)):,.0f})"})
        pf.otc_trades[t.id] = t
        self.csa(pf, t.counterparty)
        for cf in p.get("initial_cashflows", []):
            legs = cf.get("legs") or [{"currency": cf["currency"], "amount": money(cf["amount"])}]
            self._cashflow(pf, t, cf["kind"], ZERO, "", cf.get("note", ""), ev, legs=[{"currency": l["currency"], "amount": money(l["amount"])} for l in legs], derived=True)
        self.mark(pf, t, ev, derived=True)

    def _h_marked(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        t = pf.otc_trades[p["trade_id"]]
        new = D(p["mtm"])
        t.analytics = p.get("analytics", {})
        if p.get("state") is not None:
            t.state = p["state"]
        led = w.ledgers[pf.id]
        a = led.security_balance(t.id, "1800")
        l = led.security_balance(t.id, "2800")
        ta, tl = max(ZERO, new), max(ZERO, -new)
        da, dl = ta - a, tl - l
        delta = new - (a - l)
        t.mtm = new
        if da == 0 and dl == 0:
            return
        lines = [dr("1800", da, t.id, "derivative asset (PV)") if da else None, cr("2800", dl, t.id, "derivative liability (PV)") if dl else None,
                 cr("4900", delta, t.id, "OTC mark-to-market")]
        w.post(pf.id, f"Mark {t.id} {t.product} @ {new:,.2f} (change {delta:+,.2f})", [x for x in lines if x], ev, {"trade_id": t.id, "kind": "OTC_MTM"})

    def _h_fixing(self, ev: Event) -> None:
        p = ev.payload
        t = self.w.portfolios[p["portfolio_id"]].otc_trades[p["trade_id"]]
        t.fixings[p["key"]] = _f(p["value"])

    def _h_cashflow(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        t = pf.otc_trades[p["trade_id"]]
        lines = []
        net_base = ZERO
        for leg in p["legs"]:
            ccy, amt = leg["currency"], D(leg["amount"])
            if amt == 0:
                continue
            ca = pf.cash_account(ccy)
            base = amt if ccy == pf.base_currency else w.fx.to_base(ccy, amt)
            ca.balance += amt
            ca.base_value += base
            net_base += base
            w.record_cash_movement(pf, ccy, amt, "OTC", f"{t.id} {t.product} {p['kind']}: {p.get('note', '')}", ev)
            lines.append(dr(f"1010:{ccy}", base, t.id, f"{p['kind'].lower()} {ccy} {amt:+,.2f}"))
        if net_base != 0:
            lines.append(cr("4910", net_base, t.id, f"{p['kind'].lower()} realised"))
        t.realized += net_base
        t.cashflows.append({"date": ev.sim_date, "kind": p["kind"], "legs": [{"currency": l["currency"], "amount": D(l["amount"])} for l in p["legs"]], "base": net_base,
                            "note": p.get("note", ""), "event_id": ev.id})
        if lines:
            w.post(pf.id, f"{t.id} {t.product} {p['kind'].lower()}: {p.get('note', '')}", lines, ev, {"trade_id": t.id, "kind": "OTC_CASHFLOW"})

    def _h_terminated(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        t = pf.otc_trades[p["trade_id"]]
        t.status = p.get("status", "TERMINATED")
        t.closed_date = ev.sim_date
        t.notes.append({"date": ev.sim_date, "note": p.get("reason", t.status)})
        # the PV leaves the balance sheet; the settlement cash (if any) is a separate OTC_CASHFLOW
        led = w.ledgers[pf.id]
        a = led.security_balance(t.id, "1800")
        l = led.security_balance(t.id, "2800")
        t.mtm = ZERO
        if a or l:
            lines = [cr("1800", a, t.id, "PV reversed") if a else None, dr("2800", l, t.id, "PV reversed") if l else None, dr("4900", a - l, t.id, "MTM reversed on close")]
            w.post(pf.id, f"{t.id} {t.status.lower()}: derivative value removed", [x for x in lines if x], ev, {"trade_id": t.id, "kind": "OTC_CLOSE"})

    def _h_csa_moved(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        csa = self.csa(pf, p["dealer"])
        amt = D(p["amount"])
        ccy = p["currency"]
        kind = p["kind"]
        csa.history.append({"date": ev.sim_date, "kind": kind, "amount": amt, "net_mtm": D(p["net_mtm"]), "event_id": ev.id})
        if kind == "VM_RECEIVED":
            ca = pf.cash_account(ccy)
            ca.balance += amt
            ca.base_value += amt
            csa.vm_received += amt
            w.record_cash_movement(pf, ccy, amt, "COLLATERAL", f"Variation margin {'received from' if amt > 0 else 'returned to'} {DEALERS[p['dealer']].name} (CSA)", ev)
            lines = [dr(f"1010:{ccy}", amt, None, "VM cash received"), cr("2450", amt, None, f"cash collateral received CSA:{p['dealer']}")]
            w.post(pf.id, f"CSA {p['dealer']}: variation margin received {amt:+,.2f} (net MTM {D(p['net_mtm']):,.2f})", lines, ev, {"reference": f"CSA:{p['dealer']}", "kind": "COLLATERAL"})
        elif kind == "VM_POSTED":
            csa.vm_posted += amt
            w.collateral.move_cash(pf, amt, f"CSA:{p['dealer']}", "OTC", ev, note="variation margin")
        elif kind == "IM_POSTED":
            csa.im_posted += amt
            w.collateral.move_cash(pf, amt, f"IM:{p['dealer']}", "OTC", ev, note="initial margin (segregated)")
        elif kind == "IM_RECEIVED":
            csa.im_received += amt      # segregated at a third-party custodian: memo only

    def _h_credit_event(self, ev: Event) -> None:
        w = self.w
        if w.replaying:
            return
        p = ev.payload
        ref = p["reference"]
        rec = _f(p["recovery"])
        for pf in w.portfolios.values():
            for t in list(pf.otc_trades.values()):
                if t.is_open and t.product == "CDS" and t.terms["reference"] == ref:
                    N = _f(t.notional)
                    prot = N * (1 - rec)
                    accrued = _f(t.state.get("accrued", 0))
                    net = (prot - accrued) if t.terms["buyer"] else (accrued - prot)
                    self._cashflow(pf, t, "PROTECTION", money(D(repr(net))), t.currency,
                                   f"credit event on {t.terms['issuer']}: protection {prot:,.2f} at {rec:.0%} recovery less accrued premium {accrued:,.2f}", ev)
                    self._status(pf, t, "SETTLED_DEFAULT", ev, "credit event: protection settled")

    def _h_cpty_defaulted(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        dealer = p["dealer"]
        w.market.dealers.mark_defaulted(dealer, date.fromisoformat(ev.sim_date))
        if w.replaying:
            return
        rec = D(repr(_f(p["recovery"])))
        for pf in w.portfolios.values():
            trades = [t for t in pf.otc_trades.values() if t.is_open and t.counterparty == dealer]
            csa = pf.csas.get(dealer)
            if not trades and csa is None:
                continue
            net = sum((t.mtm for t in trades), ZERO)
            for t in trades:
                self._status(pf, t, "TERMINATED", ev, f"counterparty default of {DEALERS[dealer].name}: early termination at mid")
            if csa is None:
                continue
            vm_recv, vm_post, im_post = csa.vm_received, csa.vm_posted, csa.im_posted
            # settle: what we are owed net of collateral held is an unsecured claim (recovers at `rec`); what we owe net of collateral posted is paid in full
            claim = net - vm_recv + vm_post          # positive: they owe us (unsecured part)
            if im_post:
                w.emit(OE.CSA_MARGIN_MOVED, {"portfolio_id": pf.id, "dealer": dealer, "kind": "IM_POSTED", "amount": -im_post, "net_mtm": net, "threshold": csa.threshold,
                                             "mta": csa.mta, "currency": pf.base_currency}, cause_id=ev.id, portfolio_id=pf.id)
            if csa.im_received:
                w.emit(OE.CSA_MARGIN_MOVED, {"portfolio_id": pf.id, "dealer": dealer, "kind": "IM_RECEIVED", "amount": -csa.im_received, "net_mtm": net, "threshold": csa.threshold,
                                             "mta": csa.mta, "currency": pf.base_currency}, cause_id=ev.id, portfolio_id=pf.id)
            anchor = trades[0] if trades else None
            if anchor is not None:
                if claim > 0:
                    recovered = money(claim * rec)
                    self._cashflow(pf, anchor, "DEFAULT_SETTLEMENT", recovered, pf.base_currency,
                                   f"unsecured claim {claim:,.2f} recovers {rec:.0%}; VM held {vm_recv:,.2f} applied", ev)
                else:
                    self._cashflow(pf, anchor, "DEFAULT_SETTLEMENT", claim, pf.base_currency, f"net owed {-claim:,.2f} paid to the estate after applying VM posted {vm_post:,.2f}", ev)
            # collateral balances are consumed by the settlement
            if vm_recv:
                csa.vm_received = ZERO
                w.post(pf.id, f"CSA {dealer}: VM received {vm_recv:,.2f} applied against the claim", [dr("2450", vm_recv, None, "VM received applied"), cr("4910", vm_recv, None, "applied on default")], ev,
                       {"reference": f"CSA:{dealer}", "kind": "COLLATERAL"})
            if vm_post:
                csa.vm_posted = ZERO
                pf.cash_collateral.pop(f"CSA:{dealer}", None)
                w.post(pf.id, f"CSA {dealer}: VM posted {vm_post:,.2f} applied against what we owe", [dr("4910", vm_post, None, "VM posted consumed"), cr("1400", vm_post, None, "VM posted applied")], ev,
                       {"reference": f"CSA:{dealer}", "kind": "COLLATERAL"})
            csa.status = "TERMINATED"
            call = w.collateral.open_call(pf, "OTC", dealer)
            if call:
                w.collateral.resolve_call(pf, call, "CANCELLED", "counterparty defaulted", ev)


def _decimals(terms: Dict) -> Dict:
    out = {}
    for k, v in terms.items():
        if k in ("units", "usd_notional", "ccy_notional", "quantity") and isinstance(v, str):
            out[k] = D(v)
        else:
            out[k] = v
    return out
