"""Forwards, futures and options (equations D1-D20). Rates and yields are continuously compounded."""
import math

from .stats import norm_cdf, norm_pdf


# ---------------------------------------------------------------- forwards and futures

def forward_price(spot, r, T):
    """D1: forward price F₀ = S₀ e^{rT} (no income)."""
    return spot * math.exp(r * T)


def forward_price_dividend(spot, r, q, T):
    """D2: forward price with continuous dividend yield F₀ = S₀ e^{(r − q)T}."""
    return spot * math.exp((r - q) * T)


def fx_forward(spot, r_dom, r_for, T):
    """D3: FX forward (covered interest parity) F₀ = S₀ e^{(r_d − r_f)T}, S in domestic per foreign."""
    return spot * math.exp((r_dom - r_for) * T)


def commodity_forward(spot, r, storage, convenience, T):
    """D4: commodity cost of carry F₀ = S₀ e^{(r + c − y)T}."""
    return spot * math.exp((r + storage - convenience) * T)


def futures_pnl_long(contracts, multiplier, f0, ft):
    """D5: long futures P&L N · M · (F_t − F₀)."""
    return contracts * multiplier * (ft - f0)


def futures_pnl_short(contracts, multiplier, f0, ft):
    """D6: short futures P&L N · M · (F₀ − F_t)."""
    return contracts * multiplier * (f0 - ft)


# ---------------------------------------------------------------- option payoffs and parity

def call_payoff(s_T, strike):
    """Long call payoff max(S_T − K, 0)."""
    return max(s_T - strike, 0.0)


def put_payoff(s_T, strike):
    """Long put payoff max(K − S_T, 0)."""
    return max(strike - s_T, 0.0)


def long_call(s_T, strike, premium=0.0):
    """D7: long call payoff max(S_T − K, 0) and profit payoff − premium."""
    p = call_payoff(s_T, strike)
    return {"payoff": p, "profit": p - premium}


def long_put(s_T, strike, premium=0.0):
    """D8: long put payoff max(K − S_T, 0) and profit payoff − premium."""
    p = put_payoff(s_T, strike)
    return {"payoff": p, "profit": p - premium}


def parity_put_from_call(call, spot, strike, r, T, q=0.0, pv_dividends=0.0):
    """D9: put-call parity P = C − S e^{−qT} + PV(D) + K e^{−rT} (implied put from a call)."""
    return call - spot * math.exp(-q * T) + pv_dividends + strike * math.exp(-r * T)


def parity_gap(call, put, spot, strike, r, T, q=0.0, pv_dividends=0.0):
    """D9: parity gap (C − P) − (S e^{−qT} − PV(D) − K e^{−rT}); zero when parity holds."""
    return (call - put) - (spot * math.exp(-q * T) - pv_dividends - strike * math.exp(-r * T))


# ---------------------------------------------------------------- Black-Scholes-Merton

def d1_d2(spot, strike, r, sigma, T, q=0.0):
    """D12: d₁ = [ln(S/K) + (r − q + σ²/2)T] / (σ√T), d₂ = d₁ − σ√T."""
    v = sigma * math.sqrt(T)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * T) / v
    return d1, d1 - v


def bsm_call(spot, strike, r, sigma, T, q=0.0):
    """D10: BSM call C = S e^{−qT} N(d₁) − K e^{−rT} N(d₂)."""
    if T <= 0 or sigma <= 0:
        return max(spot * math.exp(-q * max(T, 0)) - strike * math.exp(-r * max(T, 0)), 0.0)
    d1, d2 = d1_d2(spot, strike, r, sigma, T, q)
    return spot * math.exp(-q * T) * norm_cdf(d1) - strike * math.exp(-r * T) * norm_cdf(d2)


def bsm_put(spot, strike, r, sigma, T, q=0.0):
    """D11: BSM put P = K e^{−rT} N(−d₂) − S e^{−qT} N(−d₁)."""
    if T <= 0 or sigma <= 0:
        return max(strike * math.exp(-r * max(T, 0)) - spot * math.exp(-q * max(T, 0)), 0.0)
    d1, d2 = d1_d2(spot, strike, r, sigma, T, q)
    return strike * math.exp(-r * T) * norm_cdf(-d2) - spot * math.exp(-q * T) * norm_cdf(-d1)


def call_delta(spot, strike, r, sigma, T, q=0.0):
    """D13: call delta Δ_C = e^{−qT} N(d₁)."""
    return math.exp(-q * T) * norm_cdf(d1_d2(spot, strike, r, sigma, T, q)[0])


def put_delta(spot, strike, r, sigma, T, q=0.0):
    """D14: put delta Δ_P = e^{−qT} (N(d₁) − 1)."""
    return math.exp(-q * T) * (norm_cdf(d1_d2(spot, strike, r, sigma, T, q)[0]) - 1.0)


def gamma(spot, strike, r, sigma, T, q=0.0):
    """D15: gamma Γ = e^{−qT} φ(d₁) / (S σ √T) (same for calls and puts)."""
    d1 = d1_d2(spot, strike, r, sigma, T, q)[0]
    return math.exp(-q * T) * norm_pdf(d1) / (spot * sigma * math.sqrt(T))


def vega(spot, strike, r, sigma, T, q=0.0):
    """D16: vega ν = S e^{−qT} φ(d₁) √T (per unit of volatility)."""
    d1 = d1_d2(spot, strike, r, sigma, T, q)[0]
    return spot * math.exp(-q * T) * norm_pdf(d1) * math.sqrt(T)


def call_rho(spot, strike, r, sigma, T, q=0.0):
    """D17: call rho ρ_C = K T e^{−rT} N(d₂)."""
    return strike * T * math.exp(-r * T) * norm_cdf(d1_d2(spot, strike, r, sigma, T, q)[1])


def put_rho(spot, strike, r, sigma, T, q=0.0):
    """D18: put rho ρ_P = −K T e^{−rT} N(−d₂)."""
    return -strike * T * math.exp(-r * T) * norm_cdf(-d1_d2(spot, strike, r, sigma, T, q)[1])


def theta(spot, strike, r, sigma, T, q=0.0, kind="call"):
    """D19: BSM theta ∂V/∂t per year (negative means time decay)."""
    d1, d2 = d1_d2(spot, strike, r, sigma, T, q)
    decay = -spot * math.exp(-q * T) * norm_pdf(d1) * sigma / (2.0 * math.sqrt(T))
    if kind == "call":
        return decay - r * strike * math.exp(-r * T) * norm_cdf(d2) + q * spot * math.exp(-q * T) * norm_cdf(d1)
    return decay + r * strike * math.exp(-r * T) * norm_cdf(-d2) - q * spot * math.exp(-q * T) * norm_cdf(-d1)


def implied_vol(price, spot, strike, r, T, q=0.0, kind="call", lo=1e-6, hi=5.0, tol=1e-10):
    """D20: implied volatility — the σ at which the BSM price equals the market price (Newton with bisection)."""
    f = bsm_call if kind == "call" else bsm_put
    if not f(spot, strike, r, lo, T, q) - 1e-12 <= price <= f(spot, strike, r, hi, T, q) + 1e-12:
        raise ValueError("price outside no-arbitrage bounds")
    s = 0.2
    for _ in range(100):
        diff = f(spot, strike, r, s, T, q) - price
        if abs(diff) < tol:
            return s
        if diff > 0:
            hi = s
        else:
            lo = s
        v = vega(spot, strike, r, s, T, q)
        nxt = s - diff / v if v > 1e-12 else None
        s = nxt if nxt is not None and lo < nxt < hi else 0.5 * (lo + hi)
    return s
