"""Fast pure-Python machine-learning models for FinSim2 (standard library only, Python 3.11).

The models are trained thousands of times inside walk-forward loops, so everything is written for
speed in CPython without numpy:

* data is converted to column-major lists once per fit;
* inner loops are pushed into C wherever possible (``map``/``operator``, ``sum``, ``itemgetter``,
  ``itertools.compress``);
* trees bin every feature once (quantile bins) and build per-node histograms either with
  C-level gathers over masked arrays (large nodes) or a tight Python loop over the node's rows
  (small nodes), with the histogram-subtraction trick for sibling nodes;
* fitted tree ensembles are compiled once into a straight-line Python function for prediction.

Common API (every supervised model)::

    m = Model(**params)
    m.fit(X, y, sample_weight=None)      # X: rows of floats (None / NaN / inf = missing)
    m.predict(X) -> list[float]
    m.contributions(x) -> list[float]    # one row; sum(contributions) + m.base_value_ == output
    m.name, m.params, m.feature_importances_, m.base_value_

Classifiers (``Logistic``, ``GradientBoosting(loss="logistic")``) also have
``predict_proba(X)`` (P(y=1)) and ``decision_function(X)`` (log-odds); their ``predict`` returns
0.0/1.0 labels and their ``contributions`` are in log-odds space (they sum, with ``base_value_``,
to ``decision_function``).  Regression trees / forests fitted on 0/1 targets predict a
probability directly; they also expose ``predict_proba`` (the prediction clipped to [0, 1]).

Missing values: a ``Preprocessor`` fitted on the training rows only (median imputation, then for
linear models standardisation with the training mean/std and a clip at +-5).  Rows passed to
``predict`` never contribute statistics.

Penalty conventions (documented because they differ between libraries):

* ``Ridge``: minimises mean_i w_i (y_i - b0 - z_i.b)^2 + alpha * |b|^2 (per-sample alpha, so the
  same alpha means the same shrinkage whatever the sample size; weights normalised to mean 1).
* ``Lasso`` / ``ElasticNet``: glmnet / scikit-learn form
  (1/2) mean_i w_i r_i^2 + alpha * l1_ratio * |b|_1 + (alpha/2) * (1 - l1_ratio) * |b|^2.
  With ``relative_alpha=True`` alpha is multiplied by the training std of y, so it is a threshold
  on |corr(feature, y)| - useful when the target's scale changes with the horizon.
* ``Logistic``: scikit-learn form  (1/2)|b|^2 + C * sum_i w_i logloss_i  (intercept unpenalised).
* ``GradientBoosting``: XGBoost-style second-order objective with ``reg_lambda`` / ``gamma``.
  (XGBoost is not used or required; this is an independent pure-Python implementation.)
"""
from __future__ import annotations

import math
import random
from bisect import bisect_left, bisect_right
from itertools import compress, repeat
from operator import add, itemgetter, mul, not_, sub

__all__ = [
    "Preprocessor", "OLS", "Ridge", "Lasso", "ElasticNet", "Logistic", "DecisionTree",
    "RandomForest", "GradientBoosting", "KMeans", "PCA", "make_model", "MODEL_NAMES",
    "MODEL_LABELS", "permutation_importance", "spearman", "pearson", "rank", "get_state",
    "from_state",
]

_INF = float("inf")
_STATE_FORMAT = "finsim2.models/1"
_STD_FLOOR = 1e-12


# ----------------------------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------------------------

def _finite(v):
    return v is not None and -_INF < v < _INF


def _take(seq, idx):
    """Gather ``seq[i] for i in idx`` at C speed; always returns a tuple."""
    if len(idx) > 1:
        return itemgetter(*idx)(seq)
    if idx:
        return (seq[idx[0]],)
    return ()


def _median(vals):
    s = sorted(vals)
    n = len(s)
    if not n:
        return 0.0
    h = n // 2
    return float(s[h]) if n % 2 else (s[h - 1] + s[h]) * 0.5


def _columns(X, p=None):
    """Rows -> list of column lists (validates a rectangular shape)."""
    if not isinstance(X, list):
        X = list(X)
    if not X:
        return [[] for _ in range(p or 0)]
    lens = set(map(len, X))
    if len(lens) != 1:
        raise ValueError("rows of X have different lengths")
    q = lens.pop()
    if p is not None and q != p:
        raise ValueError(f"X has {q} features; the model was fitted with {p}")
    return [list(c) for c in zip(*X)]


def _check_rows(X, p):
    if not isinstance(X, list):
        X = list(X)
    if X:
        lens = set(map(len, X))
        if len(lens) != 1 or lens.pop() != p:
            raise ValueError(f"every row of X must have {p} features")
    return X


def _prepare_xy(X, y, sample_weight):
    """Validate X / y / weights; drop rows whose target is missing (order preserved)."""
    X = list(X)
    y = list(y)
    if len(X) != len(y):
        raise ValueError(f"X has {len(X)} rows but y has {len(y)}")
    w = None
    if sample_weight is not None:
        w = [float(v) for v in sample_weight]
        if len(w) != len(y):
            raise ValueError("sample_weight must have one weight per row")
    keep = [_finite(v) for v in y]
    if not all(keep):
        X = list(compress(X, keep))
        y = list(compress(y, keep))
        if w is not None:
            w = list(compress(w, keep))
    if not X:
        raise ValueError("no training rows with a finite target")
    y = [float(v) for v in y]
    if w is not None:
        if any((not _finite(v)) or v < 0 for v in w):
            raise ValueError("sample weights must be finite and >= 0")
        if sum(w) <= 0:
            raise ValueError("sample weights sum to zero")
    return X, y, w


def _sigmoid(e):
    if e >= 0:
        return 1.0 / (1.0 + math.exp(-e)) if e < 700 else 1.0
    z = math.exp(e) if e > -700 else 0.0
    return z / (1.0 + z)


def _gram(cols, div):
    """Symmetric Gram matrix sum_i c_j[i] c_k[i] / div (C-level dot products)."""
    p = len(cols)
    G = [[0.0] * p for _ in range(p)]
    for j in range(p):
        cj = cols[j]
        Gj = G[j]
        for k in range(j, p):
            v = sum(map(mul, cj, cols[k])) / div
            Gj[k] = v
            G[k][j] = v
    return G


def _cholesky_solve(A, b):
    """Solve A x = b for symmetric positive (semi-)definite A by Cholesky; tiny pivots are floored."""
    p = len(b)
    L = [[0.0] * p for _ in range(p)]
    scale = max([abs(A[i][i]) for i in range(p)] + [1e-300])
    floor = scale * 1e-14
    for i in range(p):
        Li = L[i]
        Ai = A[i]
        for j in range(i):
            Lj = L[j]
            s = Ai[j] - sum(map(mul, Li[:j], Lj[:j]))
            Li[j] = s / Lj[j]
        s = Ai[i] - sum(map(mul, Li[:i], Li[:i]))
        Li[i] = math.sqrt(s if s > floor else floor)
    # forward: L z = b
    z = [0.0] * p
    for i in range(p):
        Li = L[i]
        z[i] = (b[i] - sum(map(mul, Li[:i], z[:i]))) / Li[i]
    # backward: L^T x = z
    x = [0.0] * p
    for i in range(p - 1, -1, -1):
        s = z[i]
        for k in range(i + 1, p):
            s -= L[k][i] * x[k]
        x[i] = s / L[i][i]
    return x


def _normalise(v):
    tot = math.fsum(abs(a) for a in v)
    if tot > 0:
        return [abs(a) / tot for a in v]
    return [0.0] * len(v)


# ----------------------------------------------------------------------------------------------
# preprocessing
# ----------------------------------------------------------------------------------------------

