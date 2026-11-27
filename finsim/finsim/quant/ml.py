"""Machine learning (equations 63-83). Feature matrices are lists of rows."""
import math
import random as _random

from .linalg import eigh_sym, solve


def _rng(seed_or_rng):
    return seed_or_rng if isinstance(seed_or_rng, _random.Random) else _random.Random(seed_or_rng)


# ---------------------------------------------------------------- 63-66 generic learning set-up

def model_predict(f, theta, X):
    """Eq 63: evaluate a parametric model ŷ = f_θ(x) on each row of X; f is called as f(x, θ)."""
    return [f(x, theta) for x in X]


def squared_loss(y, yhat):
    """Squared-error loss (y − ŷ)²."""
    return (y - yhat) ** 2


def empirical_risk(y, yhat, loss=squared_loss):
    """Eq 64: empirical risk (1/n) Σ ℓ(y_i, ŷ_i)."""
    return math.fsum(loss(a, b) for a, b in zip(y, yhat)) / len(y)


def regularized_objective(y, yhat, theta, lam, loss=squared_loss, penalty="l2"):
    """Eq 65: regularised objective (1/n) Σ ℓ(y_i, ŷ_i) + λ Ω(θ), with Ω = ‖θ‖²₂ ("l2") or ‖θ‖₁ ("l1")."""
    omega = math.fsum(abs(t) for t in theta) if penalty == "l1" else math.fsum(t * t for t in theta)
    return empirical_risk(y, yhat, loss) + lam * omega


def linear_predictor(x, w, b=0.0):
    """Eq 66: linear predictor ŷ = wᵀx + b."""
    return sum(wi * xi for wi, xi in zip(w, x)) + b


# ---------------------------------------------------------------- 67-72 classification

def sigmoid(z):
    """Eq 67: logistic sigmoid 1 / (1 + e^{−z}), numerically stable for large |z|."""
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def logistic_predict_proba(x, w, b=0.0):
    """Eq 68: logistic regression probability P(y = 1 | x) = σ(wᵀx + b)."""
    return sigmoid(linear_predictor(x, w, b))


def logistic_fit(X, y, lr=0.1, epochs=2000, l2=0.0):
    """Eq 68: fit logistic regression by full-batch gradient descent on mean cross-entropy. Returns (w, b)."""
    n, k = len(X), len(X[0])
    w, b = [0.0] * k, 0.0
    for _ in range(epochs):
        gw, gb = [0.0] * k, 0.0
        for x, t in zip(X, y):
            err = logistic_predict_proba(x, w, b) - t
            gb += err
            for j in range(k):
                gw[j] += err * x[j]
        w = [w[j] - lr * (gw[j] / n + l2 * w[j]) for j in range(k)]
        b -= lr * gb / n
    return w, b


def binary_cross_entropy(y, p, eps=1e-12):
    """Eq 69: mean binary cross-entropy −(1/n) Σ [y ln p + (1 − y) ln(1 − p)]."""
    s = 0.0
    for t, q in zip(y, p):
        q = min(max(q, eps), 1.0 - eps)
        s -= t * math.log(q) + (1.0 - t) * math.log(1.0 - q)
    return s / len(y)


def softmax(z):
    """Eq 70: softmax e^{z_k} / Σ_j e^{z_j} (shifted by max z for stability)."""
    m = max(z)
    e = [math.exp(v - m) for v in z]
    s = sum(e)
    return [v / s for v in e]


def cross_entropy(y_onehot, p, eps=1e-12):
    """Eq 71: multiclass cross-entropy −Σ_k y_k ln p_k for one observation."""
    return -math.fsum(t * math.log(max(q, eps)) for t, q in zip(y_onehot, p))


def hinge_loss(y, score):
    """Eq 72: hinge loss max(0, 1 − y·f(x)) with labels y ∈ {−1, +1}."""
    return max(0.0, 1.0 - y * score)


# ---------------------------------------------------------------- 73-75 penalised regression

def _center(X, y):
    n, k = len(X), len(X[0])
    mx = [sum(r[j] for r in X) / n for j in range(k)]
    my = sum(y) / n
    Xc = [[r[j] - mx[j] for j in range(k)] for r in X]
    return Xc, [v - my for v in y], mx, my


def ridge(X, y, lam=1.0, fit_intercept=True):
    """Eq 73: ridge regression β̂ = (XᵀX + λI)⁻¹Xᵀy (intercept not penalised). Returns (intercept, coefs)."""
    k = len(X[0])
    if fit_intercept:
        Xc, yc, mx, my = _center(X, y)
    else:
        Xc, yc, mx, my = X, y, [0.0] * k, 0.0
    A = [[sum(r[i] * r[j] for r in Xc) + (lam if i == j else 0.0) for j in range(k)] for i in range(k)]
    b = [sum(r[i] * v for r, v in zip(Xc, yc)) for i in range(k)]
    beta = solve(A, b)
    return my - sum(m * c for m, c in zip(mx, beta)), beta


