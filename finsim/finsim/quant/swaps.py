"""Swaps and total return swaps (equations S1-S9). α are accrual fractions, DF discount factors."""


def swap_pv(pv_receive, pv_pay):
    """S1: swap value to the holder PV = PV_receive − PV_pay."""
    return pv_receive - pv_pay


def fixed_leg_pv(notional, fixed_rate, accruals, dfs):
    """S2: fixed leg PV N · K · Σ α_i DF_i."""
    return notional * fixed_rate * sum(a * d for a, d in zip(accruals, dfs))


def floating_leg_pv(notional, float_rates, accruals, dfs):
    """S3: floating leg PV N · Σ L_i α_i DF_i."""
    return notional * sum(l * a * d for l, a, d in zip(float_rates, accruals, dfs))


def irs_pv(notional, float_rates, fixed_rate, accruals, dfs):
    """S4: interest-rate swap PV to the fixed payer N · Σ (L_i − K) α_i DF_i."""
    return notional * sum((l - fixed_rate) * a * d for l, a, d in zip(float_rates, accruals, dfs))


def par_swap_rate(float_rates, accruals, dfs):
    """S5: par swap rate K* = Σ L_i α_i DF_i / Σ α_i DF_i (the fixed rate giving zero PV)."""
    return sum(l * a * d for l, a, d in zip(float_rates, accruals, dfs)) / sum(a * d for a, d in zip(accruals, dfs))


def forward_rates_from_dfs(dfs, accruals):
    """Simple forward rates L_i = (DF_{i−1}/DF_i − 1)/α_i implied by a discount curve (DF_0 = 1)."""
    prev = 1.0
    out = []
    for d, a in zip(dfs, accruals):
        out.append((prev / d - 1.0) / a)
        prev = d
    return out


def trs_total_return(s0, s_t, dividends=0.0):
    """S6: equity total return TR = (S_t − S₀ + D_t) / S₀."""
    return (s_t - s0 + dividends) / s0


def trs_equity_leg(notional, total_return):
    """S7: TRS equity-leg cash flow N · TR."""
    return notional * total_return


def trs_financing_leg(notional, ref_rate, spread, dt):
    """S8: TRS financing-leg cash flow N (r_ref + s) Δt."""
    return notional * (ref_rate + spread) * dt


def trs_net_cash_flow(notional, s0, s_t, dividends, ref_rate, spread, dt):
    """S9: net cash flow to the total-return receiver N·TR − N(r_ref + s)Δt."""
    return trs_equity_leg(notional, trs_total_return(s0, s_t, dividends)) - \
        trs_financing_leg(notional, ref_rate, spread, dt)
