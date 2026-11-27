"""Returns, probability and descriptive statistics (equations 1-20) plus the normal distribution."""
import math

SQRT2 = math.sqrt(2.0)
INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)


# ---------------------------------------------------------------- normal distribution

def norm_pdf(x):
    """Standard normal density φ(x)."""
    return INV_SQRT_2PI * math.exp(-0.5 * x * x)


def norm_cdf(x):
    """Standard normal cumulative distribution Φ(x)."""
    return 0.5 * math.erfc(-x / SQRT2)


_A = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
      1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
_B = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
      6.680131188771972e+01, -1.328068155288572e+01)
_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
      -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
      3.754408661907416e+00)


def norm_ppf(p):
    """Inverse standard normal cdf (Acklam's rational approximation refined by one Halley step)."""
    if not 0.0 < p < 1.0:
        if p == 0.0:
            return -math.inf
        if p == 1.0:
            return math.inf
        raise ValueError("p must be in (0, 1)")
    plow = 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
            ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
    elif p <= 1.0 - plow:
        q = p - 0.5
        r = q * q
        x = (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / \
            (((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
            ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
    e = norm_cdf(x) - p
    u = e * math.sqrt(2.0 * math.pi) * math.exp(0.5 * x * x)
    return x - u / (1.0 + 0.5 * x * u)


# ---------------------------------------------------------------- 1-5 returns

def simple_return(p_t, p_prev, dividend=0.0):
    """Eq 1: one-period simple (total) return (P_t − P_{t−1} + D_t) / P_{t−1}."""
    return (p_t - p_prev + dividend) / p_prev


def simple_returns(prices, dividends=None):
    """Eq 1 applied to a price series (dividends aligned with prices, optional)."""
    if dividends is None:
        return [prices[i] / prices[i - 1] - 1.0 for i in range(1, len(prices))]
    return [simple_return(prices[i], prices[i - 1], dividends[i] or 0.0) for i in range(1, len(prices))]


def log_return(p_t, p_prev):
    """Eq 2: log return ln(P_t / P_{t−1})."""
    return math.log(p_t / p_prev)


def log_returns(prices):
    """Eq 2 applied to a price series."""
    return [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]


def cumulative_return(returns):
    """Eq 3: cumulative simple return ∏(1 + R_t) − 1."""
    g = 1.0
    for r in returns:
        g *= 1.0 + r
    return g - 1.0


def cumulative_log_return(log_rets):
    """Eq 4: cumulative log return Σ r_t."""
    return math.fsum(log_rets)


def annualized_return(returns, periods_per_year=252):
    """Eq 5: annualised return [∏(1 + R_t)]^{A/n} − 1."""
    n = len(returns)
    if n == 0:
        raise ValueError("need at least one return")
    g = cumulative_return(returns) + 1.0
    if g <= 0:
        return -1.0
    return g ** (periods_per_year / n) - 1.0


# ---------------------------------------------------------------- 6-12 moments

def mean(x):
    """Eq 6: arithmetic mean (1/N) Σ x_i."""
    return math.fsum(x) / len(x)


def geometric_mean_return(returns):
    """Eq 7: geometric mean return [∏(1 + R_t)]^{1/n} − 1."""
    g = cumulative_return(returns) + 1.0
    if g <= 0:
        return -1.0
    return g ** (1.0 / len(returns)) - 1.0


def variance(x, ddof=1):
    """Eq 8: sample variance Σ(x − x̄)² / (N − 1)."""
    n = len(x)
    if n - ddof <= 0:
        raise ValueError("not enough observations")
    m = mean(x)
    return math.fsum((v - m) ** 2 for v in x) / (n - ddof)


def stdev(x, ddof=1):
    """Eq 9: standard deviation √variance."""
    return math.sqrt(variance(x, ddof))


def covariance(x, y, ddof=1):
    """Eq 10: sample covariance Σ(x − x̄)(y − ȳ) / (N − 1)."""
    n = min(len(x), len(y))
    x, y = x[:n], y[:n]
    mx, my = mean(x), mean(y)
    return math.fsum((a - mx) * (b - my) for a, b in zip(x, y)) / (n - ddof)


def correlation(x, y):
    """Eq 11: Pearson correlation cov(x, y) / (σ_x σ_y)."""
    return covariance(x, y) / math.sqrt(variance(x) * variance(y))


def zscore(x, mu, sigma):
    """Eq 12: z-score (x − μ) / σ."""
    return (x - mu) / sigma


# ---------------------------------------------------------------- 13-15 probability

def expected_value(values, probs):
    """Eq 13: discrete expectation Σ p_i x_i."""
    return math.fsum(p * v for p, v in zip(probs, values))


def expected_value_pdf(grid, pdf, g=None):
    """Eq 13: continuous expectation ∫ g(x) f(x) dx by the trapezoid rule on a grid.

    `pdf` is either a callable or a list of density values on the grid; g defaults to identity.
    """
    fx = [pdf(x) for x in grid] if callable(pdf) else list(pdf)
    gx = [g(x) if g else x for x in grid]
    total = 0.0
    for i in range(1, len(grid)):
        h = grid[i] - grid[i - 1]
        total += 0.5 * h * (gx[i] * fx[i] + gx[i - 1] * fx[i - 1])
    return total


def conditional_expectation(x, y, bins=None):
    """Eq 14: E[X | Y] as the mean of X within each value (or quantile bin) of Y.

    Returns {y_value_or_bin_index: mean_of_x}. With `bins`, Y is split into equal-count bins.
    """
    groups = {}
    if bins:
        order = sorted(range(len(y)), key=lambda i: y[i])
        n = len(order)
        for rank, i in enumerate(order):
            groups.setdefault(min(bins - 1, rank * bins // n), []).append(x[i])
    else:
        for xi, yi in zip(x, y):
            groups.setdefault(yi, []).append(xi)
    return {k: mean(v) for k, v in sorted(groups.items())}


def bayes(p_b_given_a, p_a, p_b):
    """Eq 15: Bayes' theorem P(A|B) = P(B|A) P(A) / P(B)."""
    return p_b_given_a * p_a / p_b


def bayes_posterior(priors, likelihoods):
    """Eq 15 over a set of hypotheses: posterior ∝ likelihood × prior, normalised."""
    joint = [p * l for p, l in zip(priors, likelihoods)]
    z = math.fsum(joint)
    return [j / z for j in joint]


# ---------------------------------------------------------------- 16-20 shape and risk-adjusted return

def skewness(x):
    """Eq 16: sample skewness m₃ / m₂^{3/2} (moment estimator)."""
    m = mean(x)
    n = len(x)
    m2 = math.fsum((v - m) ** 2 for v in x) / n
    m3 = math.fsum((v - m) ** 3 for v in x) / n
    return m3 / m2 ** 1.5


def excess_kurtosis(x):
    """Eq 17: excess kurtosis m₄ / m₂² − 3 (zero for a normal distribution)."""
    m = mean(x)
    n = len(x)
    m2 = math.fsum((v - m) ** 2 for v in x) / n
    m4 = math.fsum((v - m) ** 4 for v in x) / n
    return m4 / (m2 * m2) - 3.0


def standard_error(x):
    """Eq 18: standard error of the mean σ / √N."""
    return stdev(x) / math.sqrt(len(x))


def sharpe_ratio(returns, rf=0.0, periods_per_year=None):
    """Eq 19: Sharpe ratio (E[R] − R_f) / σ. rf is per period; annualise by √A if periods_per_year given."""
    s = (mean(returns) - rf) / stdev(returns)
    return s * math.sqrt(periods_per_year) if periods_per_year else s


def downside_deviation(returns, target=0.0):
    """Root-mean-square of returns below the target."""
    return math.sqrt(math.fsum(min(0.0, r - target) ** 2 for r in returns) / len(returns))


def sortino_ratio(returns, target=0.0, periods_per_year=None):
    """Eq 20: Sortino ratio (E[R] − T) / downside deviation. Annualise by √A if periods_per_year given."""
    s = (mean(returns) - target) / downside_deviation(returns, target)
    return s * math.sqrt(periods_per_year) if periods_per_year else s