def soft_threshold(z, g):
    """Soft-thresholding operator S(z, γ) = sign(z) · max(|z| − γ, 0)."""
    if z > g:
        return z - g
    if z < -g:
        return z + g
    return 0.0


def elastic_net(X, y, lam=0.1, l1_ratio=0.5, max_iter=1000, tol=1e-8):
    """Eq 75: elastic net by coordinate descent, minimising
    (1/2n)‖y − Xβ‖² + λ[α‖β‖₁ + (1 − α)/2 ‖β‖²₂]. Returns (intercept, coefs)."""
    Xc, yc, mx, my = _center(X, y)
    n, k = len(Xc), len(Xc[0])
    cols = [[r[j] for r in Xc] for j in range(k)]
    z = [sum(v * v for v in c) / n for c in cols]
    beta = [0.0] * k
    resid = list(yc)
    for _ in range(max_iter):
        delta = 0.0
        for j in range(k):
            if z[j] == 0:
                continue
            cj = cols[j]
            old = beta[j]
            rho = sum(c * r for c, r in zip(cj, resid)) / n + z[j] * old
            new = soft_threshold(rho, lam * l1_ratio) / (z[j] + lam * (1.0 - l1_ratio))
            if new != old:
                d = new - old
                resid = [r - d * c for r, c in zip(resid, cj)]
                beta[j] = new
                delta = max(delta, abs(d))
        if delta < tol:
            break
    return my - sum(m * c for m, c in zip(mx, beta)), beta


def lasso(X, y, lam=0.1, max_iter=1000, tol=1e-8):
    """Eq 74: LASSO by coordinate descent with soft-thresholding, minimising
    (1/2n)‖y − Xβ‖² + λ‖β‖₁. Returns (intercept, coefs)."""
    return elastic_net(X, y, lam, 1.0, max_iter, tol)


# ---------------------------------------------------------------- 76-80 trees and ensembles

def tree_leaf_value(y):
    """Eq 76: a regression-tree leaf predicts the mean of its training targets."""
    return sum(y) / len(y)


def best_split(X, y, min_leaf=1, features=None):
    """Eq 77: best single split minimising SSE_left + SSE_right.

    Returns (feature, threshold, sse) or None if no valid split exists.
    """
    n = len(y)
    features = range(len(X[0])) if features is None else features
    best = None
    for j in features:
        order = sorted(range(n), key=lambda i: X[i][j])
        tot_s = sum(y)
        tot_q = sum(v * v for v in y)
        s = q = 0.0
        for pos in range(n - 1):
            i = order[pos]
            s += y[i]
            q += y[i] * y[i]
            nl = pos + 1
            nr = n - nl
            if nl < min_leaf or nr < min_leaf:
                continue
            a, b = X[i][j], X[order[pos + 1]][j]
            if a == b:
                continue
            sse = (q - s * s / nl) + ((tot_q - q) - (tot_s - s) ** 2 / nr)
            if best is None or sse < best[2] - 1e-12:
                best = (j, 0.5 * (a + b), sse)
    return best


def fit_tree(X, y, max_depth=3, min_leaf=1, max_features=None, seed=None):
    """Grow a regression tree by recursive best splits. Nodes are dicts ({"leaf": v} or a split)."""
    rng = _rng(seed) if max_features else None
    k = len(X[0])

    def grow(idx, depth):
        ys = [y[i] for i in idx]
        if depth >= max_depth or len(idx) < 2 * min_leaf or max(ys) == min(ys):
            return {"leaf": tree_leaf_value(ys)}
        feats = rng.sample(range(k), min(k, max_features)) if rng else None
        sp = best_split([X[i] for i in idx], ys, min_leaf, feats)
        if sp is None:
            return {"leaf": tree_leaf_value(ys)}
        j, thr, _ = sp
        left = [i for i in idx if X[i][j] <= thr]
        right = [i for i in idx if X[i][j] > thr]
        return {"feature": j, "threshold": thr, "left": grow(left, depth + 1), "right": grow(right, depth + 1)}

    return grow(list(range(len(y))), 0)


def tree_predict(tree, x):
    """Route x down a fitted tree and return its leaf value."""
    while "leaf" not in tree:
        tree = tree["left"] if x[tree["feature"]] <= tree["threshold"] else tree["right"]
    return tree["leaf"]


def tree_leaves(tree):
    """List of leaf values of a tree."""
    if "leaf" in tree:
        return [tree["leaf"]]
    return tree_leaves(tree["left"]) + tree_leaves(tree["right"])


