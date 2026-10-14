"""Credit risk and credit default swaps (equations C1-C9)."""
import math


def loss_given_default(recovery):
    """C1: loss given default LGD = 1 − R."""
    return 1.0 - recovery


def expected_loss(pd, lgd, ead):
    """C2: expected loss EL = PD · LGD · EAD."""
    return pd * lgd * ead


def survival_probability(hazard, t):
    """C3: survival probability Q(t) = e^{−λt} under a constant hazard rate."""
    return math.exp(-hazard * t)


def default_probability(hazard, t):
    """C4: cumulative default probability PD(t) = 1 − e^{−λt}."""
    return 1.0 - math.exp(-hazard * t)


def credit_triangle_spread(hazard, recovery):
    """C5: credit triangle s ≈ λ (1 − R)."""
    return hazard * (1.0 - recovery)


def cds_schedule(hazard, rate, maturity, freq=4):
    """Payment schedule for a flat hazard and flat continuous rate: (times, accruals, dfs, survival)."""
    n = max(1, int(round(maturity * freq)))
    times = [(i + 1) / freq for i in range(n)]
    acc = [1.0 / freq] * n
    dfs = [math.exp(-rate * t) for t in times]
    q = [survival_probability(hazard, t) for t in times]
    return times, acc, dfs, q


def cds_protection_leg_pv(notional, recovery, dfs, survival):
    """C6: protection leg PV N (1 − R) Σ DF_i ΔPD_i with ΔPD_i = Q_{i−1} − Q_i (Q_0 = 1)."""
    prev, s = 1.0, 0.0
    for d, q in zip(dfs, survival):
        s += d * (prev - q)
        prev = q
    return notional * (1.0 - recovery) * s


def cds_premium_leg_pv(notional, spread, accruals, dfs, survival):
    """C7: premium leg PV N · s · Σ α_i DF_i Q_i (accrual on default ignored)."""
    return notional * spread * sum(a * d * q for a, d, q in zip(accruals, dfs, survival))


def par_cds_spread(recovery, accruals, dfs, survival):
    """C8: par CDS spread s* = (1 − R) Σ DF_i ΔPD_i / Σ α_i DF_i Q_i (protection PV = premium PV)."""
    return cds_protection_leg_pv(1.0, recovery, dfs, survival) / cds_premium_leg_pv(1.0, 1.0, accruals, dfs, survival)


def bootstrap_flat_hazard(spread, recovery, maturity, rate=0.0, freq=4, tol=1e-12):
    """C9: flat hazard rate λ that reprices a CDS quoted at `spread` (bisection; starts near s/(1 − R))."""
    def f(h):
        _, acc, dfs, q = cds_schedule(h, rate, maturity, freq)
        return par_cds_spread(recovery, acc, dfs, q) - spread
    lo, hi = 0.0, max(1.0, 4.0 * spread / max(1e-9, 1.0 - recovery))
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)
