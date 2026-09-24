"""Neural networks (equations 84-100). W is a list of rows (one row per output unit)."""
import math
import random as _random

from .ml import sigmoid


# ---------------------------------------------------------------- 84-88 forward pass

def preactivation(W, x, b):
    """Eq 84: pre-activation z = Wx + b."""
    return [sum(w * v for w, v in zip(row, x)) + bi for row, bi in zip(W, b)]


def relu(z):
    """Rectified linear unit max(0, z)."""
    return z if z > 0 else 0.0


def activation(z, kind="relu"):
    """Eq 85: element-wise activation σ(z) — "relu", "tanh", "sigmoid" or "linear"."""
    f = {"relu": relu, "tanh": math.tanh, "sigmoid": sigmoid, "linear": lambda v: v}[kind]
    return [f(v) for v in z] if isinstance(z, (list, tuple)) else f(z)


def activation_derivative(z, kind="relu"):
    """Derivative σ'(z) of an activation, element-wise."""
    def d(v):
        if kind == "relu":
            return 1.0 if v > 0 else 0.0
        if kind == "tanh":
            t = math.tanh(v)
            return 1.0 - t * t
        if kind == "sigmoid":
            s = sigmoid(v)
            return s * (1.0 - s)
        return 1.0
    return [d(v) for v in z] if isinstance(z, (list, tuple)) else d(z)


def two_layer_forward(x, W1, b1, W2, b2, act="tanh"):
    """Eq 86: two-layer network ŷ = W₂ σ(W₁x + b₁) + b₂."""
    return preactivation(W2, activation(preactivation(W1, x, b1), act), b2)


def deep_layer(W, a_prev, b):
    """Eq 87: layer pre-activation z^(l) = W^(l) a^(l−1) + b^(l)."""
    return preactivation(W, a_prev, b)


def layer_activation(z, kind="relu"):
    """Eq 88: layer activation a^(l) = σ(z^(l))."""
    return activation(z, kind)


def mlp_forward(x, layers, act="relu", out_act="linear"):
    """Forward pass through [(W, b), ...]; returns the list of (z, a) per layer."""
    a = list(x)
    cache = []
    for i, (W, b) in enumerate(layers):
        z = deep_layer(W, a, b)
        a = layer_activation(z, out_act if i == len(layers) - 1 else act)
        cache.append((z, a))
    return cache


# ---------------------------------------------------------------- 89-94 back-propagation

def chain_rule(*derivatives):
    """Eq 89: chain rule — the derivative of a composition is the product of the link derivatives."""
    out = 1.0
    for d in derivatives:
        out *= d
    return out


def numeric_derivative(f, x, h=1e-6):
    """Central-difference derivative (f(x + h) − f(x − h)) / 2h, for checking the chain rule."""
    return (f(x + h) - f(x - h)) / (2.0 * h)


def softmax_ce_grad(yhat, y):
    """Eq 90: gradient of softmax cross-entropy with respect to the logits, ∂L/∂z = ŷ − y."""
    return [p - t for p, t in zip(yhat, y)]


def output_error(dL_da, z, kind="linear"):
    """Eq 91: output-layer error δ^(L) = ∇_a L ⊙ σ'(z^(L))."""
    return [g * d for g, d in zip(dL_da, activation_derivative(z, kind))]


def hidden_delta(W_next, delta_next, z, kind="relu"):
    """Eq 92: hidden-layer error δ^(l) = (W^(l+1)ᵀ δ^(l+1)) ⊙ σ'(z^(l))."""
    back = [sum(W_next[i][j] * delta_next[i] for i in range(len(delta_next))) for j in range(len(z))]
    return [g * d for g, d in zip(back, activation_derivative(z, kind))]


def weight_gradient(delta, a_prev):
    """Eq 93: weight gradient ∂L/∂W^(l) = δ^(l) (a^(l−1))ᵀ."""
    return [[d * a for a in a_prev] for d in delta]


def bias_gradient(delta):
    """Eq 94: bias gradient ∂L/∂b^(l) = δ^(l)."""
    return list(delta)


# ---------------------------------------------------------------- 95-100 optimisers (element-wise on flat lists)

def gd_step(theta, grad, lr):
    """Eq 95: gradient descent θ ← θ − η ∇L(θ)."""
    return [t - lr * g for t, g in zip(theta, grad)]


def sgd_step(theta, batch_grads, lr):
    """Eq 96: stochastic gradient step θ ← θ − η · (mean gradient over a sampled mini-batch)."""
    m = len(batch_grads)
    g = [sum(col) / m for col in zip(*batch_grads)]
    return gd_step(theta, g, lr)


