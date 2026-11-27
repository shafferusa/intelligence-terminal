"""Stochastic processes (equations 51-62). Random functions take a seed or a random.Random."""
import math
import random as _random


def _rng(seed_or_rng):
    return seed_or_rng if isinstance(seed_or_rng, _random.Random) else _random.Random(seed_or_rng)


def brownian_increment(dt, seed=None):
    """Eq 51: Brownian increment ΔW = √Δt · Z with Z ~ N(0, 1)."""
    return math.sqrt(dt) * _rng(seed).gauss(0.0, 1.0)


def brownian_path(n_steps, dt, seed=None, w0=0.0):
    """Eq 52: Brownian motion path W_{t+Δt} = W_t + √Δt Z (n_steps + 1 points)."""
    rng = _rng(seed)
    s = math.sqrt(dt)
    w = [w0]
    for _ in range(n_steps):
        w.append(w[-1] + s * rng.gauss(0.0, 1.0))
    return w


def gbm_euler_step(s, mu, sigma, dt, z):
    """Eq 53: GBM Euler step S_{t+Δt} = S_t + μ S_t Δt + σ S_t √Δt Z."""
    return s + mu * s * dt + sigma * s * math.sqrt(dt) * z


def gbm_exact(s0, mu, sigma, t, w_t):
    """Eq 54: GBM exact solution S_t = S_0 exp((μ − σ²/2) t + σ W_t)."""
    return s0 * math.exp((mu - 0.5 * sigma * sigma) * t + sigma * w_t)


def gbm_path(s0, mu, sigma, n_steps, dt, seed=None, exact=True):
    """Eq 53/54: simulate a GBM path (exact log-normal steps by default, Euler if exact=False)."""
    rng = _rng(seed)
    out = [s0]
    drift = (mu - 0.5 * sigma * sigma) * dt
    vol = sigma * math.sqrt(dt)
    for _ in range(n_steps):
        z = rng.gauss(0.0, 1.0)
        s = out[-1]
        out.append(s * math.exp(drift + vol * z) if exact else gbm_euler_step(s, mu, sigma, dt, z))
    return out


def ito_lemma(f_t, f_x, f_xx, mu, sigma):
    """Eq 55: Itô's lemma for f(t, X) with dX = μ dt + σ dW.

    Returns (drift, diffusion) of df = (f_t + μ f_x + ½σ² f_xx) dt + σ f_x dW.
    """
    return f_t + mu * f_x + 0.5 * sigma * sigma * f_xx, sigma * f_x


def ou_path(x0, kappa, theta, sigma, n_steps, dt, seed=None):
    """Eq 56: Ornstein-Uhlenbeck dX = κ(θ − X)dt + σ dW, simulated with the exact transition."""
    rng = _rng(seed)
    e = math.exp(-kappa * dt)
    sd = sigma * math.sqrt((1.0 - e * e) / (2.0 * kappa)) if kappa > 0 else sigma * math.sqrt(dt)
    out = [x0]
    for _ in range(n_steps):
        out.append(theta + (out[-1] - theta) * e + sd * rng.gauss(0.0, 1.0))
    return out


def ou_expected(x0, kappa, theta, t):
    """Eq 57: OU expected value E[X_t] = θ + (X_0 − θ) e^{−κt}."""
    return theta + (x0 - theta) * math.exp(-kappa * t)


def half_life(kappa):
    """Eq 58: mean-reversion half-life ln 2 / κ."""
    return math.log(2.0) / kappa


def ar1_to_kappa(phi, dt=1.0):
    """Convert an AR(1) coefficient φ sampled every Δt into κ = −ln φ / Δt (needs 0 < φ < 1)."""
    if not 0.0 < phi < 1.0:
        raise ValueError("phi must be in (0, 1)")
    return -math.log(phi) / dt


def vasicek_path(r0, a, b, sigma, n_steps, dt, seed=None):
    """Eq 59: Vasicek short rate dr = a(b − r)dt + σ dW (exact OU transition)."""
    return ou_path(r0, a, b, sigma, n_steps, dt, seed)


def vasicek_zcb_price(r0, a, b, sigma, T):
    """Eq 59 closed form: Vasicek zero-coupon bond price P(0,T) = A(T) e^{−B(T) r_0}."""
    B = (1.0 - math.exp(-a * T)) / a
    A = math.exp((b - sigma * sigma / (2 * a * a)) * (B - T) - sigma * sigma * B * B / (4 * a))
    return A * math.exp(-B * r0)


def cir_path(r0, kappa, theta, sigma, n_steps, dt, seed=None):
    """Eq 60: CIR dr = κ(θ − r)dt + σ√r dW with full-truncation Euler."""
    rng = _rng(seed)
    sq = math.sqrt(dt)
    out = [r0]
    for _ in range(n_steps):
        rp = max(out[-1], 0.0)
        out.append(out[-1] + kappa * (theta - rp) * dt + sigma * math.sqrt(rp) * sq * rng.gauss(0.0, 1.0))
    return out


def heston_paths(s0, v0, mu, kappa, theta, xi, rho, n_steps, dt, seed=None):
    """Eqs 61-62: Heston model with correlated shocks (corr ρ), full-truncation Euler.

    dS = μS dt + √v S dW₁,  dv = κ(θ − v)dt + ξ√v dW₂.  Returns (prices, variances).
    """
    rng = _rng(seed)
    sq = math.sqrt(dt)
    c = math.sqrt(max(0.0, 1.0 - rho * rho))
    s, v = [s0], [v0]
    for _ in range(n_steps):
        z1 = rng.gauss(0.0, 1.0)
        z2 = rho * z1 + c * rng.gauss(0.0, 1.0)
        vp = max(v[-1], 0.0)
        s.append(s[-1] * math.exp((mu - 0.5 * vp) * dt + math.sqrt(vp) * sq * z1))
        v.append(v[-1] + kappa * (theta - vp) * dt + xi * math.sqrt(vp) * sq * z2)
    return s, v


def heston_price_path(s0, v0, mu, kappa, theta, xi, rho, n_steps, dt, seed=None):
    """Eq 61: Heston price path dS_t = μS_t dt + √v_t S_t dW₁."""
    return heston_paths(s0, v0, mu, kappa, theta, xi, rho, n_steps, dt, seed)[0]


def heston_variance_path(s0, v0, mu, kappa, theta, xi, rho, n_steps, dt, seed=None):
    """Eq 62: Heston variance path dv_t = κ(θ − v_t)dt + ξ√v_t dW₂."""
    return heston_paths(s0, v0, mu, kappa, theta, xi, rho, n_steps, dt, seed)[1]
