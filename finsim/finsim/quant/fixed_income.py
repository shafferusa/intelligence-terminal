"""Fixed income (equations 111-118). Times are in years; yields are annual rates."""
import math


def bond_cash_flows(face, coupon_rate, maturity, freq=2):
    """Coupon-bond schedule: returns (times, cash_flows) for a bullet bond."""
    n = max(1, int(round(maturity * freq)))
    c = face * coupon_rate / freq
    times = [(i + 1) / freq for i in range(n)]
    cfs = [c] * n
    cfs[-1] += face
    return times, cfs


def bond_price_cf(cash_flows, times, y, freq=1):
    """Eq 111: price P = Σ CF_t / (1 + y/m)^{m t} (m = compounding frequency)."""
    return sum(cf / (1.0 + y / freq) ** (freq * t) for cf, t in zip(cash_flows, times))


def bond_price(face, coupon_rate, y, maturity, freq=2):
    """Eq 111 for a bullet bond paying coupon_rate·face/m m times a year, yield y compounded m times."""
    times, cfs = bond_cash_flows(face, coupon_rate, maturity, freq)
    return bond_price_cf(cfs, times, y, freq)


def ytm(price, cash_flows, times, freq=1, lo=-0.99, hi=5.0, tol=1e-12, max_iter=200):
    """Eq 112: yield to maturity — the y solving P = Σ CF_t/(1 + y/m)^{mt} (safeguarded Newton/bisection)."""
    f = lambda y: bond_price_cf(cash_flows, times, y, freq) - price  # noqa: E731
    lo = max(lo, -freq + 1e-9)
    flo, fhi = f(lo), f(hi)
    if flo * fhi > 0:
        raise ValueError("yield not bracketed")
    y = 0.05 if lo < 0.05 < hi else 0.5 * (lo + hi)
    for _ in range(max_iter):
        fy = f(y)
        if abs(fy) < tol * max(1.0, price):
            return y
        if fy > 0:  # price too high -> yield too low
            lo = y
        else:
            hi = y
        h = 1e-7
        d = (f(y + h) - f(y - h)) / (2 * h)
        nxt = y - fy / d if d != 0 else None
        y = nxt if nxt is not None and lo < nxt < hi else 0.5 * (lo + hi)
        if hi - lo < 1e-15:
            break
    return y


def macaulay_duration(cash_flows, times, y, freq=1):
    """Eq 113: Macaulay duration D = Σ t · PV(CF_t) / P."""
    pvs = [cf / (1.0 + y / freq) ** (freq * t) for cf, t in zip(cash_flows, times)]
    return sum(t * pv for t, pv in zip(times, pvs)) / sum(pvs)


def modified_duration(cash_flows, times, y, freq=1):
    """Eq 114: modified duration D_mod = D_mac / (1 + y/m)."""
    return macaulay_duration(cash_flows, times, y, freq) / (1.0 + y / freq)


def convexity(cash_flows, times, y, freq=1):
    """Eq 115: convexity (1/P) ∂²P/∂y² = Σ CF_t t(t + 1/m) / (1 + y/m)^{mt + 2} / P."""
    g = 1.0 + y / freq
    p = bond_price_cf(cash_flows, times, y, freq)
    return sum(cf * t * (t + 1.0 / freq) / g ** (freq * t + 2) for cf, t in zip(cash_flows, times)) / p


def price_change_approx(price, mod_duration, conv, dy):
    """Eq 115: ΔP ≈ P(−D_mod Δy + ½ C Δy²)."""
    return price * (-mod_duration * dy + 0.5 * conv * dy * dy)


def dv01(price, mod_duration):
    """Eq 116: DV01 = D_mod × P × 0.0001 (price change for a one-basis-point yield move)."""
    return mod_duration * price * 1e-4


def zero_coupon_price(y, T, face=1.0, continuous=False):
    """Eq 117: zero-coupon price F/(1 + y)^T, or F e^{−yT} with continuous compounding."""
    return face * math.exp(-y * T) if continuous else face / (1.0 + y) ** T


def forward_rate(r1, T1, r2, T2, continuous=True):
    """Eq 118: forward rate between T1 and T2 implied by spot rates r1, r2.

    Continuous: (r₂T₂ − r₁T₁)/(T₂ − T₁); annual compounding: [(1+r₂)^{T₂}/(1+r₁)^{T₁}]^{1/(T₂−T₁)} − 1.
    """
    if continuous:
        return (r2 * T2 - r1 * T1) / (T2 - T1)
    return ((1.0 + r2) ** T2 / (1.0 + r1) ** T1) ** (1.0 / (T2 - T1)) - 1.0
