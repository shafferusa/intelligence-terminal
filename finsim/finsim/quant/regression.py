"""Regression and econometrics (equations 21-33). Design matrices are lists of rows."""
import math

from .linalg import gram, inverse, solve, xty


def add_constant(X):
    """Prepend a column of ones to a design matrix (or a plain list of values)."""
    return [[1.0] + (list(r) if isinstance(r, (list, tuple)) else [r]) for r in X]


def linear_model(X, beta, errors=None):
    """Eq 21: linear regression model y = Xβ + ε."""
    y = [sum(b * v for b, v in zip(beta, row)) for row in X]
    if errors is not None:
        y = [a + e for a, e in zip(y, errors)]
    return y


def ols(X, y):
    """Eq 22: ordinary least squares β̂ = (XᵀX)⁻¹Xᵀy."""
    return solve(gram(X), xty(X, y))


def fitted_values(X, beta):
    """Eq 23: fitted values ŷ = Xβ̂."""
    return linear_model(X, beta)


def residuals(y, yhat):
    """Eq 24: residuals e = y − ŷ."""
    return [a - b for a, b in zip(y, yhat)]


def sse(y, yhat):
    """Eq 25: sum of squared errors Σ(y − ŷ)²."""
    return math.fsum((a - b) ** 2 for a, b in zip(y, yhat))


def mse(y, yhat):
    """Eq 26: mean squared error SSE / n."""
    return sse(y, yhat) / len(y)


def rmse(y, yhat):
    """Eq 27: root mean squared error √MSE."""
    return math.sqrt(mse(y, yhat))


def mae(y, yhat):
    """Eq 28: mean absolute error (1/n) Σ|y − ŷ|."""
    return math.fsum(abs(a - b) for a, b in zip(y, yhat)) / len(y)


def r_squared(y, yhat):
    """Eq 29: coefficient of determination 1 − SSE / SST."""
    m = math.fsum(y) / len(y)
    sst = math.fsum((a - m) ** 2 for a in y)
    return 1.0 - sse(y, yhat) / sst


def adjusted_r_squared(r2, n, k):
    """Eq 30: adjusted R² = 1 − (1 − R²)(n − 1)/(n − k − 1), k regressors excluding the constant."""
    return 1.0 - (1.0 - r2) * (n - 1) / (n - k - 1)


def coef_standard_errors(X, y, beta):
    """Standard errors √diag(σ̂²(XᵀX)⁻¹) with σ̂² = SSE / (n − k)."""
    n, k = len(X), len(X[0])
    s2 = sse(y, fitted_values(X, beta)) / (n - k)
    inv = inverse(gram(X))
    return [math.sqrt(max(0.0, s2 * inv[i][i])) for i in range(k)]


def t_statistic(beta_j, se_j, beta0=0.0):
    """Eq 31: t-statistic (β̂_j − β_j⁰) / SE(β̂_j)."""
    return (beta_j - beta0) / se_j


def coef_t_stats(X, y, beta=None):
    """Eq 31 for every coefficient of an OLS fit."""
    beta = beta if beta is not None else ols(X, y)
    return [t_statistic(b, s) for b, s in zip(beta, coef_standard_errors(X, y, beta))]


def ols_fit(X, y):
    """Full OLS summary: beta, standard errors, t-stats, fitted, residuals, R², adjusted R²."""
    beta = ols(X, y)
    yhat = fitted_values(X, beta)
    se = coef_standard_errors(X, y, beta)
    r2 = r_squared(y, yhat)
    n, k = len(X), len(X[0])
    return {
        "beta": beta, "se": se, "t": [b / s if s > 0 else math.nan for b, s in zip(beta, se)],
        "fitted": yhat, "residuals": residuals(y, yhat), "sse": sse(y, yhat),
        "r2": r2, "adj_r2": adjusted_r_squared(r2, n, k - 1) if n - k > 0 else math.nan,
    }


def log_linear_regression(X, y):
    """Eq 32: log-linear model ln y = Xβ + ε, fitted by OLS on ln y (y must be positive)."""
    return ols(X, [math.log(v) for v in y])


def add_interaction(X, i, j):
    """Append the interaction column x_i·x_j to each row."""
    return [list(r) + [r[i] * r[j]] for r in X]


def interaction_model(x1, x2, y):
    """Eq 33: y = β₀ + β₁x₁ + β₂x₂ + β₃x₁x₂ + ε, fitted by OLS. Returns [β₀, β₁, β₂, β₃]."""
    X = [[1.0, a, b, a * b] for a, b in zip(x1, x2)]
    return ols(X, y)


def simple_regression(x, y):
    """One-regressor OLS y = a + b x. Returns (a, b, r2)."""
    n = len(x)
    mx, my = math.fsum(x) / n, math.fsum(y) / n
    sxx = math.fsum((v - mx) ** 2 for v in x)
    sxy = math.fsum((u - mx) * (v - my) for u, v in zip(x, y))
    syy = math.fsum((v - my) ** 2 for v in y)
    b = sxy / sxx
    a = my - b * mx
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else math.nan
    return a, b, r2