def momentum_step(theta, velocity, grad, lr, beta=0.9):
    """Eq 97: momentum v ← βv + ∇L, θ ← θ − ηv. Returns (theta, velocity)."""
    v = [beta * vi + g for vi, g in zip(velocity, grad)]
    return [t - lr * vi for t, vi in zip(theta, v)], v


def adam_first_moment(m, grad, beta1=0.9):
    """Eq 98: Adam first moment m ← β₁m + (1 − β₁)g."""
    return [beta1 * a + (1.0 - beta1) * g for a, g in zip(m, grad)]


def adam_second_moment(v, grad, beta2=0.999):
    """Eq 99: Adam second moment v ← β₂v + (1 − β₂)g²."""
    return [beta2 * a + (1.0 - beta2) * g * g for a, g in zip(v, grad)]


def adam_update(theta, m, v, t, lr=0.001, beta1=0.9, beta2=0.999, eps=1e-8):
    """Eq 100: Adam step θ ← θ − η m̂ / (√v̂ + ε) with bias-corrected m̂ = m/(1 − β₁ᵗ), v̂ = v/(1 − β₂ᵗ)."""
    c1, c2 = 1.0 - beta1 ** t, 1.0 - beta2 ** t
    return [p - lr * (a / c1) / (math.sqrt(b / c2) + eps) for p, a, b in zip(theta, m, v)]


# ---------------------------------------------------------------- tiny trainer

def train_mlp(X, y, hidden=8, task="binary", epochs=2000, lr=0.05, act="tanh", seed=0,
              beta1=0.9, beta2=0.999):
    """Train a one-hidden-layer network with full-batch Adam.

    task "binary" uses a sigmoid output with cross-entropy; "regression" uses a linear output
    with mean squared error. y holds one scalar target per row.
    Returns {"W1", "b1", "W2", "b2", "act", "task", "loss_history"}.
    """
    rng = _random.Random(seed)
    k = len(X[0])
    s1, s2 = 1.0 / math.sqrt(k), 1.0 / math.sqrt(hidden)
    W1 = [[rng.gauss(0.0, s1) for _ in range(k)] for _ in range(hidden)]
    b1 = [0.0] * hidden
    W2 = [rng.gauss(0.0, s2) for _ in range(hidden)]
    b2 = 0.0
    n_par = hidden * k + hidden + hidden + 1
    m, v = [0.0] * n_par, [0.0] * n_par
    n = len(X)
    history = []
    for step in range(1, epochs + 1):
        gW1 = [[0.0] * k for _ in range(hidden)]
        gb1 = [0.0] * hidden
        gW2 = [0.0] * hidden
        gb2 = 0.0
        loss = 0.0
        for x, t in zip(X, y):
            z1 = preactivation(W1, x, b1)
            a1 = activation(z1, act)
            out = sum(w * a for w, a in zip(W2, a1)) + b2
            if task == "binary":
                p = sigmoid(out)
                q = min(max(p, 1e-12), 1 - 1e-12)
                loss -= t * math.log(q) + (1 - t) * math.log(1 - q)
                d_out = p - t
            else:
                loss += (out - t) ** 2
                d_out = 2.0 * (out - t)
            gb2 += d_out
            d1 = hidden_delta([W2], [d_out], z1, act)
            for j in range(hidden):
                gW2[j] += d_out * a1[j]
                gb1[j] += d1[j]
                row = gW1[j]
                dj = d1[j]
                for i in range(k):
                    row[i] += dj * x[i]
        history.append(loss / n)
        grad = [g / n for row in gW1 for g in row] + [g / n for g in gb1] + [g / n for g in gW2] + [gb2 / n]
        theta = [w for row in W1 for w in row] + b1 + W2 + [b2]
        m = adam_first_moment(m, grad, beta1)
        v = adam_second_moment(v, grad, beta2)
        theta = adam_update(theta, m, v, step, lr, beta1, beta2)
        W1 = [theta[j * k:(j + 1) * k] for j in range(hidden)]
        off = hidden * k
        b1 = theta[off:off + hidden]
        W2 = theta[off + hidden:off + 2 * hidden]
        b2 = theta[-1]
    return {"W1": W1, "b1": b1, "W2": W2, "b2": b2, "act": act, "task": task, "loss_history": history}


def mlp_predict(model, x):
    """Prediction of a network returned by train_mlp (probability for "binary")."""
    a1 = activation(preactivation(model["W1"], x, model["b1"]), model["act"])
    out = sum(w * a for w, a in zip(model["W2"], a1)) + model["b2"]
    return sigmoid(out) if model["task"] == "binary" else out
