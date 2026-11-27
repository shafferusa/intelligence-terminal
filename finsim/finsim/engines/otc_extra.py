"""OTC products beyond the core set: volatility, dividend and inflation swaps, FX swaps, exotic options, crypto
perpetuals and structured notes. The core engine (otc.py) routes these products here for fair value, parameter
checks, terms, valuation, the daily lifecycle and descriptions; quoting, dealers, CSA margining, netting, default
and termination are the core engine's and apply unchanged.

Pricing, in one line each (all documented simplifications):
  VARIANCE_SWAP     fair strike = ATM implied vol (surface) × 1.03; PV = N_var × (expected variance − K²) × DF, N_var = vega notional / 2K
  VOL_SWAP          fair strike = ATM implied × 0.98 (convexity discount); PV = N_vol × (expected vol − K) × DF
  DIVIDEND_SWAP     fixed vs the dividends per unit actually paid over the period; fair = the schedule the simulator will pay
  INFLATION_SWAP    zero-coupon: N × [CPI_T/CPI_0 − (1+K)^T]; CPI is the simulator's own index (real CPI in a career save)
  FX_SWAP           a spot exchange now and the reverse exchange at maturity at the forward (covered interest parity)
  BARRIER_OPTION    Reiner–Rubinstein closed forms (continuous monitoring, observed at each close); knock-in = vanilla − knock-out
  DIGITAL_OPTION    cash-or-nothing: payout × e^{−rT} × N(±d2)
  ASIAN_OPTION      arithmetic average, Turnbull–Wakeman moment matching (σ/√3 over the averaging life), seasoned strike adjustment
  CRYPTO_PERP       units × (spot − entry); daily funding paid by longs when the perp trades above the index
  PPN               zero-coupon bond + participation × call on the index return
  REVERSE_CONVERTIBLE   bond + coupon − down-and-in put struck at par with the barrier
  AUTOCALLABLE      Monte Carlo (fixed seed, monthly steps) with annual autocall observations and a maturity barrier
"""
from __future__ import annotations

import math
import random
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from ..money import D, money, ZERO
from . import otc_pricing as px
from .options_pricing import bsm, norm_cdf

EXTRA_PRODUCTS = ("VARIANCE_SWAP", "VOL_SWAP", "DIVIDEND_SWAP", "INFLATION_SWAP", "FX_SWAP", "BARRIER_OPTION", "DIGITAL_OPTION", "ASIAN_OPTION",
                  "CRYPTO_PERP", "PPN", "REVERSE_CONVERTIBLE", "AUTOCALLABLE")
# quote family: premium (bid/ask around the PV), price (around a price level), level (mid ± width in the unit)
FAMILY = {"VARIANCE_SWAP": "level", "VOL_SWAP": "level", "DIVIDEND_SWAP": "price", "INFLATION_SWAP": "level", "FX_SWAP": "price",
          "BARRIER_OPTION": "premium", "DIGITAL_OPTION": "premium", "ASIAN_OPTION": "premium", "CRYPTO_PERP": "price",
          "PPN": "price", "REVERSE_CONVERTIBLE": "price", "AUTOCALLABLE": "price"}
NDF_CURRENCIES = ("MXN", "BRL", "CNH", "KRW", "INR", "PLN", "TWD", "IDR")
PERP_FUNDING_DAILY = 0.0003          # 0.01% per 8 hours, the exchanges' norm when the perp trades above the index


def _f(x) -> float:
    return float(x) if x is not None else 0.0


# ---------------------------------------------------------------- closed forms
def barrier_in(call: bool, S: float, K: float, H: float, T: float, r: float, q: float, sigma: float) -> float:
    """Knock-in barrier option (Reiner–Rubinstein, no rebate). Down barriers for H < S, up barriers for H > S."""
    if T <= 0 or sigma <= 0:
        return 0.0
    b = r - q
    v = sigma * math.sqrt(T)
    mu = (b - 0.5 * sigma * sigma) / (sigma * sigma)
    phi = 1.0 if call else -1.0
    eta = 1.0 if H < S else -1.0                # down (η = 1) or up (η = −1)
    x1 = math.log(S / K) / v + (1 + mu) * v
    x2 = math.log(S / H) / v + (1 + mu) * v
    y1 = math.log(H * H / (S * K)) / v + (1 + mu) * v
    y2 = math.log(H / S) / v + (1 + mu) * v
    e1, e2 = math.exp((b - r) * T), math.exp(-r * T)
    hs = H / S
    A = phi * S * e1 * norm_cdf(phi * x1) - phi * K * e2 * norm_cdf(phi * x1 - phi * v)
    B = phi * S * e1 * norm_cdf(phi * x2) - phi * K * e2 * norm_cdf(phi * x2 - phi * v)
    C = phi * S * e1 * hs ** (2 * (mu + 1)) * norm_cdf(eta * y1) - phi * K * e2 * hs ** (2 * mu) * norm_cdf(eta * y1 - eta * v)
    Dd = phi * S * e1 * hs ** (2 * (mu + 1)) * norm_cdf(eta * y2) - phi * K * e2 * hs ** (2 * mu) * norm_cdf(eta * y2 - eta * v)
    down = eta > 0
    if call and down:
        val = C if K > H else A - B + Dd
    elif call and not down:
        val = A if K > H else B - C + Dd
    elif (not call) and down:
        val = B - C + Dd if K > H else A
    else:
        val = A - B + Dd if K > H else C
    return max(0.0, val)


def barrier_price(call: bool, knock_in: bool, S: float, K: float, H: float, T: float, r: float, q: float, sigma: float) -> float:
    vanilla = bsm("C" if call else "P", S, K, T, r, q, sigma)
    kin = min(vanilla, barrier_in(call, S, K, H, T, r, q, sigma))
    return kin if knock_in else max(0.0, vanilla - kin)