class Preprocessor:
    """Median imputation (+ optional standardisation and clipping) fitted on training rows only.

    Missing = ``None``, NaN or +-inf.  ``scale=True``: z = (x - mean) / max(std, 1e-12), then
    clipped to +-``clip`` (``clip=None`` disables).  Means/stds are of the imputed training column.
    """

    def __init__(self, scale=True, clip=5.0):
        self.scale = bool(scale)
        self.clip = None if clip is None else float(clip)
        self.medians_ = None
        self.means_ = None
        self.stds_ = None
        self.n_features_ = None

    def fit(self, X):
        self.fit_transform_columns(X)
        return self

    def fit_transform_columns(self, X):
        cols = _columns(X)
        meds, means, stds, out = [], [], [], []
        for col in cols:
            n = len(col)
            obs = [v for v in col if v is not None and -_INF < v < _INF]
            med = _median(obs) if obs else 0.0
            if len(obs) != n:
                col = [v if (v is not None and -_INF < v < _INF) else med for v in col]
            meds.append(float(med))
            if self.scale:
                mu = math.fsum(col) / n
                d = list(map(sub, col, repeat(mu, n)))
                sd = math.sqrt(sum(map(mul, d, d)) / n)
                sd = sd if sd > _STD_FLOOR else _STD_FLOOR
                means.append(mu)
                stds.append(sd)
                col = self._clip([a * (1.0 / sd) for a in d])
            out.append(col)
        self.medians_ = meds
        self.means_ = means if self.scale else None
        self.stds_ = stds if self.scale else None
        self.n_features_ = len(cols)
        return out

    def _clip(self, z):
        c = self.clip
        if c is not None and z and (max(z) > c or min(z) < -c):
            lo = -c
            return [lo if a < lo else (c if a > c else a) for a in z]
        return z

    def transform_columns(self, X):
        if self.medians_ is None:
            raise RuntimeError("Preprocessor is not fitted")
        cols = _columns(X, self.n_features_)
        out = []
        for j, col in enumerate(cols):
            med = self.medians_[j]
            if self.scale:
                mu = self.means_[j]
                inv = 1.0 / self.stds_[j]
                z = [((v if (v is not None and -_INF < v < _INF) else med) - mu) * inv for v in col]
                out.append(self._clip(z))
            else:
                out.append([v if (v is not None and -_INF < v < _INF) else med for v in col])
        return out

    def transform(self, X):
        return [list(r) for r in zip(*self.transform_columns(X))]

    def transform_row(self, x):
        return [c[0] for c in self.transform_columns([list(x)])]

    def get_state(self):
        return {"scale": self.scale, "clip": self.clip, "medians": self.medians_,
                "means": self.means_, "stds": self.stds_, "n_features": self.n_features_}

    @classmethod
    def from_state(cls, s):
        pre = cls(scale=s["scale"], clip=s["clip"])
        pre.medians_ = [float(v) for v in s["medians"]]
        pre.means_ = None if s["means"] is None else [float(v) for v in s["means"]]
        pre.stds_ = None if s["stds"] is None else [float(v) for v in s["stds"]]
        pre.n_features_ = int(s["n_features"])
        return pre


# ----------------------------------------------------------------------------------------------
# base class
# ----------------------------------------------------------------------------------------------

class _Model:
    name = "model"
    is_classifier = False
    _fitted_attrs = ()

    def __init__(self, **params):
        self.params = params
        self.feature_importances_ = None
        self.base_value_ = None
        self.n_features_ = None

    def __repr__(self):
        args = ", ".join(f"{k}={v!r}" for k, v in self.params.items())
        return f"{type(self).__name__}({args})"

    def _check_fitted(self):
        if self.n_features_ is None:
            raise RuntimeError(f"{type(self).__name__} is not fitted")

    # classifier helpers (used by Logistic and GradientBoosting(loss="logistic"))
    def predict_proba(self, X):
        if not self.is_classifier:
            raise AttributeError(f"{type(self).__name__} is not a classifier")
        return [_sigmoid(e) for e in self.decision_function(X)]

    def _state(self):  # pragma: no cover - overridden
        raise NotImplementedError

    def _load(self, s):  # pragma: no cover - overridden
        raise NotImplementedError


# ----------------------------------------------------------------------------------------------
# linear family
# ----------------------------------------------------------------------------------------------

class _Linear(_Model):
    """Shared machinery: standardise, centre (weighted), fit on the Gram matrix, predict."""

    def _design(self, X, y, w):
        X, y, w = _prepare_xy(X, y, w)
        pre = Preprocessor(scale=self.params.get("standardize", True))
        Z = pre.fit_transform_columns(X)
        n = len(y)
        if w is None:
            zbar = [math.fsum(c) / n for c in Z]
            ybar = math.fsum(y) / n
            Zc = [list(map(sub, c, repeat(m, n))) for c, m in zip(Z, zbar)]
            yc = list(map(sub, y, repeat(ybar, n)))
        else:
            W = math.fsum(w)
            zbar = [sum(map(mul, w, c)) / W for c in Z]
            ybar = sum(map(mul, w, y)) / W
            sw = [math.sqrt(v * n / W) for v in w]          # weights normalised to mean 1
            Zc = [list(map(mul, map(sub, c, repeat(m, n)), sw)) for c, m in zip(Z, zbar)]
            yc = list(map(mul, map(sub, y, repeat(ybar, n)), sw))
        self.pre_ = pre
        self.zbar_ = zbar
        self.ybar_ = ybar
        self.n_features_ = len(Z)
        return Zc, yc, n

    def _set_coef(self, coef, ybar_or_margin, gdiag):
        self.coef_ = [float(c) for c in coef]
        self.base_value_ = float(ybar_or_margin)
        self.intercept_ = self.base_value_ - math.fsum(c * m for c, m in zip(self.coef_, self.zbar_))
        self.feature_importances_ = _normalise(
            [abs(c) * math.sqrt(max(d, 0.0)) for c, d in zip(self.coef_, gdiag)])

    def _linear(self, X):
        self._check_fitted()
        Z = self.pre_.transform_columns(X)
        n = len(Z[0]) if Z else len(list(X))
        out = [self.intercept_] * n
        for zj, c in zip(Z, self.coef_):
            if c:
                out = list(map(add, out, map(mul, zj, repeat(c, n))))
        return out

    def predict(self, X):
        return self._linear(X)

    def contributions(self, x):
        self._check_fitted()
        z = self.pre_.transform_row(x)
        return [c * (a - m) for c, a, m in zip(self.coef_, z, self.zbar_)]

    def _state(self):
        return {"pre": self.pre_.get_state(), "coef": self.coef_, "zbar": self.zbar_,
                "intercept": self.intercept_, "base": self.base_value_,
                "importances": self.feature_importances_, "extra": self._extra_state()}

    def _extra_state(self):
        return {}

    def _load(self, s):
        self.pre_ = Preprocessor.from_state(s["pre"])
        self.coef_ = [float(v) for v in s["coef"]]
        self.zbar_ = [float(v) for v in s["zbar"]]
        self.intercept_ = float(s["intercept"])
        self.base_value_ = float(s["base"])
        self.feature_importances_ = [float(v) for v in s["importances"]]
        self.n_features_ = len(self.coef_)
        for k, v in s.get("extra", {}).items():
            setattr(self, k, v)


