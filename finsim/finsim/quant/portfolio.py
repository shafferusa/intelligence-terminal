"""Portfolio theory, risk and asset pricing (equations 101-110)."""
import math

from .linalg import dot, matvec, solve
from .stats import covariance, norm_pdf, norm_ppf, variance


def portfolio_variance(w, cov):
    """Portfolio variance wᵀΣw."""
    return dot(w, matvec(cov, w))


def mean_variance_objective(w, mu, cov, risk_aversion):
    """Eq 101 objective: wᵀμ − (λ/2) wᵀΣw."""
    return dot(w, mu) - 0.5 * risk_aversion * portfolio_variance(w, cov)


def mean_variance_weights(mu, cov, risk_aversion):
    """Eq 101: unconstrained mean-variance optimum w* = Σ⁻¹μ / λ."""
    return [v / risk_aversion for v in solve(cov, mu)]


def mean_variance_weights_budget(mu, cov, risk_aversion):
    """Eq 101 with the budget constraint 1ᵀw = 1 (Lagrangian): w* = Σ⁻¹(μ − γ1)/λ,
    with γ = (1ᵀΣ⁻¹μ − λ) / 1ᵀΣ⁻¹1."""
    n = len(mu)
    inv_mu = solve(cov, mu)
    inv_1 = solve(cov, [1.0] * n)
    a, b = sum(inv_1), sum(inv_mu)
    gamma = (b - risk_aversion) / a
    return [(im - gamma * i1) / risk_aversion for im, i1 in zip(inv_mu, inv_1)]


def is_fully_invested(w, tol=1e-9):
    """Eq 102: fully-invested constraint Σ w_i = 1."""
    return abs(sum(w) - 1.0) <= tol


def frontier_weights(mu, cov, target_return):
    """Eq 103: minimum-variance weights for a target return (constraints wᵀμ = m, 1ᵀw = 1).

    w = Σ⁻¹[(C − Bm)1 + (Am − B)μ] / (AC − B²), A = 1ᵀΣ⁻¹1, B = 1ᵀΣ⁻¹μ, C = μᵀΣ⁻¹μ.
    """
    n = len(mu)
    inv_1 = solve(cov, [1.0] * n)
    inv_mu = solve(cov, mu)
    A, B, C = sum(inv_1), sum(inv_mu), dot(mu, inv_mu)
    D = A * C - B * B
    lam1 = (C - B * target_return) / D
    lam2 = (A * target_return - B) / D
    return [lam1 * a + lam2 * b for a, b in zip(inv_1, inv_mu)]


def gmv_weights(cov):
    """Eq 104: global minimum-variance portfolio w = Σ⁻¹1 / (1ᵀΣ⁻¹1)."""
    inv_1 = solve(cov, [1.0] * len(cov))
    s = sum(inv_1)
    return [v / s for v in inv_1]


def risk_contributions(w, cov):
    """Eq 105: risk contributions RC_i = w_i (Σw)_i / σ_p (they sum to σ_p)."""
    sw = matvec(cov, w)
    sig = math.sqrt(dot(w, sw))
    return [wi * s / sig for wi, s in zip(w, sw)]


def market_beta(asset_returns, market_returns):
    """Eq 106: beta β = Cov(R_i, R_m) / Var(R_m)."""
    n = min(len(asset_returns), len(market_returns))
    a, m = asset_returns[-n:], market_returns[-n:]
    return covariance(a, m) / variance(m)


def capm_expected_return(rf, beta, market_return):
    """Eq 107: CAPM E[R_i] = R_f + β_i (E[R_m] − R_f)."""
    return rf + beta * (market_return - rf)


def jensens_alpha(portfolio_return, rf, beta, market_return):
    """Eq 108: Jensen's alpha α = R_p − [R_f + β (R_m − R_f)]."""
    return portfolio_return - capm_expected_return(rf, beta, market_return)


def parametric_var(mu_loss, sigma_loss, alpha=0.95):
    """Eq 109: parametric (normal) VaR_α = μ_L + z_α σ_L."""
    return mu_loss + norm_ppf(alpha) * sigma_loss


def expected_shortfall(mu_loss, sigma_loss, alpha=0.95):
    """Eq 110: normal expected shortfall ES_α = μ_L + σ_L φ(z_α) / (1 − α)."""
    return mu_loss + sigma_loss * norm_pdf(norm_ppf(alpha)) / (1.0 - alpha)