def digital_price(call: bool, S: float, K: float, T: float, r: float, q: float, sigma: float, payout: float) -> float:
    if T <= 0:
        return payout if ((S > K) if call else (S < K)) else 0.0
    d2 = (math.log(S / K) + (r - q - 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return payout * math.exp(-r * T) * (norm_cdf(d2) if call else norm_cdf(-d2))


def asian_price(call: bool, F_avg: float, K: float, T_total: float, T_remaining: float, r: float, sigma: float, realized_avg: float, n_done: int, n_total: int) -> float:
    """Arithmetic-average option (Turnbull–Wakeman moment matching). A seasoned trade folds the realised average into the strike."""
    if n_total <= 0:
        n_total = 1
    frac_left = max(0.0, min(1.0, T_remaining / T_total)) if T_total > 0 else 0.0
    if n_done > 0 and n_done < n_total:
        K_eff = (K * n_total - realized_avg * n_done) / (n_total - n_done)
        scale = (n_total - n_done) / n_total
    elif n_done >= n_total:
        payoff = max(0.0, realized_avg - K) if call else max(0.0, K - realized_avg)
        return payoff
    else:
        K_eff, scale = K, 1.0
    if K_eff <= 0:                       # the realised average already exceeds the strike: the option is a forward on the rest
        return scale * max(0.0, F_avg - K_eff) * math.exp(-r * T_remaining) if call else 0.0
    sig_a = sigma / math.sqrt(3.0)
    T_ = max(T_remaining, 1 / 365)
    disc = math.exp(-r * T_)
    return scale * px.black76(call, F_avg, K_eff, T_, sig_a, disc)


# ---------------------------------------------------------------- the products
class ExtraProducts:
    def __init__(self, engine):
        self.e = engine

    @property
    def w(self):
        return self.e.w

    # ------------------------------------------------------------- helpers
    def _underlying(self, sid: str, T: float) -> Tuple[float, float, float, float]:
        """(spot, r, q, implied vol) for an equity-style underlying or an index."""
        S, r, q = self.e._eq_inputs(sid, T)
        sigma = self.e._eq_vol(sid, S, S, r, q, max(T, 0.05))
        return S, r, q, sigma

    def _sec_ok(self, sid: str) -> bool:
        from .vol import INDICES
        w = self.w
        if sid in INDICES:
            return True
        sec = w.securities.get(sid)
        return bool(sec and not sec.is_bond and not sec.is_future and not sec.is_option and sec.asset_class != "INDEX")

    def _maturity_months(self, months: int) -> str:
        w = self.w
        return w.calendar.roll(px._add_months(w.current_date, int(months))).isoformat()

    def _cpi(self) -> float:
        """The simulator's price index: 100 at the world's first session, compounding the macro model's inflation daily."""
        return float(getattr(self.w.market.macro, "cpi_index", 100.0) or 100.0)

    # ------------------------------------------------------------- parameters
    def norm(self, p: str, q: Dict) -> Dict:
        from ..world import CommandError
        from ..engines.fx_market import CURRENCIES
        w = self.w
        if p in ("VARIANCE_SWAP", "VOL_SWAP"):
            sid = str(q.get("security_id", "SPX")).upper()
            if not self._sec_ok(sid):
                raise CommandError("reference must be an equity, ETF or an index (SPX, NDX, RUT)")
            q["security_id"] = sid
            if _f(q.get("vega_notional", 0)) <= 0:
                raise CommandError("vega_notional (dollars per vol point) must be positive")
            q["tenor_months"] = max(1, int(_f(q.get("tenor_months", 3))))
            q.setdefault("buyer", True)
        elif p == "DIVIDEND_SWAP":
            sid = str(q.get("security_id", "SPY")).upper()
            sec = w.securities.get(sid)
            if sec is None or sec.asset_class not in ("EQUITY", "ETF", "ADR", "REIT", "PREFERRED"):
                raise CommandError("reference must be a dividend-paying stock or ETF (SPY, JPM, XOM ...)")
            q["security_id"] = sid
            if _f(q.get("units", 0)) <= 0:
                raise CommandError("units must be positive")
            q["tenor_months"] = max(3, int(_f(q.get("tenor_months", 12))))
            q.setdefault("buyer", True)
        elif p == "INFLATION_SWAP":
            if _f(q.get("notional", 0)) <= 0:
                raise CommandError("notional must be positive")
            q["tenor_years"] = max(1, int(_f(q.get("tenor_years", 5))))
            q.setdefault("pay_fixed", True)
        elif p == "FX_SWAP":
            ccy = str(q.get("ccy", "EUR")).upper()
            if ccy not in CURRENCIES or ccy == "USD":
                raise CommandError("ccy must be a non-USD currency")
            q["ccy"] = ccy
            if _f(q.get("amount", 0)) <= 0:
                raise CommandError("amount (foreign currency) must be positive")
            q["tenor_months"] = max(1, int(_f(q.get("tenor_months", 3))))
            q["direction"] = str(q.get("direction", "BUY_SELL")).upper()
            if q["direction"] not in ("BUY_SELL", "SELL_BUY"):
                raise CommandError("direction must be BUY_SELL (buy the currency now, sell it forward) or SELL_BUY")
        elif p in ("BARRIER_OPTION", "DIGITAL_OPTION"):
            ccy = str(q.get("ccy", "")).upper()
            sid = str(q.get("security_id", "")).upper()
            if ccy:
                if ccy not in CURRENCIES or ccy == "USD":
                    raise CommandError("ccy must be a non-USD currency")
                q["ccy"] = ccy
                q.pop("security_id", None)
            else:
                if not self._sec_ok(sid):
                    raise CommandError("reference must be an equity, ETF, index (SPX, NDX, RUT) or a currency (ccy)")
                q["security_id"] = sid
                q.pop("ccy", None)
            q["option_type"] = str(q.get("option_type", "C")).upper()[:1]
            if q["option_type"] not in ("C", "P"):
                raise CommandError("option_type must be C or P")
            if _f(q.get("units", 0)) <= 0:
                raise CommandError("units must be positive")
            q["tenor_months"] = max(1, int(_f(q.get("tenor_months", 3))))
            q.setdefault("buyer", True)
            if p == "BARRIER_OPTION":
                q["barrier_type"] = str(q.get("barrier_type", "KO")).upper()
                if q["barrier_type"] not in ("KO", "KI"):
                    raise CommandError("barrier_type must be KO (knock-out) or KI (knock-in)")
                if _f(q.get("barrier_pct", 0)) <= 0:
                    raise CommandError("barrier_pct (barrier as % of spot, e.g. 80 or 120) is required")
            else:
                if _f(q.get("payout", 0)) <= 0:
                    raise CommandError("payout (dollars per unit if it finishes in the money) must be positive")
        elif p == "ASIAN_OPTION":
            code = str(q.get("code", "CL")).upper()
            if code not in w.market.commodities.state:
                raise CommandError("unknown commodity code")
            q["code"] = code
            q["option_type"] = str(q.get("option_type", "C")).upper()[:1]
            if q["option_type"] not in ("C", "P"):
                raise CommandError("option_type must be C or P")
            if _f(q.get("units", 0)) <= 0:
                raise CommandError("units must be positive")
            q["tenor_months"] = max(1, int(_f(q.get("tenor_months", 3))))
            q.setdefault("buyer", True)
        elif p == "CRYPTO_PERP":
            coin = str(q.get("coin", "BTC")).upper()
            sec = w.securities.get(coin)
            if sec is None or sec.asset_class != "CRYPTO":
                raise CommandError("coin must be a listed crypto asset (BTC, ETH, SOL ...)")
            q["coin"] = coin
            if _f(q.get("units", 0)) <= 0:
                raise CommandError("units (coins) must be positive")
            q.setdefault("long", True)
        elif p in ("PPN", "REVERSE_CONVERTIBLE", "AUTOCALLABLE"):
            sid = str(q.get("security_id", "SPX")).upper()
            if not self._sec_ok(sid):
                raise CommandError("the note's reference must be an equity, ETF or index (SPX, NDX, RUT)")
            q["security_id"] = sid
            if _f(q.get("notional", 0)) <= 0:
                raise CommandError("notional must be positive")
            q["tenor_years"] = max(1, int(_f(q.get("tenor_years", {"PPN": 5, "REVERSE_CONVERTIBLE": 1, "AUTOCALLABLE": 3}[p]))))
            if p == "PPN":
                q["participation"] = max(0.1, _f(q.get("participation", 1.0)))
            if p == "REVERSE_CONVERTIBLE":
                q["barrier_pct"] = max(10.0, min(100.0, _f(q.get("barrier_pct", 70.0))))
            if p == "AUTOCALLABLE":
                q["autocall_pct"] = max(50.0, _f(q.get("autocall_pct", 100.0)))
                q["barrier_pct"] = max(10.0, min(100.0, _f(q.get("barrier_pct", 60.0))))
        return q

    # ------------------------------------------------------------- fair value
    def fair(self, p: str, P: Dict) -> Dict:
        from ..engines.fx_market import CURRENCIES  # noqa: F401
        w = self.w
        asof = w.current_date
        c = self.e.curve()
        if p in ("VARIANCE_SWAP", "VOL_SWAP"):
            mat = self._maturity_months(P["tenor_months"])
            T = px.years(asof, date.fromisoformat(mat))
            S, r, q, sigma = self._underlying(P["security_id"], T)
            iv = sigma * 100.0
            k = iv * (1.03 if p == "VARIANCE_SWAP" else 0.98)
            return {"unit": "vol points", "mid": k, "pv": 0.0, "maturity": mat, "implied_vol": iv, "spot": S, "sessions": max(1, w.calendar.business_days_between(asof, date.fromisoformat(mat)))}
        if p == "DIVIDEND_SWAP":
            mat = self._maturity_months(P["tenor_months"])
            sec = w.securities[P["security_id"]]
            expected = self._expected_dividends(sec, asof, date.fromisoformat(mat))
            price = _f(w.market.last_bar(sec.id).close)
            return {"unit": "dividends per unit", "mid": expected, "pv": 0.0, "maturity": mat, "spot": price, "notional": price * _f(P["units"])}
        if p == "INFLATION_SWAP":
            mat = self._maturity_months(12 * int(P["tenor_years"]))
            k = self._breakeven()
            return {"unit": "bp of the fixed rate", "mid": k * 1e4, "pv": 0.0, "maturity": mat, "cpi": self._cpi(), "current_inflation": _f(w.market.macro.state.inflation)}
        if p == "FX_SWAP":
            mat = self._maturity_months(P["tenor_months"])
            ccy = P["ccy"]
            spot = w.market.fx.spot[ccy]
            fwd = w.market.fx.forward(ccy, "USD", asof, date.fromisoformat(mat))
            return {"unit": "USD per unit (forward)", "mid": fwd, "pv": 0.0, "maturity": mat, "spot": spot, "points": (fwd - spot) * (1e2 if ccy == "JPY" else 1e4),
                    "notional": spot * _f(P["amount"])}
        if p in ("BARRIER_OPTION", "DIGITAL_OPTION"):
            mat = self._maturity_months(P["tenor_months"])
            T = px.years(asof, date.fromisoformat(mat))
            call = P["option_type"] == "C"
            if P.get("ccy"):
                S = w.market.fx.spot[P["ccy"]]
                r = -math.log(max(1e-9, px.df(c, T))) / max(T, 1 / 365)
                q = w.market.fx.rate[P["ccy"]]
                sigma = self.e.fx_vol(P["ccy"])
            else:
                S, r, q, sigma = self._underlying(P["security_id"], T)
            K = _f(P["strike"]) if P.get("strike") not in (None, "") else S * math.exp((r - q) * T)
            units = _f(P["units"])
            if p == "BARRIER_OPTION":
                H = S * _f(P["barrier_pct"]) / 100.0
                if abs(H - S) / S < 0.005:
                    H = S * (0.995 if H <= S else 1.005)
                prem = barrier_price(call, P["barrier_type"] == "KI", S, K, H, T, r, q, sigma)
                out = {"barrier": H}
            else:
                prem = digital_price(call, S, K, T, r, q, sigma, _f(P["payout"]))
                out = {"payout": _f(P["payout"])}
            return {"unit": "premium (USD)", "mid": prem * units, "pv": prem * units, "maturity": mat, "spot": S, "strike": K, "vol": sigma, "premium_per_unit": prem,
                    "notional": units * (S if p == "BARRIER_OPTION" else _f(P["payout"])), **out}
        if p == "ASIAN_OPTION":
            mat = self._maturity_months(P["tenor_months"])
            T = px.years(asof, date.fromisoformat(mat))
            code = P["code"]
            curve = w.market.curve_for(code)
            spot = w.market.spot(code)
            vals = list(curve.values())[:max(1, P["tenor_months"])] or [spot]
            F_avg = sum(vals) / len(vals)
            K = _f(P["strike"]) if P.get("strike") not in (None, "") else F_avg
            r = -math.log(max(1e-9, px.df(c, T))) / max(T, 1 / 365)
            sigma = w.securities[w.market.front_contract(code).id].sigma_annual if w.market.front_contract(code) else 0.3
            n_total = max(1, w.calendar.business_days_between(asof, date.fromisoformat(mat)))
            prem = asian_price(P["option_type"] == "C", F_avg, K, T, T, r, sigma, 0.0, 0, n_total)
            units = _f(P["units"])
            return {"unit": "premium (USD)", "mid": prem * units, "pv": prem * units, "maturity": mat, "spot": spot, "strike": K, "vol": sigma, "forward_average": F_avg,
                    "premium_per_unit": prem, "notional": units * spot, "sessions": n_total}
        if p == "CRYPTO_PERP":
            mat = self._maturity_months(120)
            spot = _f(w.market.last_bar(P["coin"]).close)
            return {"unit": "USD per coin (entry)", "mid": spot, "pv": 0.0, "maturity": mat, "spot": spot, "notional": spot * _f(P["units"]),
                    "funding_daily": PERP_FUNDING_DAILY}
        if p in ("PPN", "REVERSE_CONVERTIBLE", "AUTOCALLABLE"):
            mat = self._maturity_months(12 * int(P["tenor_years"]))
            T = px.years(asof, date.fromisoformat(mat))
            S, r, q, sigma = self._underlying(P["security_id"], T)
            price, extra = self._note_price(p, P, S, r, q, sigma, T, S, 0.0, asof)
            return {"unit": "% of par", "mid": price * 100.0, "pv": price * _f(P["notional"]), "maturity": mat, "spot": S, "vol": sigma, "notional": _f(P["notional"]), **extra}
        raise ValueError(f"unknown product {p}")

    def _expected_dividends(self, sec, start: date, end: date) -> float:
        m = self.w.market
        n = 0
        for y in range(start.year, end.year + 1):
            n += sum(1 for ex in m.dividend_ex_dates(sec, y) if start < ex <= end)
        return n * _f(sec.dividend_per_share)

    def _breakeven(self) -> float:
        infl = _f(self.w.market.macro.state.inflation) / 100.0
        return 0.5 * infl + 0.5 * 0.025

    def _note_price(self, p: str, P: Dict, S: float, r: float, q: float, sigma: float, T: float, S0: float, coupon_accrued: float, asof: date,
                    knocked: bool = False) -> Tuple[float, Dict]:
        """Price of the note as a fraction of par, given today's spot and the initial level S0."""
        if T <= 0:
            T = 1 / 365
        disc = math.exp(-r * T)
        x = S / S0
        if p == "PPN":
            part = _f(P["participation"])
            call = bsm("C", x, 1.0, T, r, q, sigma)
            return disc + part * call, {"participation": part, "zero_bond": disc, "option": part * call}
        if p == "REVERSE_CONVERTIBLE":
            cpn = _f(P.get("coupon", 0.10))
            H = _f(P["barrier_pct"]) / 100.0
            put = bsm("P", x, 1.0, T, r, q, sigma) if knocked else barrier_price(False, True, x, 1.0, H, T, r, q, sigma)
            years_total = _f(P.get("years_total", P.get("tenor_years", 1)))
            return disc * (1 + cpn * years_total) - put, {"coupon": cpn, "barrier": H, "put": put, "bond": disc * (1 + cpn * years_total)}
        # autocallable: Monte Carlo, fixed seed, monthly steps, annual observations
        cpn = _f(P.get("coupon", 0.08))
        auto = _f(P["autocall_pct"]) / 100.0
        H = _f(P["barrier_pct"]) / 100.0
        obs = [float(k) for k in P.get("observations", [])] or [float(y) for y in range(1, int(P.get("tenor_years", 3)) + 1)]
        elapsed = _f(P.get("elapsed_years", 0.0))
        remaining_obs = [o - elapsed for o in obs if o - elapsed > 1e-6]
        if not remaining_obs:
            remaining_obs = [T]
        rng = random.Random(f"{self.w.seed}|autocall|{P.get('security_id')}|{asof.isoformat()}|{round(S, 2)}")
        n_paths, steps_per_year = 1500, 12
        total = 0.0
        for _ in range(n_paths):
            x_ = x
            t = 0.0
            paid = None
            for o in remaining_obs:
                dt_ = (o - t) / max(1, int(round((o - t) * steps_per_year)))
                nsteps = max(1, int(round((o - t) * steps_per_year)))
                for _s in range(nsteps):
                    x_ *= math.exp((r - q - 0.5 * sigma * sigma) * dt_ + sigma * math.sqrt(dt_) * rng.gauss(0, 1))
                t = o
                if x_ >= auto:
                    paid = (1.0 + cpn * (o + elapsed)) * math.exp(-r * o)
                    break
            if paid is None:
                last = remaining_obs[-1]
                paid = (1.0 + cpn * (last + elapsed)) * math.exp(-r * last) if x_ >= H else x_ * math.exp(-r * last)
            total += paid
        return total / n_paths, {"coupon": cpn, "autocall_level": auto, "barrier": H, "observations": obs, "paths": n_paths}

    # ------------------------------------------------------------- quoting
    def player_level(self, p: str, P: Dict, fair: Dict, bid: float, ask: float) -> Tuple[float, float]:
        if p in ("VARIANCE_SWAP", "VOL_SWAP"):
            level = ask if P["buyer"] else bid
            nv = _f(P["vega_notional"])
            return level, abs(level - fair["mid"]) * nv
        if p == "DIVIDEND_SWAP":
            level = ask if P["buyer"] else bid
            return level, abs(level - fair["mid"]) * _f(P["units"])
        if p == "INFLATION_SWAP":
            level = ask if P["pay_fixed"] else bid
            return level, abs(level - fair["mid"]) * 1e-4 * _f(P["notional"]) * _f(P["tenor_years"])
        if p == "FX_SWAP":
            level = bid if P["direction"] == "BUY_SELL" else ask            # we sell the currency forward: the bid
            return level, abs(level - fair["mid"]) * _f(P["amount"])
        if p in ("BARRIER_OPTION", "DIGITAL_OPTION", "ASIAN_OPTION"):
            level = ask if P["buyer"] else bid
            return level, abs(level - fair["pv"])
        if p == "CRYPTO_PERP":
            level = ask if P["long"] else bid
            return level, abs(level - fair["mid"]) * _f(P["units"])
        if p in ("PPN", "REVERSE_CONVERTIBLE", "AUTOCALLABLE"):
            return ask, abs(ask - fair["mid"]) / 100.0 * _f(P["notional"])
        return fair["mid"], 0.0

    def build_terms(self, r, q: Dict):
        w = self.w
        p, P, M = r.product, r.params, r.mid
        today = w.current_date.isoformat()
        level = q["level"]
        base = w.base_currency
        initial: List[Dict] = []
        if p in ("VARIANCE_SWAP", "VOL_SWAP"):
            nv = money(P["vega_notional"])
            terms = {"security_id": P["security_id"], "vega_notional": nv, "strike": level, "buyer": bool(P["buyer"]), "implied_at_trade": M["implied_vol"], "sessions": M["sessions"]}
            return terms, money(D(repr(level)) * nv), base, today, M["maturity"], initial
        if p == "DIVIDEND_SWAP":
            units = money(P["units"])
            terms = {"security_id": P["security_id"], "units": units, "fixed_dividends": level, "buyer": bool(P["buyer"]), "initial_price": M["spot"]}
            return terms, money(D(repr(M["notional"])), ), base, today, M["maturity"], initial
        if p == "INFLATION_SWAP":
            N = money(P["notional"])
            terms = {"pay_fixed": bool(P["pay_fixed"]), "fixed_rate": level / 1e4, "cpi_start": M["cpi"], "years": int(P["tenor_years"])}
            return terms, N, base, today, M["maturity"], initial
        if p == "FX_SWAP":
            amt = money(P["amount"])
            ccy = P["ccy"]
            spot = M["spot"]
            usd_near = money(amt * D(repr(spot)))
            usd_far = money(amt * D(repr(level)))
            buy_sell = P["direction"] == "BUY_SELL"
            terms = {"ccy": ccy, "amount": amt, "direction": P["direction"], "spot_rate": spot, "forward_rate": level, "usd_near": usd_near, "usd_far": usd_far}
            initial.append({"kind": "NOTIONAL_EXCHANGE", "legs": [{"currency": base, "amount": -usd_near if buy_sell else usd_near}, {"currency": ccy, "amount": amt if buy_sell else -amt}],
                            "note": f"near leg at spot {spot:.5f}: {'buy' if buy_sell else 'sell'} {ccy} {amt:,.0f}"})
            return terms, usd_near, base, today, M["maturity"], initial
        if p in ("BARRIER_OPTION", "DIGITAL_OPTION"):
            units = money(P["units"])
            terms = {"option_type": P["option_type"], "strike": M["strike"], "units": units, "buyer": bool(P["buyer"]), "premium": level, "vol_at_trade": M["vol"], "settlement": "CASH"}
            if P.get("ccy"):
                terms["ccy"] = P["ccy"]
            else:
                terms["security_id"] = P["security_id"]
            if p == "BARRIER_OPTION":
                terms.update({"barrier_type": P["barrier_type"], "barrier": M["barrier"], "spot_at_trade": M["spot"]})
            else:
                terms["payout"] = M["payout"]
            initial.append({"kind": "PREMIUM", "currency": base, "amount": -level if P["buyer"] else level, "note": f"{p.replace('_', ' ').lower()} premium {'paid' if P['buyer'] else 'received'}"})
            return terms, money(D(repr(M["notional"]))), base, today, M["maturity"], initial
        if p == "ASIAN_OPTION":
            units = money(P["units"])
            terms = {"code": P["code"], "option_type": P["option_type"], "strike": M["strike"], "units": units, "buyer": bool(P["buyer"]), "premium": level,
                     "vol_at_trade": M["vol"], "sessions": M["sessions"], "settlement": "CASH"}
            initial.append({"kind": "PREMIUM", "currency": base, "amount": -level if P["buyer"] else level, "note": f"Asian option premium {'paid' if P['buyer'] else 'received'}"})
            return terms, money(D(repr(M["notional"]))), base, today, M["maturity"], initial
        if p == "CRYPTO_PERP":
            units = D(str(P["units"])).quantize(Decimal("0.0001"))
            terms = {"coin": P["coin"], "units": units, "long": bool(P["long"]), "entry_price": level, "funding_daily": PERP_FUNDING_DAILY}
            return terms, money(D(repr(level)) * units), base, today, M["maturity"], initial
        if p in ("PPN", "REVERSE_CONVERTIBLE", "AUTOCALLABLE"):
            N = money(P["notional"])
            terms = {"security_id": P["security_id"], "initial_level": M["spot"], "price_at_trade": level, "years": int(P["tenor_years"]), "vol_at_trade": M["vol"]}
            if p == "PPN":
                terms["participation"] = _f(P["participation"])
            if p == "REVERSE_CONVERTIBLE":
                terms.update({"coupon": _f(P.get("coupon", M.get("coupon", 0.10))), "barrier_pct": _f(P["barrier_pct"])})
            if p == "AUTOCALLABLE":
                terms.update({"coupon": _f(P.get("coupon", M.get("coupon", 0.08))), "autocall_pct": _f(P["autocall_pct"]), "barrier_pct": _f(P["barrier_pct"]),
                              "observations": [w.calendar.roll(px._add_months(w.current_date, 12 * y)).isoformat() for y in range(1, int(P["tenor_years"]) + 1)]})
            paid = money(D(repr(level / 100.0)) * N)
            initial.append({"kind": "PREMIUM", "currency": base, "amount": -paid, "note": f"note bought at {level:.2f}% of par"})
            return terms, N, base, today, M["maturity"], initial
        raise ValueError(p)

    # ------------------------------------------------------------- valuation
    def value(self, pf, t, asof: date, st: Dict) -> Tuple[float, Dict, Dict]:
        w = self.w
        T_ = t.terms
        p = t.product
        c = self.e.curve()
        mat = date.fromisoformat(t.maturity)
        T = px.years(asof, mat)
        disc = px.df(c, max(T, 0.0))
        if p in ("VARIANCE_SWAP", "VOL_SWAP"):
            sid = T_["security_id"]
            S, r, q, sigma = self._underlying(sid, max(T, 0.05))
            n_total = max(1, int(T_["sessions"]))
            n = int(st.get("n", 0))
            sum_sq = _f(st.get("sum_sq", 0.0))
            rv_var = (252.0 / n * sum_sq * 1e4) if n > 0 else 0.0                       # vol points squared
            iv_var = (sigma * 100.0) ** 2
            exp_var = (n * rv_var + max(0, n_total - n) * iv_var) / max(n_total, 1) if n < n_total else rv_var
            K = _f(T_["strike"])
            sign = 1.0 if T_["buyer"] else -1.0
            nv = _f(T_["vega_notional"])
            if p == "VARIANCE_SWAP":
                n_var = nv / (2 * K) if K else 0.0
                pv = sign * n_var * (exp_var - K * K) * disc
                an = {"strike": K, "realized_vol": math.sqrt(rv_var) if n else None, "implied_vol": sigma * 100.0, "expected_vol": math.sqrt(max(0.0, exp_var)), "sessions_done": n,
                      "sessions": n_total, "variance_notional": n_var, "vega": sign * nv * disc * math.sqrt(max(exp_var, 1e-9)) / max(K, 1e-9) * (max(0, n_total - n) / n_total) if n_total else 0.0,
                      "underlying": sid, "days_to_maturity": (mat - asof).days}
            else:
                exp_vol = math.sqrt(max(0.0, exp_var))
                pv = sign * nv * (exp_vol - K) * disc
                an = {"strike": K, "realized_vol": math.sqrt(rv_var) if n else None, "implied_vol": sigma * 100.0, "expected_vol": exp_vol, "sessions_done": n, "sessions": n_total,
                      "vega": sign * nv * disc * (max(0, n_total - n) / n_total), "underlying": sid, "days_to_maturity": (mat - asof).days}
            return pv, an, st
        if p == "DIVIDEND_SWAP":
            sec = w.securities[T_["security_id"]]
            realized = _f(st.get("realized", 0.0))
            expected = self._expected_dividends(sec, asof, mat)
            K = _f(T_["fixed_dividends"])
            units = _f(T_["units"])
            sign = 1.0 if T_["buyer"] else -1.0
            pv = sign * units * (realized + expected - K) * disc
            an = {"fixed_dividends": K, "realized_dividends": realized, "expected_remaining": expected, "units": units, "delta_units": 0.0, "days_to_maturity": (mat - asof).days}
            return pv, an, st
        if p == "INFLATION_SWAP":
            N = _f(t.notional)
            K = _f(T_["fixed_rate"])
            cpi0, cpi = _f(T_["cpi_start"]), self._cpi()
            years_total = _f(T_["years"])
            ratio_now = cpi / cpi0 if cpi0 else 1.0
            exp_ratio = ratio_now * (1 + self._breakeven()) ** max(T, 0.0)
            fixed = (1 + K) ** years_total
            sign = 1.0 if T_["pay_fixed"] else -1.0                                     # paying fixed you receive inflation
            pv = sign * N * (exp_ratio - fixed) * disc
            an = {"fixed_rate": K, "breakeven_now": self._breakeven(), "cpi_start": cpi0, "cpi_now": cpi, "expected_ratio": exp_ratio, "fixed_ratio": fixed,
                  "inflation_dv01": sign * N * exp_ratio * max(T, 0.0) * 1e-4 * disc, "days_to_maturity": (mat - asof).days}
            return pv, an, st
        if p == "FX_SWAP":
            ccy = T_["ccy"]
            amt = _f(T_["amount"])
            K = _f(T_["forward_rate"])
            F = w.market.fx.forward(ccy, "USD", asof, mat) if T > 0 else w.market.fx.spot[ccy]
            sign = -1.0 if T_["direction"] == "BUY_SELL" else 1.0                       # BUY_SELL sells the currency forward
            pv = sign * amt * (F - K) * disc
            an = {"forward": F, "forward_rate": K, "spot": w.market.fx.spot[ccy], "fx_delta_1pct": sign * amt * w.market.fx.spot[ccy] * 0.01 * disc, "days_to_maturity": (mat - asof).days}
            return pv, an, st
        if p in ("BARRIER_OPTION", "DIGITAL_OPTION"):
            call = T_["option_type"] == "C"
            if T_.get("ccy"):
                S = w.market.fx.spot[T_["ccy"]]
                r = -math.log(max(1e-9, px.df(c, max(T, 1 / 365)))) / max(T, 1 / 365)
                q = w.market.fx.rate[T_["ccy"]]
                sigma = self.e.fx_vol(T_["ccy"])
            else:
                S, r, q, sigma = self._underlying(T_["security_id"], max(T, 0.05))
            K, units = _f(T_["strike"]), _f(T_["units"])
            sign = 1.0 if T_["buyer"] else -1.0
            if p == "BARRIER_OPTION":
                ko = T_["barrier_type"] == "KO"
                if st.get("knocked_out"):
                    prem = 0.0
                elif st.get("knocked_in") or T <= 0:
                    prem = bsm("C" if call else "P", S, K, max(T, 1 / 365), r, q, sigma) if T > 0 else (max(0.0, S - K) if call else max(0.0, K - S))
                    if T <= 0 and not (ko or st.get("knocked_in")):
                        prem = 0.0
                else:
                    prem = barrier_price(call, not ko, S, K, _f(T_["barrier"]), T, r, q, sigma)
                an = {"spot": S, "strike": K, "barrier": _f(T_["barrier"]), "barrier_type": T_["barrier_type"], "premium_per_unit": prem, "vol": sigma,
                      "knocked_in": bool(st.get("knocked_in")), "knocked_out": bool(st.get("knocked_out")), "days_to_expiry": (mat - asof).days}
            else:
                prem = digital_price(call, S, K, T, r, q, sigma, _f(T_["payout"]))
                an = {"spot": S, "strike": K, "payout": _f(T_["payout"]), "premium_per_unit": prem, "vol": sigma, "days_to_expiry": (mat - asof).days}
            dS = S * 0.01
            up = (barrier_price(call, T_.get("barrier_type") == "KI", S + dS, K, _f(T_["barrier"]), max(T, 1 / 365), r, q, sigma) if p == "BARRIER_OPTION" and not st.get("knocked_out") and not st.get("knocked_in")
                  else digital_price(call, S + dS, K, max(T, 1 / 365), r, q, sigma, _f(T_["payout"])) if p == "DIGITAL_OPTION" else prem)
            an["delta_units"] = sign * units * (up - prem) / dS if dS else 0.0
            return sign * units * prem, an, st
        if p == "ASIAN_OPTION":
            code = T_["code"]
            call = T_["option_type"] == "C"
            spot = w.market.spot(code)
            curve = w.market.curve_for(code)
            n_total = max(1, int(T_["sessions"]))
            n = int(st.get("n", 0))
            avg = _f(st.get("avg", 0.0))
            months_left = max(1, int(round(T * 12)))
            vals = list(curve.values())[:months_left] or [spot]
            F_avg = sum(vals) / len(vals)
            r = -math.log(max(1e-9, px.df(c, max(T, 1 / 365)))) / max(T, 1 / 365)
            sigma = _f(T_["vol_at_trade"])
            T_total = px.years(date.fromisoformat(t.start), mat)
            prem = asian_price(call, F_avg, _f(T_["strike"]), T_total, max(T, 0.0), r, sigma, avg, n, n_total)
            sign = 1.0 if T_["buyer"] else -1.0
            units = _f(T_["units"])
            an = {"spot": spot, "strike": _f(T_["strike"]), "average_so_far": avg if n else None, "sessions_done": n, "sessions": n_total, "forward_average": F_avg,
                  "premium_per_unit": prem, "delta_units": sign * units * 0.5 * (1.0 if call else -1.0) * (n_total - n) / n_total, "days_to_expiry": (mat - asof).days}
            return sign * units * prem, an, st
        if p == "CRYPTO_PERP":
            spot = _f(w.market.last_bar(T_["coin"]).close)
            units, entry = _f(T_["units"]), _f(T_["entry_price"])
            sign = 1.0 if T_["long"] else -1.0
            pv = sign * units * (spot - entry)
            an = {"spot": spot, "entry_price": entry, "delta_units": sign * units, "funding_paid": _f(st.get("funding", 0.0)), "funding_daily": _f(T_["funding_daily"]),
                  "notional": units * spot, "leverage_note": "margined under the dealer's CSA; funding settles daily"}
            return pv, an, st
        if p in ("PPN", "REVERSE_CONVERTIBLE", "AUTOCALLABLE"):
            sid = T_["security_id"]
            S, r, q, sigma = self._underlying(sid, max(T, 0.05))
            S0 = _f(T_["initial_level"])
            P = dict(T_)
            P["tenor_years"] = T_["years"]
            P["years_total"] = T_["years"]
            P["elapsed_years"] = max(0.0, px.years(date.fromisoformat(t.start), asof))
            if p == "AUTOCALLABLE":
                P["observations"] = [px.years(date.fromisoformat(t.start), date.fromisoformat(o)) for o in T_["observations"]]
            price, extra = self._note_price(p, P, S, r, q, sigma, max(T, 1 / 365), S0, 0.0, asof, knocked=bool(st.get("knocked_in")))
            N = _f(t.notional)
            an = {"price_pct": price * 100.0, "spot": S, "initial_level": S0, "performance": S / S0 - 1.0 if S0 else 0.0, "vol": sigma, "days_to_maturity": (mat - asof).days,
                  "delta_units": N / S0 * (extra.get("participation", 1.0) * 0.5) if S0 else 0.0, **{k: v for k, v in extra.items() if k not in ("observations",)}}
            if p == "REVERSE_CONVERTIBLE":
                an["barrier_breached"] = bool(st.get("knocked_in"))
            return price * N, an, st
        raise ValueError(p)

    # ------------------------------------------------------------- daily lifecycle
    def lifecycle(self, pf, t, cause, prev: date, paid: set, days: int) -> None:
        w = self.w
        e = self.e
        today = w.current_date
        iso = today.isoformat()
        T_ = t.terms
        p = t.product
        mat = date.fromisoformat(t.maturity)
        st = dict(t.state)
        if p in ("VARIANCE_SWAP", "VOL_SWAP"):
            sid = T_["security_id"]
            from .vol import index_source
            h = w.market.history.get(index_source(sid))
            if h and len(h) >= 2 and t.trade_date != iso:
                r_ = math.log(_f(h[-1].close) / _f(h[-2].close)) if _f(h[-2].close) > 0 else 0.0
                st["sum_sq"] = _f(st.get("sum_sq", 0.0)) + r_ * r_
                st["n"] = int(st.get("n", 0)) + 1
            t.state = st
            if today >= mat:
                n = max(1, int(st.get("n", 1)))
                rv_var = 252.0 / n * _f(st.get("sum_sq", 0.0)) * 1e4
                K = _f(T_["strike"])
                nv = _f(T_["vega_notional"])
                if p == "VARIANCE_SWAP":
                    amt = nv / (2 * K) * (rv_var - K * K) if K else 0.0
                    note = f"settled: realised variance {rv_var:,.1f} (vol {math.sqrt(rv_var):.2f}) vs strike {K:.2f}² over {n} sessions"
                else:
                    amt = nv * (math.sqrt(rv_var) - K)
                    note = f"settled: realised vol {math.sqrt(rv_var):.2f} vs strike {K:.2f} over {n} sessions"
                e._cashflow(pf, t, "VARIANCE_SETTLEMENT", D(repr(amt if T_["buyer"] else -amt)), t.currency, note, cause)
                e._status(pf, t, "MATURED", cause, note)
        elif p == "DIVIDEND_SWAP":
            sid = T_["security_id"]
            for ca in w.corporate_actions.values():
                if ca.security_id == sid and ca.ex_date == iso and ca.action_type == "CASH_DIVIDEND":
                    st["realized"] = _f(st.get("realized", 0.0)) + _f(ca.amount_per_unit)
                    st.setdefault("log", []).append({"date": iso, "per_unit": _f(ca.amount_per_unit)})
            t.state = st
            if today >= mat:
                amt = _f(T_["units"]) * (_f(st.get("realized", 0.0)) - _f(T_["fixed_dividends"]))
                note = f"settled: dividends paid {_f(st.get('realized', 0.0)):.4f} per unit vs fixed {_f(T_['fixed_dividends']):.4f}"
                e._cashflow(pf, t, "DIVIDEND_SETTLEMENT", D(repr(amt if T_["buyer"] else -amt)), t.currency, note, cause)
                e._status(pf, t, "MATURED", cause, note)
        elif p == "INFLATION_SWAP":
            if today >= mat:
                N = _f(t.notional)
                ratio = self._cpi() / _f(T_["cpi_start"]) if _f(T_["cpi_start"]) else 1.0
                fixed = (1 + _f(T_["fixed_rate"])) ** _f(T_["years"])
                amt = N * (ratio - fixed)
                note = f"settled: CPI ratio {ratio:.4f} vs fixed {fixed:.4f} on {N:,.0f}"
                e._cashflow(pf, t, "INFLATION_SETTLEMENT", D(repr(amt if T_["pay_fixed"] else -amt)), t.currency, note, cause)
                e._status(pf, t, "MATURED", cause, note)
        elif p == "FX_SWAP":
            if today >= mat:
                ccy = T_["ccy"]
                amt = D(str(T_["amount"]))
                usd_far = D(str(T_["usd_far"]))
                buy_sell = T_["direction"] == "BUY_SELL"
                legs = [{"currency": "USD", "amount": money(usd_far if buy_sell else -usd_far)}, {"currency": ccy, "amount": money(-amt if buy_sell else amt)}]
                e._cashflow(pf, t, "NOTIONAL_EXCHANGE", ZERO, "USD", f"far leg at {_f(T_['forward_rate']):.5f}: {'sell' if buy_sell else 'buy'} {ccy} {amt:,.0f}", cause, legs=legs)
                e._status(pf, t, "MATURED", cause, "far leg exchanged; swap matured")
        elif p == "BARRIER_OPTION":
            call = T_["option_type"] == "C"
            S = w.market.fx.spot[T_["ccy"]] if T_.get("ccy") else self._underlying(T_["security_id"], 0.1)[0]
            H = _f(T_["barrier"])
            S0 = _f(T_["spot_at_trade"])
            touched = (S <= H) if H < S0 else (S >= H)
            ko = T_["barrier_type"] == "KO"
            if touched and not st.get("knocked_in") and not st.get("knocked_out"):
                if ko:
                    st["knocked_out"] = iso
                    t.state = st
                    e._status(pf, t, "EXPIRED", cause, f"knocked out: {S:.4f} through the barrier {H:.4f}")
                    return
                st["knocked_in"] = iso
                t.notes.append({"date": iso, "note": f"knocked in: {S:.4f} through the barrier {H:.4f}; now a vanilla {'call' if call else 'put'}"})
            t.state = st
            if today >= mat:
                alive = (not ko and st.get("knocked_in")) or (ko and not st.get("knocked_out"))
                K = _f(T_["strike"])
                intrinsic = (max(0.0, S - K) if call else max(0.0, K - S)) if alive else 0.0
                units = _f(T_["units"])
                if intrinsic > 0:
                    value = units * intrinsic
                    e._cashflow(pf, t, "EXERCISE_SETTLEMENT", D(repr(value if T_["buyer"] else -value)), t.currency, f"cash settlement at expiry: {S:.4f} vs strike {K:.4f} x {units:,.0f}", cause)
                    e._status(pf, t, "EXERCISED", cause, "expired in the money")
                else:
                    e._status(pf, t, "EXPIRED", cause, "expired worthless" + ("" if alive else " (never knocked in)" if not ko else ""))
        elif p == "DIGITAL_OPTION":
            if today >= mat:
                call = T_["option_type"] == "C"
                S = w.market.fx.spot[T_["ccy"]] if T_.get("ccy") else self._underlying(T_["security_id"], 0.1)[0]
                K = _f(T_["strike"])
                hit = (S > K) if call else (S < K)
                if hit:
                    value = _f(T_["units"]) * _f(T_["payout"])
                    e._cashflow(pf, t, "EXERCISE_SETTLEMENT", D(repr(value if T_["buyer"] else -value)), t.currency, f"digital paid: {S:.4f} {'>' if call else '<'} {K:.4f}", cause)
                    e._status(pf, t, "EXERCISED", cause, "finished in the money")
                else:
                    e._status(pf, t, "EXPIRED", cause, f"finished out of the money ({S:.4f} vs {K:.4f})")
        elif p == "ASIAN_OPTION":
            spot = w.market.spot(T_["code"])
            if t.trade_date != iso or True:
                n = int(st.get("n", 0))
                st["avg"] = (_f(st.get("avg", 0.0)) * n + spot) / (n + 1)
                st["n"] = n + 1
            t.state = st
            if today >= mat:
                call = T_["option_type"] == "C"
                K = _f(T_["strike"])
                avg = _f(st["avg"])
                intrinsic = max(0.0, avg - K) if call else max(0.0, K - avg)
                units = _f(T_["units"])
                if intrinsic > 0:
                    value = units * intrinsic
                    e._cashflow(pf, t, "EXERCISE_SETTLEMENT", D(repr(value if T_["buyer"] else -value)), t.currency, f"average {avg:.4f} vs strike {K:.4f} x {units:,.0f}", cause)
                    e._status(pf, t, "EXERCISED", cause, "average finished in the money")
                else:
                    e._status(pf, t, "EXPIRED", cause, f"average {avg:.4f} finished out of the money vs {K:.4f}")
        elif p == "CRYPTO_PERP":
            if t.trade_date != iso:
                spot = _f(w.market.last_bar(T_["coin"]).close)
                fund = _f(T_["units"]) * spot * _f(T_["funding_daily"]) * days
                amt = -fund if T_["long"] else fund                           # longs pay the funding
                st["funding"] = _f(st.get("funding", 0.0)) + amt
                t.state = st
                e._cashflow(pf, t, "PERP_FUNDING", D(repr(amt)), t.currency, f"funding {days} day(s) at {_f(T_['funding_daily']):.4%}/day on {spot:,.2f}", cause)
        elif p in ("PPN", "REVERSE_CONVERTIBLE", "AUTOCALLABLE"):
            sid = T_["security_id"]
            S = self._underlying(sid, 0.1)[0]
            S0 = _f(T_["initial_level"])
            N = _f(t.notional)
            if p == "REVERSE_CONVERTIBLE" and S <= S0 * _f(T_["barrier_pct"]) / 100.0 and not st.get("knocked_in"):
                st["knocked_in"] = iso
                t.state = st
                t.notes.append({"date": iso, "note": f"barrier breached at {S:.2f} ({S / S0:.1%} of the initial level): the note now returns the index at maturity if below par"})
            if p == "AUTOCALLABLE":
                obs = [o for o in T_["observations"] if o <= iso and "OBS:" + o not in paid]
                for o in obs:
                    paid.add("OBS:" + o)
                    st["paid"] = sorted(paid)
                    t.state = st
                    yrs = px.years(date.fromisoformat(t.start), date.fromisoformat(o))
                    if S >= S0 * _f(T_["autocall_pct"]) / 100.0:
                        value = N * (1 + _f(T_["coupon"]) * yrs)
                        e._cashflow(pf, t, "NOTE_REDEMPTION", D(repr(value)), t.currency, f"autocalled at {S:.2f} ≥ {S0 * _f(T_['autocall_pct']) / 100:.2f}: par plus {_f(T_['coupon']) * yrs:.1%}", cause)
                        e._status(pf, t, "MATURED", cause, f"autocalled on {o}")
                        return
                    t.notes.append({"date": o, "note": f"observation: {S:.2f} below the autocall level {S0 * _f(T_['autocall_pct']) / 100:.2f}; coupon carried (memory)"})
            if today >= mat:
                yrs = _f(T_["years"])
                if p == "PPN":
                    value = N * (1 + _f(T_["participation"]) * max(0.0, S / S0 - 1.0))
                    note = f"matured: principal plus {_f(T_['participation']):.0%} of the {S / S0 - 1:+.1%} index return"
                elif p == "REVERSE_CONVERTIBLE":
                    cpn = _f(T_["coupon"]) * yrs
                    if st.get("knocked_in") and S < S0:
                        value = N * (cpn + S / S0)
                        note = f"matured below par after a barrier breach: coupon {cpn:.1%} plus {S / S0:.1%} of principal"
                    else:
                        value = N * (1 + cpn)
                        note = f"matured at par plus {cpn:.1%} coupon"
                else:
                    if S >= S0 * _f(T_["barrier_pct"]) / 100.0:
                        value = N * (1 + _f(T_["coupon"]) * yrs)
                        note = f"matured above the barrier: par plus {_f(T_['coupon']) * yrs:.1%}"
                    else:
                        value = N * S / S0
                        note = f"matured below the barrier: {S / S0:.1%} of principal"
                e._cashflow(pf, t, "NOTE_REDEMPTION", D(repr(value)), t.currency, note, cause)
                e._status(pf, t, "MATURED", cause, note)

    # ------------------------------------------------------------- words
    def describe(self, t) -> str:
        T = t.terms
        p = t.product
        if p in ("VARIANCE_SWAP", "VOL_SWAP"):
            return f"{'Long' if T['buyer'] else 'Short'} {'variance' if p == 'VARIANCE_SWAP' else 'volatility'} on {T['security_id']} struck at {_f(T['strike']):.2f}, {_f(T['vega_notional']):,.0f} per vol point, to {t.maturity}"
        if p == "DIVIDEND_SWAP":
            return f"{'Receive' if T['buyer'] else 'Pay'} realised dividends vs fixed {_f(T['fixed_dividends']):.4f} per unit on {_f(T['units']):,.0f} {T['security_id']}, to {t.maturity}"
        if p == "INFLATION_SWAP":
            return f"{'Pay' if T['pay_fixed'] else 'Receive'} fixed {_f(T['fixed_rate']):.3%} vs CPI on {t.notional:,.0f}, zero-coupon to {t.maturity}"
        if p == "FX_SWAP":
            bs = T["direction"] == "BUY_SELL"
            return f"{'Buy' if bs else 'Sell'} {T['ccy']} {_f(T['amount']):,.0f} at spot {_f(T['spot_rate']):.5f}, {'sell' if bs else 'buy'} it back at {_f(T['forward_rate']):.5f} on {t.maturity}"
        if p == "BARRIER_OPTION":
            ref = T.get("security_id") or T.get("ccy") + "/USD"
            return f"{'Long' if T['buyer'] else 'Short'} {T['barrier_type']} {'call' if T['option_type'] == 'C' else 'put'} on {_f(T['units']):,.0f} {ref} strike {_f(T['strike']):,.4f}, barrier {_f(T['barrier']):,.4f}, expires {t.maturity}"
        if p == "DIGITAL_OPTION":
            ref = T.get("security_id") or T.get("ccy") + "/USD"
            return f"{'Long' if T['buyer'] else 'Short'} digital {'call' if T['option_type'] == 'C' else 'put'} on {ref}: pays {_f(T['payout']):,.2f} x {_f(T['units']):,.0f} if {'above' if T['option_type'] == 'C' else 'below'} {_f(T['strike']):,.4f} on {t.maturity}"
        if p == "ASIAN_OPTION":
            return f"{'Long' if T['buyer'] else 'Short'} Asian {'call' if T['option_type'] == 'C' else 'put'} on {_f(T['units']):,.0f} {T['code']} strike {_f(T['strike']):.4f} vs the average spot to {t.maturity}"
        if p == "CRYPTO_PERP":
            return f"{'Long' if T['long'] else 'Short'} {_f(T['units']):,.4f} {T['coin']} perpetual from {_f(T['entry_price']):,.2f}; funding {_f(T['funding_daily']):.3%}/day"
        if p == "PPN":
            return f"Principal-protected note on {T['security_id']}: {_f(T['participation']):.0%} of the upside from {_f(T['initial_level']):,.2f}, {t.notional:,.0f} to {t.maturity}"
        if p == "REVERSE_CONVERTIBLE":
            return f"Reverse convertible on {T['security_id']}: {_f(T['coupon']):.1%} coupon, {_f(T['barrier_pct']):.0f}% barrier from {_f(T['initial_level']):,.2f}, {t.notional:,.0f} to {t.maturity}"
        if p == "AUTOCALLABLE":
            return f"Autocallable on {T['security_id']}: {_f(T['coupon']):.1%} p.a., autocall at {_f(T['autocall_pct']):.0f}%, barrier {_f(T['barrier_pct']):.0f}%, {t.notional:,.0f} to {t.maturity}"
        return p

    def upcoming(self, t, add, paid: set, mat: date) -> None:
        T = t.terms
        p = t.product
        if p in ("VARIANCE_SWAP", "VOL_SWAP"):
            add(t, mat, "VARIANCE_SETTLEMENT", f"realised vs strike {_f(T['strike']):.2f} settles")
        elif p == "DIVIDEND_SWAP":
            add(t, mat, "DIVIDEND_SETTLEMENT", f"dividends paid vs fixed {_f(T['fixed_dividends']):.4f} settle")
        elif p == "INFLATION_SWAP":
            add(t, mat, "INFLATION_SETTLEMENT", "CPI ratio vs the fixed rate settles")
        elif p == "FX_SWAP":
            add(t, mat, "NOTIONAL_EXCHANGE", f"far leg at {_f(T['forward_rate']):.5f}")
        elif p in ("BARRIER_OPTION", "DIGITAL_OPTION", "ASIAN_OPTION"):
            add(t, mat, "EXPIRY", f"cash settlement vs strike {_f(T['strike']):,.4f}")
        elif p == "AUTOCALLABLE":
            for o in T["observations"]:
                if "OBS:" + o not in paid:
                    add(t, date.fromisoformat(o), "AUTOCALL_OBSERVATION", f"autocalls if the index is at or above {_f(T['autocall_pct']):.0f}% of {_f(T['initial_level']):,.2f}")
        elif p in ("PPN", "REVERSE_CONVERTIBLE"):
            add(t, mat, "NOTE_REDEMPTION", "the note redeems")