class OLS(_Linear):
    """Least squares on standardised features; normal equations by Cholesky with a 1e-8 ridge."""

    name = "ols"

    def __init__(self, standardize=True, ridge=1e-8):
        super().__init__(standardize=standardize, ridge=ridge)

    def _penalty(self, diag):
        # a tiny ridge, relative to the average feature variance, keeps the Cholesky stable
        return float(self.params["ridge"]) * max(1.0, math.fsum(diag) / max(1, len(diag)))

    def fit(self, X, y, sample_weight=None):
        Zc, yc, n = self._design(X, y, sample_weight)
        G = _gram(Zc, n)
        c = [sum(map(mul, z, yc)) / n for z in Zc]
        diag = [G[j][j] for j in range(len(G))]
        lam = self._penalty(diag)
        A = [row[:] for row in G]
        for j in range(len(A)):
            A[j][j] += lam
        coef = _cholesky_solve(A, c) if A else []
        self._set_coef(coef, self.ybar_, diag)
        return self


class Ridge(OLS):
    """Closed-form ridge: (Z'WZ/n + alpha I) b = Z'Wy/n on standardised, centred data."""

    name = "ridge"

    def __init__(self, alpha=1.0, standardize=True):
        _Model.__init__(self, alpha=alpha, standardize=standardize)

    def _penalty(self, diag):
        return float(self.params["alpha"])


class ElasticNet(OLS):
    """Coordinate descent with covariance updates (each sweep is O(p^2), independent of n)."""

    name = "elastic_net"

    def __init__(self, alpha=0.01, l1_ratio=0.5, max_iter=200, tol=1e-5, standardize=True,
                 relative_alpha=False):
        _Model.__init__(self, alpha=alpha, l1_ratio=l1_ratio, max_iter=max_iter, tol=tol,
                        standardize=standardize, relative_alpha=relative_alpha)

    def fit(self, X, y, sample_weight=None):
        Zc, yc, n = self._design(X, y, sample_weight)
        p = len(Zc)
        G = _gram(Zc, n)
        c = [sum(map(mul, z, yc)) / n for z in Zc]
        ysd = math.sqrt(sum(map(mul, yc, yc)) / n)
        alpha = float(self.params["alpha"])
        if self.params["relative_alpha"]:
            alpha *= ysd
        r = self._l1_ratio()
        l1 = alpha * r
        l2 = alpha * (1.0 - r)
        tol = float(self.params["tol"]) * max(ysd, 1e-300)
        b = [0.0] * p
        Gb = [0.0] * p
        it = 0
        for it in range(1, int(self.params["max_iter"]) + 1):
            maxd = 0.0
            for j in range(p):
                Gj = G[j]
                gjj = Gj[j]
                if gjj <= 0.0:
                    continue
                bj = b[j]
                rho = c[j] - Gb[j] + gjj * bj
                if rho > l1:
                    new = (rho - l1) / (gjj + l2)
                elif rho < -l1:
                    new = (rho + l1) / (gjj + l2)
                else:
                    new = 0.0
                d = new - bj
                if d:
                    b[j] = new
                    Gb = list(map(add, Gb, map(mul, Gj, repeat(d, p))))
                    ad = d if d > 0 else -d
                    if ad > maxd:
                        maxd = ad
            if maxd <= tol:
                break
        self.n_iter_ = it
        self._set_coef(b, self.ybar_, [G[j][j] for j in range(p)])
        return self

    def _l1_ratio(self):
        return float(self.params["l1_ratio"])

    def _extra_state(self):
        return {"n_iter_": getattr(self, "n_iter_", 0)}


class Lasso(ElasticNet):
    """L1-penalised least squares (ElasticNet with l1_ratio = 1)."""

    name = "lasso"

    def __init__(self, alpha=0.01, max_iter=200, tol=1e-5, standardize=True, relative_alpha=False):
        _Model.__init__(self, alpha=alpha, max_iter=max_iter, tol=tol, standardize=standardize,
                        relative_alpha=relative_alpha)

    def _l1_ratio(self):
        return 1.0


class Logistic(_Linear):
    """L2-regularised logistic regression fitted by Newton's method (IRLS) with step halving.

    Objective: 0.5 * |b|^2 + C * sum_i w_i * logloss_i (intercept not penalised).
    ``predict`` -> 0/1 labels, ``predict_proba`` -> P(y=1), ``decision_function`` -> log-odds.
    """

    name = "logistic"
    is_classifier = True

    def __init__(self, C=1.0, max_iter=100, tol=1e-6, standardize=True):
        super().__init__(C=C, max_iter=max_iter, tol=tol, standardize=standardize)

    def fit(self, X, y, sample_weight=None):
        X, y, w = _prepare_xy(X, y, sample_weight)
        y = [1.0 if v > 1.0 else (0.0 if v < 0.0 else v) for v in y]
        pre = Preprocessor(scale=self.params["standardize"])
        Z = pre.fit_transform_columns(X)
        n = len(y)
        p = len(Z)
        wn = w if w is not None else [1.0] * n
        W = math.fsum(wn)
        zbar = [sum(map(mul, wn, c)) / W for c in Z]
        Zc = [list(map(sub, c, repeat(m, n))) for c, m in zip(Z, zbar)]
        self.pre_ = pre
        self.zbar_ = zbar
        self.n_features_ = p
        invC = 1.0 / float(self.params["C"])
        ybar = min(max(sum(map(mul, wn, y)) / W, 1e-6), 1 - 1e-6)
        b0 = math.log(ybar / (1 - ybar))
        beta = [0.0] * p

        def margins(b0_, beta_):
            eta = [b0_] * n
            for zj, bj in zip(Zc, beta_):
                if bj:
                    eta = list(map(add, eta, map(mul, zj, repeat(bj, n))))
            return eta

        def objective(eta, beta_):
            ll = 0.0
            for e, yi, wi in zip(eta, y, wn):
                # log(1 + exp(e)) - y e, computed stably
                ll += wi * ((e if e > 0 else 0.0) + math.log1p(math.exp(-abs(e))) - yi * e)
            return 0.5 * sum(map(mul, beta_, beta_)) + ll / invC

        eta = margins(b0, beta)
        obj = objective(eta, beta)
        it = 0
        tol = float(self.params["tol"])
        for it in range(1, int(self.params["max_iter"]) + 1):
            s = []
            r = []
            for e, yi, wi in zip(eta, y, wn):
                pr = _sigmoid(e)
                s.append(wi * max(pr * (1.0 - pr), 1e-12))
                r.append(wi * (pr - yi))
            # gradient and Hessian of obj/C (i.e. loss + 0.5*|b|^2/C), intercept first
            grad = [math.fsum(r)] + [sum(map(mul, r, zj)) + invC * bj for zj, bj in zip(Zc, beta)]
            sZ = [list(map(mul, s, zj)) for zj in Zc]
            H = [[0.0] * (p + 1) for _ in range(p + 1)]
            H[0][0] = math.fsum(s)
            for j in range(p):
                v = math.fsum(sZ[j])
                H[0][j + 1] = v
                H[j + 1][0] = v
                sj = sZ[j]
                Hj = H[j + 1]
                for k in range(j, p):
                    v = sum(map(mul, sj, Zc[k]))
                    Hj[k + 1] = v
                    H[k + 1][j + 1] = v
                Hj[j + 1] += invC
            delta = _cholesky_solve(H, grad)
            step = 1.0
            for _ in range(30):
                nb0 = b0 - step * delta[0]
                nbeta = [bj - step * d for bj, d in zip(beta, delta[1:])]
                neta = margins(nb0, nbeta)
                nobj = objective(neta, nbeta)
                if nobj <= obj + 1e-12 * abs(obj):
                    break
                step *= 0.5
            change = step * max(abs(d) for d in delta)
            b0, beta, eta, obj = nb0, nbeta, neta, nobj
            if change < tol:
                break
        self.n_iter_ = it
        gdiag = [sum(map(mul, wn, map(mul, zj, zj))) / W for zj in Zc]
        # margin = b0 + beta . (z - zbar)  ->  base_value_ is b0 (log-odds at the mean row)
        self._set_coef(beta, b0, gdiag)
        return self

    def decision_function(self, X):
        return self._linear(X)

    def predict(self, X):
        return [1.0 if e > 0 else 0.0 for e in self._linear(X)]

    def _extra_state(self):
        return {"n_iter_": getattr(self, "n_iter_", 0)}