def random_forest_fit(X, y, n_trees=20, max_depth=4, min_leaf=2, max_features=None, seed=0):
    """Eq 78: random forest — bootstrap samples, random feature subsets, averaged small trees."""
    rng = _rng(seed)
    n, k = len(y), len(X[0])
    mf = max_features or max(1, int(round(math.sqrt(k))))
    forest = []
    for _ in range(n_trees):
        idx = [rng.randrange(n) for _ in range(n)]
        forest.append(fit_tree([X[i] for i in idx], [y[i] for i in idx], max_depth, min_leaf, mf,
                               rng.randrange(2 ** 31)))
    return forest


def random_forest_predict(forest, x):
    """Eq 78: forest prediction (1/B) Σ_b T_b(x)."""
    return sum(tree_predict(t, x) for t in forest) / len(forest)


def gradient_boosting_fit(X, y, n_estimators=100, learning_rate=0.1, max_depth=1, min_leaf=1):
    """Eq 79: gradient boosting with squared loss, F_m = F_{m−1} + η h_m where h_m fits the residuals."""
    f0 = sum(y) / len(y)
    pred = [f0] * len(y)
    trees = []
    for _ in range(n_estimators):
        resid = [a - b for a, b in zip(y, pred)]
        t = fit_tree(X, resid, max_depth, min_leaf)
        trees.append(t)
        pred = [p + learning_rate * tree_predict(t, x) for p, x in zip(pred, X)]
    return {"init": f0, "learning_rate": learning_rate, "trees": trees}


def gradient_boosting_predict(model, x):
    """Eq 79: boosted prediction F_0 + η Σ h_m(x)."""
    return model["init"] + model["learning_rate"] * sum(tree_predict(t, x) for t in model["trees"])


def xgb_objective(y, yhat, trees, gamma=0.0, lam=1.0, loss=squared_loss):
    """Eq 80: XGBoost-style objective Σ ℓ(y_i, ŷ_i) + Σ_k (γ T_k + ½ λ ‖w_k‖²).

    `trees` is a list of fitted trees or of leaf-weight lists.
    """
    data = math.fsum(loss(a, b) for a, b in zip(y, yhat))
    reg = 0.0
    for t in trees:
        w = tree_leaves(t) if isinstance(t, dict) else list(t)
        reg += gamma * len(w) + 0.5 * lam * sum(v * v for v in w)
    return data + reg


def xgb_leaf_weight(grad_sum, hess_sum, lam=1.0):
    """Optimal XGBoost leaf weight w* = −G / (H + λ)."""
    return -grad_sum / (hess_sum + lam)


# ---------------------------------------------------------------- 81-83 unsupervised

def _sqdist(a, b):
    return sum((u - v) ** 2 for u, v in zip(a, b))


def kmeans(X, k, seed=0, max_iter=100):
    """Eq 81: k-means (Lloyd's algorithm) with k-means++ seeding.

    Minimises Σ ‖x_i − μ_{c(i)}‖². Returns (centroids, labels, inertia).
    """
    rng = _rng(seed)
    n = len(X)
    cents = [list(X[rng.randrange(n)])]
    while len(cents) < k:
        d = [min(_sqdist(x, c) for c in cents) for x in X]
        tot = sum(d)
        if tot == 0:
            cents.append(list(X[rng.randrange(n)]))
            continue
        u, acc = rng.random() * tot, 0.0
        for x, di in zip(X, d):
            acc += di
            if acc >= u:
                cents.append(list(x))
                break
    labels = [0] * n
    for it in range(max_iter):
        new = [min(range(k), key=lambda c: _sqdist(x, cents[c])) for x in X]
        if new == labels and it > 0:
            break
        labels = new
        for c in range(k):
            members = [X[i] for i in range(n) if labels[i] == c]
            if members:
                cents[c] = [sum(col) / len(members) for col in zip(*members)]
    inertia = sum(_sqdist(x, cents[l]) for x, l in zip(X, labels))
    return cents, labels, inertia


def covariance_matrix(X):
    """Sample covariance matrix of the columns of X."""
    n, k = len(X), len(X[0])
    m = [sum(r[j] for r in X) / n for j in range(k)]
    return [[sum((r[i] - m[i]) * (r[j] - m[j]) for r in X) / (n - 1) for j in range(k)] for i in range(k)]


def pca(cov):
    """Eq 82: PCA via eigen-decomposition Σ = VΛVᵀ. Returns (eigenvalues desc, eigenvectors, explained ratios)."""
    vals, vecs = eigh_sym(cov)
    tot = sum(vals)
    return vals, vecs, [v / tot if tot else 0.0 for v in vals]


def pca_project(X, components, mean=None):
    """Eq 83: project centred data onto principal components, z = Vᵀ(x − μ)."""
    k = len(X[0])
    if mean is None:
        mean = [sum(r[j] for r in X) / len(X) for j in range(k)]
    return [[sum(v[j] * (r[j] - mean[j]) for j in range(k)) for v in components] for r in X]
