"""
ShafferFinEval -- pure-stdlib machine learning.

No numpy, no scikit-learn: this environment has neither, and the rest of the
project is deliberately dependency-light. Everything here is implemented
directly and is deterministic, so a model trained twice on the same rows gives
the same coefficients.

Models: OLS, Ridge, Lasso, Elastic Net (coordinate descent) and gradient-boosted
regression trees, plus a random forest. All share one interface:

    model.fit(X, y) -> self
    model.predict(X) -> list[float]
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional, Sequence

Matrix = Sequence[Sequence[float]]
Vector = Sequence[float]


# --------------------------------------------------------------------------
# Linear algebra
# --------------------------------------------------------------------------

def _transpose(matrix: Matrix) -> list[list[float]]:
    return [list(col) for col in zip(*matrix)] if matrix else []


def _matmul(a: Matrix, b: Matrix) -> list[list[float]]:
    b_t = _transpose(b)
    return [[sum(x * y for x, y in zip(row, col)) for col in b_t] for row in a]


def _matvec(a: Matrix, v: Vector) -> list[float]:
    return [sum(x * y for x, y in zip(row, v)) for row in a]


def solve(a: Matrix, b: Vector) -> Optional[list[float]]:
    """Solve a x = b by Gauss-Jordan with partial pivoting. None if singular."""
    n = len(b)
    if n == 0 or any(len(row) != n for row in a):
        return None
    m = [list(row) + [b[i]] for i, row in enumerate(a)]

    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        divisor = m[col][col]
        m[col] = [value / divisor for value in m[col]]
        for row in range(n):
            if row == col:
                continue
            factor = m[row][col]
            if factor:
                m[row] = [v - factor * p for v, p in zip(m[row], m[col])]
    return [row[n] for row in m]


def standardize(X: Matrix) -> tuple[list[list[float]], list[float], list[float]]:
    """Centre and scale columns. Returns (Z, means, stds). Zero-variance -> 1."""
    if not X:
        return [], [], []
    n_features = len(X[0])
    means, stds = [], []
    for j in range(n_features):
        column = [row[j] for row in X]
        mean = sum(column) / len(column)
        variance = sum((v - mean) ** 2 for v in column) / max(len(column) - 1, 1)
        std = math.sqrt(variance) or 1.0
        means.append(mean)
        stds.append(std)
    Z = [[(row[j] - means[j]) / stds[j] for j in range(n_features)] for row in X]
    return Z, means, stds


def apply_standardization(X: Matrix, means: Vector, stds: Vector) -> list[list[float]]:
    return [[(row[j] - means[j]) / stds[j] for j in range(len(means))] for row in X]


# --------------------------------------------------------------------------
# Linear family
# --------------------------------------------------------------------------

@dataclass
class LinearModel:
    """OLS / Ridge. `alpha` = 0 is plain least squares.

    Fitting happens on standardized features so the penalty is scale-free and
    the coefficients are directly comparable; they are then mapped back to the
    original units.
    """

    alpha: float = 0.0
    name: str = "linear"
    intercept: float = 0.0
    coefficients: list[float] = field(default_factory=list)
    std_coefficients: list[float] = field(default_factory=list)
    feature_names: list[str] = field(default_factory=list)
    fitted: bool = False

    def fit(self, X: Matrix, y: Vector, feature_names: Optional[Sequence[str]] = None):
        if not X or not y or len(X) != len(y):
            return self
        Z, means, stds = standardize(X)
        n_features = len(Z[0])
        y_mean = sum(y) / len(y)
        y_centred = [value - y_mean for value in y]

        Zt = _transpose(Z)
        gram = _matmul(Zt, Z)
        for i in range(n_features):
            gram[i][i] += self.alpha
        rhs = _matvec(Zt, y_centred)

        beta = solve(gram, rhs)
        if beta is None:
            # Singular even with the ridge term: nudge the diagonal.
            for i in range(n_features):
                gram[i][i] += 1e-6
            beta = solve(gram, rhs)
        if beta is None:
            return self

        self.std_coefficients = beta
        self.coefficients = [b / s for b, s in zip(beta, stds)]
        self.intercept = y_mean - sum(
            b * m for b, m in zip(self.coefficients, means)
        )
        self.feature_names = list(feature_names or [f"x{i}" for i in range(n_features)])
        self.fitted = True
        return self

    def predict(self, X: Matrix) -> list[float]:
        if not self.fitted:
            return [0.0] * len(X)
        return [
            self.intercept + sum(c * v for c, v in zip(self.coefficients, row))
            for row in X
        ]

    def importance(self) -> list[tuple[str, float]]:
        """Standardized coefficient magnitude, normalised to shares of 1."""
        if not self.fitted:
            return []
        total = sum(abs(c) for c in self.std_coefficients) or 1.0
        pairs = list(zip(self.feature_names, (abs(c) / total for c in self.std_coefficients)))
        return sorted(pairs, key=lambda p: p[1], reverse=True)


@dataclass
class ElasticNetModel:
    """Lasso / Elastic Net by coordinate descent on standardized features.

    l1_ratio = 1.0 is Lasso, 0.0 is Ridge, in between is Elastic Net.
    """

    alpha: float = 0.1
    l1_ratio: float = 1.0
    max_iter: int = 500
    tol: float = 1e-6
    name: str = "elastic_net"
    intercept: float = 0.0
    coefficients: list[float] = field(default_factory=list)
    std_coefficients: list[float] = field(default_factory=list)
    feature_names: list[str] = field(default_factory=list)
    fitted: bool = False

    def fit(self, X: Matrix, y: Vector, feature_names: Optional[Sequence[str]] = None):
        if not X or not y or len(X) != len(y):
            return self
        Z, means, stds = standardize(X)
        n, p = len(Z), len(Z[0])
        y_mean = sum(y) / len(y)
        r = [value - y_mean for value in y]
        beta = [0.0] * p
        columns = _transpose(Z)
        norms = [sum(v * v for v in col) / n for col in columns]

        l1 = self.alpha * self.l1_ratio
        l2 = self.alpha * (1 - self.l1_ratio)

        for _ in range(self.max_iter):
            max_change = 0.0
            for j in range(p):
                if norms[j] == 0:
                    continue
                column = columns[j]
                # Partial residual excluding feature j.
                rho = sum(column[i] * (r[i] + column[i] * beta[j]) for i in range(n)) / n
                if rho > l1:
                    new = (rho - l1) / (norms[j] + l2)
                elif rho < -l1:
                    new = (rho + l1) / (norms[j] + l2)
                else:
                    new = 0.0
                change = new - beta[j]
                if change:
                    for i in range(n):
                        r[i] -= column[i] * change
                    beta[j] = new
                    max_change = max(max_change, abs(change))
            if max_change < self.tol:
                break

        self.std_coefficients = beta
        self.coefficients = [b / s for b, s in zip(beta, stds)]
        self.intercept = y_mean - sum(c * m for c, m in zip(self.coefficients, means))
        self.feature_names = list(feature_names or [f"x{i}" for i in range(p)])
        self.fitted = True
        return self

    def predict(self, X: Matrix) -> list[float]:
        if not self.fitted:
            return [0.0] * len(X)
        return [
            self.intercept + sum(c * v for c, v in zip(self.coefficients, row))
            for row in X
        ]

    def importance(self) -> list[tuple[str, float]]:
        if not self.fitted:
            return []
        total = sum(abs(c) for c in self.std_coefficients) or 1.0
        pairs = list(zip(self.feature_names, (abs(c) / total for c in self.std_coefficients)))
        return sorted(pairs, key=lambda p: p[1], reverse=True)

    def selected_features(self) -> list[str]:
        return [n for n, c in zip(self.feature_names, self.std_coefficients) if abs(c) > 1e-10]


# --------------------------------------------------------------------------
# Trees
# --------------------------------------------------------------------------

@dataclass
class _Node:
    value: float = 0.0
    feature: Optional[int] = None
    threshold: Optional[float] = None
    left: Optional["_Node"] = None
    right: Optional["_Node"] = None

    @property
    def is_leaf(self) -> bool:
        return self.feature is None


class RegressionTree:
    """CART regression tree, variance-reduction splits."""

    def __init__(self, max_depth: int = 3, min_samples_leaf: int = 5,
                 feature_subset: Optional[int] = None, seed: int = 0):
        self.max_depth = max_depth
        self.min_samples_leaf = min_samples_leaf
        self.feature_subset = feature_subset
        self.rng = random.Random(seed)
        self.root: Optional[_Node] = None
        self.n_features = 0

    def fit(self, X: Matrix, y: Vector):
        self.n_features = len(X[0]) if X else 0
        indices = list(range(len(y)))
        self.root = self._build(X, y, indices, 0)
        return self

    def _build(self, X, y, indices, depth) -> _Node:
        mean = sum(y[i] for i in indices) / len(indices)
        node = _Node(value=mean)
        if depth >= self.max_depth or len(indices) < 2 * self.min_samples_leaf:
            return node

        features = list(range(self.n_features))
        if self.feature_subset and self.feature_subset < self.n_features:
            features = self.rng.sample(features, self.feature_subset)

        best = (0.0, None, None)          # (gain, feature, threshold)
        parent_sse = sum((y[i] - mean) ** 2 for i in indices)

        for feature in features:
            ordered = sorted(indices, key=lambda i: X[i][feature])
            values = [X[i][feature] for i in ordered]
            targets = [y[i] for i in ordered]
            left_sum = left_n = 0.0
            total_sum = sum(targets)
            n = len(ordered)
            for k in range(1, n):
                left_sum += targets[k - 1]
                left_n += 1
                if k < self.min_samples_leaf or n - k < self.min_samples_leaf:
                    continue
                if values[k] == values[k - 1]:
                    continue
                right_sum = total_sum - left_sum
                right_n = n - left_n
                sse = (
                    sum((t - left_sum / left_n) ** 2 for t in targets[:k])
                    + sum((t - right_sum / right_n) ** 2 for t in targets[k:])
                )
                gain = parent_sse - sse
                if gain > best[0]:
                    best = (gain, feature, (values[k] + values[k - 1]) / 2)

        if best[1] is None:
            return node

        node.feature, node.threshold = best[1], best[2]
        left = [i for i in indices if X[i][node.feature] <= node.threshold]
        right = [i for i in indices if X[i][node.feature] > node.threshold]
        if not left or not right:
            node.feature = None
            return node
        node.left = self._build(X, y, left, depth + 1)
        node.right = self._build(X, y, right, depth + 1)
        return node

    def predict_one(self, row: Vector) -> float:
        node = self.root
        while node is not None and not node.is_leaf:
            node = node.left if row[node.feature] <= node.threshold else node.right
        return node.value if node else 0.0

    def predict(self, X: Matrix) -> list[float]:
        return [self.predict_one(row) for row in X]


@dataclass
class GradientBoostingModel:
    """Gradient-boosted regression trees on squared error.

    Captures the interactions a linear model cannot: valuation mattering more
    past a threshold, debt mattering more when profitability is weak, and so on.
    """

    n_estimators: int = 60
    learning_rate: float = 0.08
    max_depth: int = 3
    min_samples_leaf: int = 5
    seed: int = 0
    name: str = "gradient_boosting"
    base: float = 0.0
    trees: list = field(default_factory=list)
    feature_names: list[str] = field(default_factory=list)
    fitted: bool = False

    def fit(self, X: Matrix, y: Vector, feature_names: Optional[Sequence[str]] = None):
        if not X or not y or len(X) != len(y):
            return self
        self.base = sum(y) / len(y)
        residual = [value - self.base for value in y]
        self.trees = []
        for i in range(self.n_estimators):
            tree = RegressionTree(self.max_depth, self.min_samples_leaf, seed=self.seed + i)
            tree.fit(X, residual)
            step = tree.predict(X)
            residual = [r - self.learning_rate * s for r, s in zip(residual, step)]
            self.trees.append(tree)
        self.feature_names = list(feature_names or [f"x{i}" for i in range(len(X[0]))])
        self.fitted = True
        return self

    def predict(self, X: Matrix) -> list[float]:
        if not self.fitted:
            return [self.base] * len(X)
        out = [self.base] * len(X)
        for tree in self.trees:
            for i, value in enumerate(tree.predict(X)):
                out[i] += self.learning_rate * value
        return out


@dataclass
class RandomForestModel:
    """Bagged regression trees with a random feature subset per split."""

    n_estimators: int = 40
    max_depth: int = 6
    min_samples_leaf: int = 3
    seed: int = 0
    name: str = "random_forest"
    trees: list = field(default_factory=list)
    feature_names: list[str] = field(default_factory=list)
    fitted: bool = False

    def fit(self, X: Matrix, y: Vector, feature_names: Optional[Sequence[str]] = None):
        if not X or not y:
            return self
        rng = random.Random(self.seed)
        n = len(X)
        subset = max(1, int(math.sqrt(len(X[0]))))
        self.trees = []
        for i in range(self.n_estimators):
            picks = [rng.randrange(n) for _ in range(n)]
            Xb = [X[p] for p in picks]
            yb = [y[p] for p in picks]
            tree = RegressionTree(self.max_depth, self.min_samples_leaf,
                                  feature_subset=subset, seed=self.seed + i)
            tree.fit(Xb, yb)
            self.trees.append(tree)
        self.feature_names = list(feature_names or [f"x{i}" for i in range(len(X[0]))])
        self.fitted = True
        return self

    def predict(self, X: Matrix) -> list[float]:
        if not self.fitted or not self.trees:
            return [0.0] * len(X)
        totals = [0.0] * len(X)
        for tree in self.trees:
            for i, value in enumerate(tree.predict(X)):
                totals[i] += value
        return [t / len(self.trees) for t in totals]


def permutation_importance(
    model, X: Matrix, y: Vector, feature_names: Sequence[str], seed: int = 0
) -> list[tuple[str, float]]:
    """Drop in accuracy when one feature is shuffled, as shares of 1.

    Model-agnostic and preferred for trees. This measures PREDICTIVE importance,
    not causation.
    """
    if not X or not y:
        return []
    rng = random.Random(seed)
    baseline = mean_absolute_error(y, model.predict(X))
    losses = []
    for j in range(len(X[0])):
        column = [row[j] for row in X]
        shuffled = column[:]
        rng.shuffle(shuffled)
        permuted = [list(row) for row in X]
        for i, value in enumerate(shuffled):
            permuted[i][j] = value
        loss = mean_absolute_error(y, model.predict(permuted)) - baseline
        losses.append(max(loss, 0.0))
    total = sum(losses) or 1.0
    pairs = list(zip(feature_names, (l / total for l in losses)))
    return sorted(pairs, key=lambda p: p[1], reverse=True)


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def mean_absolute_error(y_true: Vector, y_pred: Vector) -> float:
    if not y_true:
        return 0.0
    return sum(abs(a - b) for a, b in zip(y_true, y_pred)) / len(y_true)


def root_mean_squared_error(y_true: Vector, y_pred: Vector) -> float:
    if not y_true:
        return 0.0
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(y_true, y_pred)) / len(y_true))


def r_squared(y_true: Vector, y_pred: Vector) -> Optional[float]:
    if len(y_true) < 2:
        return None
    mean = sum(y_true) / len(y_true)
    ss_tot = sum((a - mean) ** 2 for a in y_true)
    if ss_tot == 0:
        return None
    ss_res = sum((a - b) ** 2 for a, b in zip(y_true, y_pred))
    return 1 - ss_res / ss_tot


def pearson(x: Vector, y: Vector) -> Optional[float]:
    n = len(x)
    if n < 2:
        return None
    mx, my = sum(x) / n, sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    dx = math.sqrt(sum((a - mx) ** 2 for a in x))
    dy = math.sqrt(sum((b - my) ** 2 for b in y))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _ranks(values: Vector) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def spearman(x: Vector, y: Vector) -> Optional[float]:
    """Rank correlation -- the metric that matters most for a ranking model."""
    if len(x) < 2:
        return None
    return pearson(_ranks(x), _ranks(y))


def direction_accuracy(y_true: Vector, y_pred: Vector) -> Optional[float]:
    """Share of observations where the predicted sign matched the realised sign."""
    pairs = [(a, b) for a, b in zip(y_true, y_pred) if a != 0]
    if not pairs:
        return None
    hits = sum(1 for a, b in pairs if (a > 0) == (b > 0))
    return hits / len(pairs)


def quantile_buckets(
    y_true: Vector, y_pred: Vector, n_buckets: int = 10
) -> list[dict]:
    """Sort by prediction, then report the realised average per bucket.

    Returned best-bucket-first. A useful ranking model shows a monotonic decline.
    """
    n = len(y_pred)
    if n < n_buckets:
        n_buckets = max(1, n)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: y_pred[i], reverse=True)
    size = n / n_buckets
    out = []
    for b in range(n_buckets):
        start, end = int(b * size), int((b + 1) * size)
        chunk = order[start:end] or order[start:start + 1]
        actual = [y_true[i] for i in chunk]
        predicted = [y_pred[i] for i in chunk]
        out.append({
            "bucket": b + 1,
            "n": len(chunk),
            "mean_predicted": sum(predicted) / len(predicted),
            "mean_actual": sum(actual) / len(actual),
            "hit_rate": sum(1 for a in actual if a > 0) / len(actual),
        })
    return out


def calibration_buckets(
    y_true: Vector, y_pred: Vector, edges: Optional[Sequence[float]] = None
) -> list[dict]:
    """Realised average return inside fixed predicted-return bands."""
    if edges is None:
        edges = [-1.0, -0.10, -0.05, 0.0, 0.05, 0.10, 1.0]
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        members = [(p, a) for p, a in zip(y_pred, y_true) if lo <= p < hi]
        if not members:
            continue
        out.append({
            "range": f"{lo:+.0%} to {hi:+.0%}",
            "n": len(members),
            "mean_predicted": sum(p for p, _ in members) / len(members),
            "mean_actual": sum(a for _, a in members) / len(members),
        })
    return out


def evaluate(y_true: Vector, y_pred: Vector) -> dict:
    """Every headline metric for one prediction set."""
    return {
        "n": len(y_true),
        "mae": mean_absolute_error(y_true, y_pred),
        "rmse": root_mean_squared_error(y_true, y_pred),
        "r2": r_squared(y_true, y_pred),
        "pearson": pearson(y_true, y_pred),
        "spearman": spearman(y_true, y_pred),
        "direction_accuracy": direction_accuracy(y_true, y_pred),
    }