# ----------------------------------------------------------------------------------------------
# histogram trees
# ----------------------------------------------------------------------------------------------

class _Binned:
    """Features binned once into <= n_bins quantile bins (training data only).

    For feature j: ``codes[j][i]`` is the bin of row i; a split "bin <= b" is the raw rule
    ``x <= cuts[j][b]`` (midpoint between the bin's largest value and the next training value).
    ``getters[j][b]`` gathers the rows of bin b (plus the sentinel index n) from a length n+1 array.
    """

    __slots__ = ("n", "p", "codes", "getters", "nbins", "cuts")

    def __init__(self, cols, n_bins):
        self.p = len(cols)
        self.n = n = len(cols[0]) if cols else 0
        nb = max(2, int(n_bins))
        self.codes, self.getters, self.nbins, self.cuts = [], [], [], []
        for col in cols:
            s = sorted(col)
            vmax = s[-1]
            cand = {s[(k * n) // nb] for k in range(1, nb)}
            edges = sorted(v for v in cand if v < vmax)
            cuts = []
            for e in edges:
                nxt = s[bisect_right(s, e)]
                mid = e + (nxt - e) * 0.5
                if not (e <= mid < nxt):
                    mid = e
                cuts.append(float(mid))
            codes = [bisect_left(edges, v) for v in col]
            groups = [[] for _ in range(len(edges) + 1)]
            for i, c in enumerate(codes):
                groups[c].append(i)
            self.codes.append(codes)
            self.getters.append([itemgetter(*g, n) for g in groups])
            self.nbins.append(len(groups))
            self.cuts.append(cuts)


def _bernoulli_rows(rows, frac, rng):
    """Row subsample: each row kept independently with probability frac (at least 2 rows)."""
    r = rng.random
    out = [i for i in rows if r() < frac]
    return out if len(out) >= 2 else rows


def _resolve_max_features(mf, p):
    if mf is None:
        return p
    if isinstance(mf, str):
        if mf == "sqrt":
            return max(1, int(math.sqrt(p)))
        if mf == "log2":
            return max(1, int(math.log2(p))) if p > 1 else 1
        raise ValueError(f"unknown max_features {mf!r}")
    if isinstance(mf, float) and mf <= 1.0:
        return max(1, int(mf * p))
    return max(1, min(p, int(mf)))


class _Grower:
    """Grows one second-order histogram tree.

    Node value = -G / (H + lambda); split score G_L^2/(H_L+l) + G_R^2/(H_R+l); gain is
    0.5 * (score - G^2/(H+l)) - gamma.  With g = -w*y, h = w, lambda = 0 this is exactly a
    weighted least-squares regression tree (node value = weighted mean).
    Tree tuple: (feature, bin, threshold, left, right, value, gain) lists; feature -1 = leaf.
    """

    SMALL_NODE = 0.2   # nodes with fewer than this fraction of rows use the row loop

    def __init__(self, bn, max_depth, min_leaf, lam, gamma, min_child_weight, max_features, rng):
        self.bn = bn
        self.max_depth = int(max_depth)
        self.min_leaf = max(1, int(min_leaf))
        self.lam = float(lam)
        self.gamma = float(gamma)
        self.mcw = max(float(min_child_weight), 1e-12)
        self.k = _resolve_max_features(max_features, bn.p)
        self.rng = rng
        self.all_feats = list(range(bn.p))
        self.importance = [0.0] * bn.p

    def _choose(self):
        if self.k >= self.bn.p:
            return self.all_feats
        return sorted(self.rng.sample(self.all_feats, self.k))

    def _hist(self, rows, feats, g, h):
        bn = self.bn
        n = bn.n
        out = {}
        if len(rows) >= self.SMALL_NODE * n:
            m = [0] * (n + 1)
            for i in rows:
                m[i] = 1
            gm = list(map(mul, g, m))
            hm = None if h is None else list(map(mul, h, m))
            getters = bn.getters
            for j in feats:
                gets = getters[j]
                G = [sum(gt(gm)) for gt in gets]
                C = [sum(gt(m)) for gt in gets]
                H = C if hm is None else [sum(gt(hm)) for gt in gets]
                out[j] = (G, H, C)
        else:
            gs = _take(g, rows)
            hs = None if h is None else _take(h, rows)
            codes = bn.codes
            nbins = bn.nbins
            for j in feats:
                nb = nbins[j]
                G = [0.0] * nb
                C = [0] * nb
                cs = _take(codes[j], rows)
                if hs is None:
                    for b, v in zip(cs, gs):
                        G[b] += v
                        C[b] += 1
                    H = C
                else:
                    H = [0.0] * nb
                    for b, v, hv in zip(cs, gs, hs):
                        G[b] += v
                        H[b] += hv
                        C[b] += 1
                out[j] = (G, H, C)
        return out

    def _best(self, hist, Gt, Ht, Ct):
        lam = self.lam
        ml = self.min_leaf
        mcw = self.mcw
        parent = Gt * Gt / (Ht + lam)
        best = parent + 2.0 * self.gamma + 1e-12 * abs(parent) + 1e-300
        found = None
        for j in sorted(hist):
            G, H, C = hist[j]
            GL = 0.0
            HL = 0.0
            CL = 0
            for b in range(len(G) - 1):
                c = C[b]
                GL += G[b]
                HL += H[b]
                CL += c
                if CL < ml or not c:
                    continue
                if Ct - CL < ml:
                    break
                HR = Ht - HL
                if HL < mcw or HR < mcw:
                    continue
                GR = Gt - GL
                s = GL * GL / (HL + lam) + GR * GR / (HR + lam)
                if s > best:
                    best = s
                    found = (j, b, GL, HL, CL)
        if found is None:
            return None
        return (0.5 * (best - parent) - self.gamma,) + found

    def _expandable(self, depth, C, H):
        return depth < self.max_depth and C >= 2 * self.min_leaf and H >= 2 * self.mcw

    def grow(self, rows, g, h):
        bn = self.bn
        lam = self.lam
        F, B, T, L, R, V, GN = [], [], [], [], [], [], []

        def new(G, H):
            F.append(-1)
            B.append(0)
            T.append(0.0)
            L.append(-1)
            R.append(-1)
            V.append(-G / (H + lam) if H + lam > 0 else 0.0)
            GN.append(0.0)
            return len(F) - 1

        Gt = math.fsum(_take(g, rows))
        Ht = float(len(rows)) if h is None else math.fsum(_take(h, rows))
        Ct = len(rows)
        root = new(Gt, Ht)
        stack = []
        if self._expandable(0, Ct, Ht):
            stack.append((root, rows, 0, self._hist(rows, self._choose(), g, h), Gt, Ht, Ct))
        imp = self.importance
        while stack:
            nd, nrows, depth, hist, G, H, C = stack.pop()
            best = self._best(hist, G, H, C)
            if best is None:
                continue
            gain, j, b, GL, HL, CL = best
            sel = list(map(b.__ge__, _take(bn.codes[j], nrows)))
            lrows = list(compress(nrows, sel))
            rrows = list(compress(nrows, map(not_, sel)))
            GR, HR, CR = G - GL, H - HL, C - CL
            lnd = new(GL, HL)
            rnd = new(GR, HR)
            F[nd] = j
            B[nd] = b
            T[nd] = bn.cuts[j][b]
            L[nd] = lnd
            R[nd] = rnd
            GN[nd] = gain
            imp[j] += gain
            d1 = depth + 1
            le = self._expandable(d1, CL, HL)
            re_ = self._expandable(d1, CR, HR)
            if le and re_:
                fl, fr = self._choose(), self._choose()
                if len(lrows) <= len(rrows):
                    srows, brows, fs, fb = lrows, rrows, fl, fr
                else:
                    srows, brows, fs, fb = rrows, lrows, fr, fl
                via_sub = [k for k in fb if k in hist]
                need = sorted(set(fs).union(via_sub))
                hs = self._hist(srows, need, g, h)
                hb = {}
                direct = [k for k in fb if k not in hist]
                if direct:
                    hb.update(self._hist(brows, direct, g, h))
                for k in via_sub:
                    PG, PH, PC = hist[k]
                    SG, SH, SC = hs[k]
                    Cd = list(map(sub, PC, SC))
                    Hd = Cd if PH is PC else list(map(sub, PH, SH))
                    hb[k] = (list(map(sub, PG, SG)), Hd, Cd)
                if len(need) != len(fs):
                    hs = {k: hs[k] for k in fs}
                if srows is lrows:
                    hl, hr = hs, hb
                else:
                    hl, hr = hb, hs
                stack.append((rnd, rrows, d1, hr, GR, HR, CR))
                stack.append((lnd, lrows, d1, hl, GL, HL, CL))
            elif le:
                stack.append((lnd, lrows, d1, self._hist(lrows, self._choose(), g, h), GL, HL, CL))
            elif re_:
                stack.append((rnd, rrows, d1, self._hist(rrows, self._choose(), g, h), GR, HR, CR))
        return (F, B, T, L, R, V, GN)


def _apply_codes(tree, codes, idx, out, scale):
    """out[i] += scale * leaf_value(i) for rows idx, using binned codes (C-level partitioning)."""
    f, bins, _, left, right, val = tree[:6]
    stack = [(0, idx)]
    while stack:
        nd, rows = stack.pop()
        if not rows:
            continue
        fj = f[nd]
        if fj < 0:
            dv = val[nd] * scale
            for i in rows:
                out[i] += dv
            continue
        sel = list(map(bins[nd].__ge__, _take(codes[fj], rows)))
        stack.append((left[nd], list(compress(rows, sel))))
        stack.append((right[nd], list(compress(rows, map(not_, sel)))))


def _tree_expr(tree, scale):
    f, _, thr, left, right, val = tree[:6]
    order = []
    stack = [0]
    while stack:
        nd = stack.pop()
        order.append(nd)
        if f[nd] >= 0:
            stack.append(left[nd])
            stack.append(right[nd])
    ex = {}
    for nd in reversed(order):
        if f[nd] < 0:
            ex[nd] = repr(float(val[nd] * scale))
        else:
            ex[nd] = (f"({ex.pop(left[nd])} if x{int(f[nd])} <= {float(thr[nd])!r} "
                      f"else {ex.pop(right[nd])})")
    return ex[0]


def _tree_depth(tree):
    f, left, right = tree[0], tree[3], tree[4]
    best = 0
    stack = [(0, 0)]
    while stack:
        nd, d = stack.pop()
        if f[nd] >= 0:
            stack.append((left[nd], d + 1))
            stack.append((right[nd], d + 1))
        elif d > best:
            best = d
    return best


def _compile_trees(trees, scale, base, medians):
    """Compile an ensemble into one straight-line Python function X -> list of outputs.

    Only numbers generated here (int feature indices, repr of finite floats) enter the source.
    Returns None when compilation is not appropriate (non-finite values or very deep trees).
    """
    for t in trees:
        if any(not _finite(v) for v in t[5]) or any(not _finite(v) for v in t[2]):
            return None
        if _tree_depth(t) > 40:
            return None
    if not _finite(base) or not _finite(scale):
        return None
    used = sorted({int(j) for t in trees for j in t[0] if j >= 0})
    src = ["def _predict(X):", " out = []", " ap = out.append", " for x in X:"]
    for j in used:
        src.append(f"  x{j} = x[{j}]")
        src.append(f"  if x{j} is None or not (-_INF < x{j} < _INF): x{j} = {float(medians[j])!r}")
    src.append(f"  s = {float(base)!r}")
    for t in trees:
        if t[0][0] < 0:
            src.append(f"  s += {float(t[5][0] * scale)!r}")
        else:
            src.append(f"  s += {_tree_expr(t, scale)}")
    src.append("  ap(s)")
    src.append(" return out")
    ns = {"_INF": _INF}
    exec(compile("\n".join(src), "<finsim2-trees>", "exec"), ns)  # noqa: S102 - generated numerics only
    return ns["_predict"]


class _TreeModel(_Model):
    """Shared prediction / attribution / state for DecisionTree, RandomForest, GradientBoosting."""

    def _bin(self, X, y, w):
        X, y, w = _prepare_xy(X, y, w)
        pre = Preprocessor(scale=False, clip=None)
        cols = pre.fit_transform_columns(X)
        self.pre_ = pre
        self.n_features_ = len(cols)
        if not cols:
            raise ValueError("X has no features")
        return _Binned(cols, self.params["n_bins"]), y, w

    def _finish(self, trees, scale, base_score):
        self.trees_ = [tuple(t) for t in trees]
        self.tree_scale_ = float(scale)
        self.base_score_ = float(base_score)
        self.base_value_ = self.base_score_ + self.tree_scale_ * math.fsum(t[5][0] for t in self.trees_)
        imp = [0.0] * self.n_features_
        for t in self.trees_:
            for j, gn in zip(t[0], t[6]):
                if j >= 0:
                    imp[j] += gn
        self.feature_importances_ = _normalise(imp)
        self._fn = None

    def _raw(self, X):
        self._check_fitted()
        X = _check_rows(X, self.n_features_)
        fn = getattr(self, "_fn", None)
        if fn is None:
            fn = _compile_trees(self.trees_, self.tree_scale_, self.base_score_, self.pre_.medians_)
            self._fn = fn if fn is not None else self._interp
            fn = self._fn
        return fn(X)

    def _interp(self, X):
        med = self.pre_.medians_
        scale = self.tree_scale_
        out = []
        for x in X:
            row = [v if (v is not None and -_INF < v < _INF) else m for v, m in zip(x, med)]
            s = self.base_score_
            for f, _, thr, left, right, val, _g in self.trees_:
                nd = 0
                while f[nd] >= 0:
                    nd = left[nd] if row[f[nd]] <= thr[nd] else right[nd]
                s += val[nd] * scale
            out.append(s)
        return out

    def contributions(self, x):
        """Saabas path attribution: each split's feature is credited with the change in node value."""
        self._check_fitted()
        row = self.pre_.transform_row(x)
        contrib = [0.0] * self.n_features_
        scale = self.tree_scale_
        for f, _, thr, left, right, val, _g in self.trees_:
            nd = 0
            while f[nd] >= 0:
                nxt = left[nd] if row[f[nd]] <= thr[nd] else right[nd]
                contrib[f[nd]] += (val[nxt] - val[nd]) * scale
                nd = nxt
        return contrib

    def predict(self, X):
        return self._raw(X)

    def _state(self):
        return {"pre": self.pre_.get_state(), "trees": [[list(a) for a in t] for t in self.trees_],
                "scale": self.tree_scale_, "base_score": self.base_score_,
                "extra": self._extra_state()}

    def _extra_state(self):
        return {}

    def _load(self, s):
        self.pre_ = Preprocessor.from_state(s["pre"])
        self.n_features_ = self.pre_.n_features_
        trees = []
        for t in s["trees"]:
            f, b, thr, left, right, val, gn = t
            trees.append(([int(v) for v in f], [int(v) for v in b], [float(v) for v in thr],
                          [int(v) for v in left], [int(v) for v in right], [float(v) for v in val],
                          [float(v) for v in gn]))
        for k, v in s.get("extra", {}).items():
            setattr(self, k, v)
        self._finish(trees, s["scale"], s["base_score"])


class _ProbaMixin:
    def predict_proba(self, X):
        return [0.0 if v < 0.0 else (1.0 if v > 1.0 else v) for v in self.predict(X)]


class DecisionTree(_ProbaMixin, _TreeModel):
    """Histogram regression tree on squared error (use with 0/1 targets for probabilities)."""

    name = "decision_tree"

    def __init__(self, max_depth=3, min_leaf=20, n_bins=32, max_features=None, seed=0):
        super().__init__(max_depth=max_depth, min_leaf=min_leaf, n_bins=n_bins,
                         max_features=max_features, seed=seed)

    def fit(self, X, y, sample_weight=None):
        bn, y, w = self._bin(X, y, sample_weight)
        g = [-v for v in y] if w is None else [-a * b for a, b in zip(y, w)]
        g.append(0.0)
        h = None if w is None else w + [0.0]
        pr = self.params
        grower = _Grower(bn, pr["max_depth"], pr["min_leaf"], 0.0, 0.0, 0.0, pr["max_features"],
                         random.Random(pr["seed"]))
        tree = grower.grow(list(range(bn.n)), g, h)
        self._finish([tree], 1.0, 0.0)
        return self


class RandomForest(_ProbaMixin, _TreeModel):
    """Bagged histogram trees: row subsampling without replacement + per-node feature sampling.

    Features are binned once for the whole forest.  Prediction = mean of tree predictions.
    """

    name = "random_forest"

    def __init__(self, n_estimators=40, max_depth=4, min_leaf=20, max_features="sqrt",
                 subsample=0.7, n_bins=32, seed=0):
        super().__init__(n_estimators=n_estimators, max_depth=max_depth, min_leaf=min_leaf,
                         max_features=max_features, subsample=subsample, n_bins=n_bins, seed=seed)

    def fit(self, X, y, sample_weight=None):
        bn, y, w = self._bin(X, y, sample_weight)
        pr = self.params
        n = bn.n
        g = [-v for v in y] if w is None else [-a * b for a, b in zip(y, w)]
        g.append(0.0)
        h = None if w is None else w + [0.0]
        rng = random.Random(pr["seed"])
        grower = _Grower(bn, pr["max_depth"], pr["min_leaf"], 0.0, 0.0, 0.0, pr["max_features"], rng)
        frac = float(pr["subsample"])
        all_rows = list(range(n))
        trees = []
        T = max(1, int(pr["n_estimators"]))
        for _ in range(T):
            rows = all_rows if frac >= 1.0 else _bernoulli_rows(all_rows, frac, rng)
            trees.append(grower.grow(rows, g, h))
        self._finish(trees, 1.0 / T, 0.0)
        return self


class GradientBoosting(_TreeModel):
    """XGBoost-style second-order gradient boosting on histogram trees (pure Python).

    loss="squared" (regression) or "logistic" (0/1 classification; ``predict`` -> labels,
    ``predict_proba`` -> P(y=1), ``decision_function`` / ``contributions`` -> log-odds).
    Leaf weight -G/(H+lambda); split gain 0.5*[G_L^2/(H_L+l) + G_R^2/(H_R+l) - G^2/(H+l)] - gamma.
    ``validation_fraction`` > 0 holds out the LAST chronological fraction of the training rows for
    early stopping (``n_iter_no_change`` rounds without improvement); rows are never shuffled.
    """

    name = "gradient_boosting"

    def __init__(self, n_estimators=100, learning_rate=0.05, max_depth=2, min_leaf=20,
                 subsample=0.8, reg_lambda=1.0, gamma=0.0, seed=0, loss="squared",
                 validation_fraction=0.0, n_iter_no_change=10, min_child_weight=1e-3,
                 max_features=None, n_bins=32):
        if loss not in ("squared", "logistic"):
            raise ValueError("loss must be 'squared' or 'logistic'")
        super().__init__(n_estimators=n_estimators, learning_rate=learning_rate,
                         max_depth=max_depth, min_leaf=min_leaf, subsample=subsample,
                         reg_lambda=reg_lambda, gamma=gamma, seed=seed, loss=loss,
                         validation_fraction=validation_fraction,
                         n_iter_no_change=n_iter_no_change, min_child_weight=min_child_weight,
                         max_features=max_features, n_bins=n_bins)

    @property
    def is_classifier(self):
        return self.params["loss"] == "logistic"

    def fit(self, X, y, sample_weight=None):
        bn, y, w = self._bin(X, y, sample_weight)
        pr = self.params
        n = bn.n
        logistic = pr["loss"] == "logistic"
        if logistic:
            y = [1.0 if v > 1.0 else (0.0 if v < 0.0 else v) for v in y]
        vf = float(pr["validation_fraction"] or 0.0)
        n_val = int(n * vf) if vf > 0 else 0
        if vf > 0 and n_val < 1:
            n_val = 1
        if n - n_val < 2:
            n_val = 0
        n_tr = n - n_val
        wt = w[:n_tr] if w is not None else None
        if wt is None:
            ybar = math.fsum(y[:n_tr]) / n_tr
        else:
            ybar = sum(map(mul, wt, y[:n_tr])) / max(math.fsum(wt), 1e-300)
        if logistic:
            pb = min(max(ybar, 1e-6), 1 - 1e-6)
            base = math.log(pb / (1 - pb))
        else:
            base = ybar
        lr = float(pr["learning_rate"])
        rng = random.Random(pr["seed"])
        grower = _Grower(bn, pr["max_depth"], pr["min_leaf"], pr["reg_lambda"], pr["gamma"],
                         pr["min_child_weight"], pr["max_features"], rng)
        F = [base] * n
        train_rows = list(range(n_tr))
        all_rows = list(range(n))
        val_rows = list(range(n_tr, n))
        frac = float(pr["subsample"])
        wv = [1.0] * n if w is None else w
        trees = []
        best_loss, best_iter, since = _INF, 0, 0
        self.validation_scores_ = []
        patience = max(1, int(pr["n_iter_no_change"]))
        for it in range(max(0, int(pr["n_estimators"]))):
            if logistic:
                g, h = [], []
                for Fi, yi in zip(F, y):
                    p = _sigmoid(Fi)
                    g.append(p - yi)
                    h.append(max(p * (1.0 - p), 1e-16))
                if w is not None:
                    g = list(map(mul, g, w))
                    h = list(map(mul, h, w))
                h.append(0.0)
            else:
                g = list(map(sub, F, y))
                if w is not None:
                    g = list(map(mul, g, w))
                    h = w + [0.0]
                else:
                    h = None
            g.append(0.0)
            rows = train_rows if frac >= 1.0 else _bernoulli_rows(train_rows, frac, rng)
            tree = grower.grow(rows, g, h)
            trees.append(tree)
            _apply_codes(tree, bn.codes, all_rows, F, lr)
            if n_val:
                if logistic:
                    ls = 0.0
                    for i in val_rows:
                        e = F[i]
                        ls += wv[i] * ((e if e > 0 else 0.0) + math.log1p(math.exp(-abs(e))) - y[i] * e)
                else:
                    ls = math.fsum(wv[i] * (F[i] - y[i]) ** 2 for i in val_rows)
                ls /= max(math.fsum(wv[i] for i in val_rows), 1e-300)
                self.validation_scores_.append(ls)
                if ls < best_loss - 1e-15 * abs(best_loss if best_loss < _INF else 0.0):
                    best_loss, best_iter, since = ls, it + 1, 0
                else:
                    since += 1
                    if since >= patience:
                        break
        if n_val:
            trees = trees[:best_iter]
        self.n_estimators_ = len(trees)
        self.best_iteration_ = len(trees)
        self._finish(trees, lr, base)
        return self

    def decision_function(self, X):
        return self._raw(X)

    def predict(self, X):
        raw = self._raw(X)
        if self.params["loss"] == "logistic":
            return [1.0 if e > 0 else 0.0 for e in raw]
        return raw

    def predict_proba(self, X):
        if self.params["loss"] != "logistic":
            return [0.0 if v < 0.0 else (1.0 if v > 1.0 else v) for v in self._raw(X)]
        return [_sigmoid(e) for e in self._raw(X)]

    def _extra_state(self):
        return {"n_estimators_": getattr(self, "n_estimators_", len(self.trees_)),
                "best_iteration_": getattr(self, "best_iteration_", len(self.trees_)),
                "validation_scores_": getattr(self, "validation_scores_", [])}


# ----------------------------------------------------------------------------------------------
# unsupervised utilities
# ----------------------------------------------------------------------------------------------

def _jacobi_eigh(A, tol=1e-12, max_sweeps=60):
    """Cyclic Jacobi eigen-decomposition of a symmetric matrix -> (eigenvalues, eigenvector rows)."""
    p = len(A)
    a = [list(map(float, row)) for row in A]
    V = [[1.0 if i == j else 0.0 for j in range(p)] for i in range(p)]  # rows = eigenvectors
    scale = math.fsum(a[i][i] ** 2 for i in range(p)) + 1e-300
    for _ in range(max_sweeps):
        off = math.fsum(a[i][j] ** 2 for i in range(p) for j in range(i + 1, p))
        if off <= tol * tol * scale:
            break
        for i in range(p - 1):
            for j in range(i + 1, p):
                aij = a[i][j]
                if abs(aij) <= 1e-300:
                    continue
                aii, ajj = a[i][i], a[j][j]
                theta = (ajj - aii) / (2.0 * aij)
                t = (1.0 if theta >= 0 else -1.0) / (abs(theta) + math.sqrt(theta * theta + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                ai, aj = a[i], a[j]
                ni = [c * x - s * y for x, y in zip(ai, aj)]
                nj = [s * x + c * y for x, y in zip(ai, aj)]
                ni[i] = aii - t * aij
                nj[j] = ajj + t * aij
                ni[j] = 0.0
                nj[i] = 0.0
                a[i], a[j] = ni, nj
                for k in range(p):
                    if k != i and k != j:
                        ak = a[k]
                        ak[i] = ni[k]
                        ak[j] = nj[k]
                vi, vj = V[i], V[j]
                V[i] = [c * x - s * y for x, y in zip(vi, vj)]
                V[j] = [s * x + c * y for x, y in zip(vi, vj)]
    return [a[i][i] for i in range(p)], V


class PCA(_Model):
    """Principal components of the (median-imputed, standardised, clipped) training data."""

    name = "pca"

    def __init__(self, n_components=None, standardize=True):
        super().__init__(n_components=n_components, standardize=standardize)

    def fit(self, X, y=None, sample_weight=None):
        pre = Preprocessor(scale=self.params["standardize"])
        Z = pre.fit_transform_columns(X)
        n = len(Z[0])
        mean = [math.fsum(c) / n for c in Z]
        Zc = [list(map(sub, c, repeat(m, n))) for c, m in zip(Z, mean)]
        C = _gram(Zc, max(n - 1, 1))
        vals, vecs = _jacobi_eigh(C)
        order = sorted(range(len(vals)), key=lambda i: -vals[i])
        total = math.fsum(max(v, 0.0) for v in vals)
        k = len(vals) if self.params["n_components"] is None else min(int(self.params["n_components"]), len(vals))
        comps = []
        for i in order[:k]:
            v = vecs[i]
            big = max(range(len(v)), key=lambda q: abs(v[q]))
            if v[big] < 0:
                v = [-a for a in v]
            comps.append(v)
        self.pre_ = pre
        self.mean_ = mean
        self.components_ = comps
        self.explained_variance_ = [max(vals[i], 0.0) for i in order[:k]]
        self.explained_variance_ratio_ = [v / total if total > 0 else 0.0 for v in self.explained_variance_]
        self.n_features_ = len(Z)
        return self

    def transform(self, X):
        self._check_fitted()
        Z = self.pre_.transform_columns(X)
        n = len(Z[0]) if Z else 0
        Zc = [list(map(sub, c, repeat(m, n))) for c, m in zip(Z, self.mean_)]
        scores = []
        for comp in self.components_:
            acc = [0.0] * n
            for zj, cj in zip(Zc, comp):
                if cj:
                    acc = list(map(add, acc, map(mul, zj, repeat(cj, n))))
            scores.append(acc)
        return [list(r) for r in zip(*scores)] if scores else [[] for _ in range(n)]

    def fit_transform(self, X):
        return self.fit(X).transform(X)

    def _state(self):
        return {"pre": self.pre_.get_state(), "mean": self.mean_, "components": self.components_,
                "ev": self.explained_variance_, "evr": self.explained_variance_ratio_}

    def _load(self, s):
        self.pre_ = Preprocessor.from_state(s["pre"])
        self.mean_ = [float(v) for v in s["mean"]]
        self.components_ = [[float(v) for v in c] for c in s["components"]]
        self.explained_variance_ = [float(v) for v in s["ev"]]
        self.explained_variance_ratio_ = [float(v) for v in s["evr"]]
        self.n_features_ = self.pre_.n_features_


class KMeans(_Model):
    """k-means (k-means++ seeding, Lloyd iterations) on median-imputed, standardised data."""

    name = "kmeans"

    def __init__(self, k=8, seed=0, max_iter=100, tol=1e-6, standardize=True):
        super().__init__(k=k, seed=seed, max_iter=max_iter, tol=tol, standardize=standardize)

    def _dists(self, Z, n):
        """Squared distances of every row to every centre (list per centre)."""
        zz = [0.0] * n
        for c in Z:
            zz = list(map(add, zz, map(mul, c, c)))
        out = []
        for cen in self.cluster_centers_:
            acc = zz
            cc = math.fsum(v * v for v in cen)
            for zj, cj in zip(Z, cen):
                if cj:
                    acc = list(map(sub, acc, map(mul, zj, repeat(2.0 * cj, n))))
            out.append([v + cc if v + cc > 0 else 0.0 for v in acc])
        return out

    @staticmethod
    def _argmin(D, n):
        best = list(D[0])
        lab = [0] * n
        for c in range(1, len(D)):
            Dc = D[c]
            for i in range(n):
                if Dc[i] < best[i]:
                    best[i] = Dc[i]
                    lab[i] = c
        return lab, best

    def fit(self, X, y=None, sample_weight=None):
        pre = Preprocessor(scale=self.params["standardize"])
        Z = pre.fit_transform_columns(X)
        self.pre_ = pre
        self.n_features_ = len(Z)
        n = len(Z[0])
        k = max(1, min(int(self.params["k"]), n))
        rng = random.Random(self.params["seed"])
        rows = list(zip(*Z))
        # k-means++ seeding
        centers = [list(rows[rng.randrange(n)])]
        self.cluster_centers_ = centers
        while len(centers) < k:
            D = self._dists(Z, n)
            _, dmin = self._argmin(D, n)
            tot = math.fsum(dmin)
            if tot <= 0:
                centers.append(list(rows[rng.randrange(n)]))
                continue
            r = rng.random() * tot
            acc = 0.0
            pick = n - 1
            for i, d in enumerate(dmin):
                acc += d
                if acc >= r:
                    pick = i
                    break
            centers.append(list(rows[pick]))
        lab = None
        it = 0
        for it in range(1, int(self.params["max_iter"]) + 1):
            D = self._dists(Z, n)
            new_lab, dmin = self._argmin(D, n)
            groups = [[] for _ in range(k)]
            for i, c in enumerate(new_lab):
                groups[c].append(i)
            shift = 0.0
            for c in range(k):
                if groups[c]:
                    idx = groups[c]
                    m = len(idx)
                    newc = [math.fsum(_take(zj, idx)) / m for zj in Z]
                else:   # empty cluster: re-seed at the worst-fitted row
                    newc = list(rows[max(range(n), key=dmin.__getitem__)])
                shift = max(shift, math.fsum((a - b) ** 2 for a, b in zip(newc, centers[c])))
                centers[c] = newc
            converged = new_lab == lab or shift <= float(self.params["tol"])
            lab = new_lab
            if converged:
                break
        D = self._dists(Z, n)
        self.labels_, dmin = self._argmin(D, n)
        self.inertia_ = math.fsum(dmin)
        self.n_iter_ = it
        return self

    def transform(self, X):
        """Euclidean distance of each row to each centre -> rows of k distances."""
        self._check_fitted()
        Z = self.pre_.transform_columns(X)
        n = len(Z[0]) if Z else 0
        D = self._dists(Z, n)
        return [[math.sqrt(v) for v in r] for r in zip(*D)]

    def predict(self, X):
        self._check_fitted()
        Z = self.pre_.transform_columns(X)
        n = len(Z[0]) if Z else 0
        return self._argmin(self._dists(Z, n), n)[0]

    def _state(self):
        return {"pre": self.pre_.get_state(), "centers": self.cluster_centers_,
                "inertia": self.inertia_, "labels": self.labels_}

    def _load(self, s):
        self.pre_ = Preprocessor.from_state(s["pre"])
        self.cluster_centers_ = [[float(v) for v in c] for c in s["centers"]]
        self.inertia_ = float(s["inertia"])
        self.labels_ = [int(v) for v in s["labels"]]
        self.n_features_ = self.pre_.n_features_


# ----------------------------------------------------------------------------------------------
# registry, factory, persistence
# ----------------------------------------------------------------------------------------------

_REGISTRY = {cls.name: cls for cls in (OLS, Ridge, Lasso, ElasticNet, Logistic, DecisionTree,
                                        RandomForest, GradientBoosting, PCA, KMeans)}

MODEL_NAMES = ["ols", "ridge", "lasso", "elastic_net", "logistic", "random_forest",
               "gradient_boosting"]

MODEL_LABELS = {
    "ols": "Linear regression (OLS)",
    "ridge": "Ridge regression",
    "lasso": "Lasso regression",
    "elastic_net": "Elastic net",
    "logistic": "Logistic regression (L2)",
    "decision_tree": "Decision tree",
    "random_forest": "Random forest",
    "gradient_boosting": "Gradient boosting (XGBoost-style)",
}

# Defaults for noisy financial data: strong shrinkage, shallow trees, large leaves.
_DEFAULTS = {
    "ols": {},
    "ridge": {"alpha": 2.0},
    "lasso": {"alpha": 0.03, "relative_alpha": True},
    "elastic_net": {"alpha": 0.05, "l1_ratio": 0.5, "relative_alpha": True},
    "logistic": {"C": 0.05},
    "decision_tree": {"max_depth": 3, "min_leaf": 50},
    "random_forest": {"n_estimators": 40, "max_depth": 4, "min_leaf": 50, "max_features": "sqrt",
                      "subsample": 0.7},
    "gradient_boosting": {"n_estimators": 100, "learning_rate": 0.05, "max_depth": 2,
                          "min_leaf": 50, "subsample": 0.8, "reg_lambda": 5.0},
}


def make_model(name, **overrides):
    """Model by name with defaults tuned for noisy financial data; keyword overrides win."""
    if name not in _DEFAULTS:
        raise ValueError(f"unknown model {name!r}; choose from {sorted(_DEFAULTS)}")
    params = dict(_DEFAULTS[name])
    params.update(overrides)
    return _REGISTRY[name](**params)


def get_state(model):
    """JSON-serialisable snapshot of a fitted model."""
    model._check_fitted()
    return {"format": _STATE_FORMAT, "model": model.name, "params": dict(model.params),
            "fitted": model._state()}


def from_state(state):
    """Rebuild a model saved with ``get_state`` (predictions are identical)."""
    if state.get("format") != _STATE_FORMAT:
        raise ValueError("unrecognised model state format")
    cls = _REGISTRY.get(state["model"])
    if cls is None:
        raise ValueError(f"unknown model {state['model']!r}")
    m = cls(**state["params"])
    m._load(state["fitted"])
    return m


# ----------------------------------------------------------------------------------------------
# statistics helpers
# ----------------------------------------------------------------------------------------------

def rank(values):
    """1-based ranks with ties sharing their average rank."""
    n = len(values)
    order = sorted(range(n), key=values.__getitem__)
    out = [0.0] * n
    i = 0
    while i < n:
        j = i
        v = values[order[i]]
        while j + 1 < n and values[order[j + 1]] == v:
            j += 1
        r = (i + j) * 0.5 + 1.0
        for q in range(i, j + 1):
            out[order[q]] = r
        i = j + 1
    return out


def _pairs(a, b):
    if len(a) != len(b):
        raise ValueError("inputs must have the same length")
    xs, ys = [], []
    for u, v in zip(a, b):
        if _finite(u) and _finite(v):
            xs.append(float(u))
            ys.append(float(v))
    return xs, ys


def _pearson_clean(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx = math.fsum(xs) / n
    my = math.fsum(ys) / n
    dx = [v - mx for v in xs]
    dy = [v - my for v in ys]
    sxx = sum(map(mul, dx, dx))
    syy = sum(map(mul, dy, dy))
    if sxx <= 0 or syy <= 0:
        return None
    r = sum(map(mul, dx, dy)) / math.sqrt(sxx * syy)
    return max(-1.0, min(1.0, r))


def pearson(a, b):
    """Pearson correlation over pairs where both values are present; None if undefined (n < 3)."""
    return _pearson_clean(*_pairs(a, b))


def spearman(a, b):
    """Spearman rank correlation over complete pairs (average ranks for ties); None if undefined."""
    xs, ys = _pairs(a, b)
    if len(xs) < 3:
        return None
    return _pearson_clean(rank(xs), rank(ys))


def permutation_importance(model, X, y, metric, n_repeats=3, seed=0, method="predict"):
    """Mean drop in ``metric(y, prediction)`` (higher is better) when one column is shuffled.

    ``method`` names the prediction method ("predict", "predict_proba" or "decision_function").
    """
    predict = getattr(model, method)
    rows = [list(r) for r in X]
    y = list(y)
    baseline = metric(y, predict(rows))
    rng = random.Random(seed)
    p = len(rows[0]) if rows else 0
    out = []
    for j in range(p):
        orig = [r[j] for r in rows]
        drops = []
        for _ in range(max(1, int(n_repeats))):
            col = orig[:]
            rng.shuffle(col)
            for r, v in zip(rows, col):
                r[j] = v
            drops.append(baseline - metric(y, predict(rows)))
        for r, v in zip(rows, orig):
            r[j] = v
        out.append(math.fsum(drops) / len(drops))
    return out
